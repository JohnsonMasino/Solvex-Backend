from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import BusinessError
from core.openapi import success_response_doc
from core.pagination import StandardPagination
from core.permissions import HasCronSecret, IsKYCApproved
from core.responses import success_response
from wallet import services as wallet_services

from . import docs, services
from .models import Loan, LoanStatus
from .serializers import (
    LoanCreateSerializer, LoanDetailSerializer, LoanFilterSerializer, LoanRepaymentSerializer,
    LoanRepaySerializer, LoanSerializer,
)

TAG = "Loan"
ID_PARAM = OpenApiParameter("id", str, OpenApiParameter.PATH, description="Loan UUID.")
PAGE_PARAMS = [
    OpenApiParameter("page", int, description="Page number (default 1)."),
    OpenApiParameter("page_size", int, description="Items per page (default 20, max 100)."),
]


def _loan_payload(loan, user, wallet=None):
    data = LoanSerializer(loan).data
    data["account"] = services.account_snapshot(user, wallet)
    return data


# --------------------------------------------------------------------------- #
# Config / eligibility
# --------------------------------------------------------------------------- #
class LoanConfigView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Loan configuration (durations, limits, rates)",
        description=(
            "Returns the available loan **durations**, the min / max loan size, the maximum wallet balance that "
            "still qualifies for a loan, and the daily interest rates (`daily_interest_percent` within the term, "
            "`overdue_daily_interest_percent` after the due date). Build the duration picker from `durations`. "
            "`requires_admin_approval` is always `true`: a request must be approved by an admin."
            + docs.LOAN_GUIDE
        ),
        responses={
            200: success_response_doc("LoanConfigSuccess", "Loan configuration.", example=docs.CONFIG_EXAMPLE),
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        return success_response(services.get_config())


class LoanEligibilityView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Can I request a loan?",
        description=(
            "Tells the caller whether they can request a loan right now. `reasons` lists why not: "
            "`loan_request_pending` (a request is waiting for admin approval), `active_loan_exists` (repay the "
            "current loan first) and/or `balance_too_high` (wallet balance must be below "
            "`max_eligible_balance_usd`). `open_loan_id` / `open_loan_status` point to the pending or active loan. "
            "KYC is checked when the request is created."
        ),
        responses={
            200: success_response_doc(
                "LoanEligibilitySuccess", "Eligibility result.", example=docs.ELIGIBLE_EXAMPLE,
            ),
            401: docs.UNAUTH,
        },
        examples=[OpenApiExample("Not eligible", value=docs.NOT_ELIGIBLE_EXAMPLE, response_only=True)],
    )
    def get(self, request):
        return success_response(services.get_eligibility(request.user))


# --------------------------------------------------------------------------- #
# Request / list
# --------------------------------------------------------------------------- #
class LoanListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsKYCApproved()]
        return [IsAuthenticated()]

    @extend_schema(
        tags=[TAG], summary="My loans",
        description=(
            "Paginated loan history, newest first (pending, active, repaid, rejected and cancelled). "
            "`outstanding` is what is still owed right now (only for `active` loans), `principal` is the amount "
            "requested. `decline_reason` is set for `rejected` loans."
        ),
        parameters=[
            OpenApiParameter("status", str, enum=[s.value for s in LoanStatus], description="Filter by status."),
            *PAGE_PARAMS,
        ],
        responses={
            200: success_response_doc("LoanListSuccess", "Paginated loans.", example=docs.LOAN_LIST_EXAMPLE),
            400: docs.VALIDATION,
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        f = LoanFilterSerializer(data=request.query_params)
        f.is_valid(raise_exception=True)
        qs = Loan.objects.filter(user=request.user).order_by("-created_at")
        if f.validated_data.get("status"):
            qs = qs.filter(status=f.validated_data["status"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(LoanSerializer(page, many=True).data)

    @extend_schema(
        tags=[TAG], summary="Request a loan (needs admin approval)",
        description=(
            "Submits a loan **request** (Tier 2 / KYC approved only). The loan is created with `status: \"pending\"`: "
            "nothing is owed and no interest runs until an admin approves it. The user is emailed a confirmation and "
            "another email when the request is approved or declined. Requirements: wallet `balance` below "
            "`max_eligible_balance_usd`, no other pending/active loan, amount between `min_loan_usd` and "
            "`max_loan_usd`."
            + docs.LOAN_GUIDE
        ),
        request=LoanCreateSerializer,
        examples=[OpenApiExample("Valid request", value=docs.LOAN_REQUEST, request_only=True)],
        responses={
            201: success_response_doc(
                "LoanRequestedSuccess", "Loan request created (pending).", example=docs.LOAN_REQUESTED_EXAMPLE,
                message="Your loan request has been submitted and is waiting for admin approval.",
            ),
            400: docs.wallet_error(
                "Invalid fields, amount out of range, or balance too high.",
                {"code": "validation_error", "message": "Please correct the highlighted fields.",
                 "errors": {"duration": ['"5_days" is not a valid choice.']}},
                {"code": "loan_below_minimum", "message": "The minimum loan is 10.00 USD.",
                 "data": {"min_loan_usd": "10.00"}},
                {"code": "loan_above_maximum", "message": "The maximum loan is 10000.00 USD.",
                 "data": {"max_loan_usd": "10000.00"}},
                {"code": "balance_too_high",
                 "message": "You can only borrow when your balance is below 50.00 USD.",
                 "data": {"balance": "120.00", "max_eligible_balance_usd": "50.00"}},
            ),
            401: docs.UNAUTH,
            403: docs.KYC_REQUIRED,
            409: docs.wallet_error(
                "The user already has a pending request or an active loan.",
                {"code": "loan_request_pending",
                 "message": "You already have a loan request waiting for approval.",
                 "data": {"loan_id": "d4e5f6a7-1b2c-4d3e-8f90-a1b2c3d4e5f6", "status": "pending"}},
                {"code": "active_loan_exists",
                 "message": "You already have an active loan. Repay it in full before borrowing again.",
                 "data": {"loan_id": "d4e5f6a7-1b2c-4d3e-8f90-a1b2c3d4e5f6", "status": "active"}},
            ),
            429: docs.THROTTLED,
        },
    )
    def post(self, request):
        s = LoanCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        loan = services.request_loan(request.user, **s.validated_data)
        return success_response(
            _loan_payload(loan, request.user),
            message="Your loan request has been submitted and is waiting for admin approval.",
            status=201,
        )


# --------------------------------------------------------------------------- #
# Current / detail / cancel
# --------------------------------------------------------------------------- #
class LoanCurrentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Current loan (pending or active)",
        description=(
            "Returns the caller's open loan, i.e. the one that is `pending` (waiting for approval) or `active`, or "
            "`data: null` if there is none. Use it for the loan page and branch on `status`. For an `active` loan "
            "show `outstanding` as the amount owed and `principal` in the loan details."
        ),
        responses={
            200: success_response_doc("LoanCurrentSuccess", "Open loan or null.", example=docs.LOAN_CURRENT_EXAMPLE),
            401: docs.UNAUTH,
        },
        examples=[
            OpenApiExample("Pending approval", value=docs.LOAN_CURRENT_PENDING_EXAMPLE, response_only=True),
            OpenApiExample("No open loan", value=docs.LOAN_NONE_EXAMPLE, response_only=True),
        ],
    )
    def get(self, request):
        loan = services.get_open_loan(request.user)
        if loan is None:
            # success_response() turns data=None into {}; the contract here is an explicit null.
            return Response({"success": True, "message": "You have no open loan.", "data": None})
        return success_response(_loan_payload(loan, request.user))


class LoanDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Loan detail (with repayments)",
        description="Returns one of the caller's loans including the list of `repayments`. Other users' ids return 404.",
        parameters=[ID_PARAM],
        responses={
            200: success_response_doc("LoanDetailSuccess", "Loan.", example=docs.LOAN_DETAIL_EXAMPLE),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
        },
    )
    def get(self, request, pk):
        loan = Loan.objects.filter(pk=pk, user=request.user).first()
        if loan is None:
            raise BusinessError("not_found", "Not found.", status=404)
        return success_response(LoanDetailSerializer(loan).data)


class LoanCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Cancel loan request",
        description="Cancels one of the caller's loan requests that is still `pending`. Approved loans cannot be cancelled.",
        request=None, parameters=[ID_PARAM],
        responses={
            200: success_response_doc(
                "LoanCancelSuccess", "Loan request cancelled.", example=docs.LOAN_CANCELLED_EXAMPLE,
                message="Loan request cancelled.",
            ),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
            409: docs.wallet_error(
                "Only pending requests can be cancelled.",
                {"code": "conflict", "message": "Only loan requests waiting for approval can be cancelled.",
                 "data": {"status": "active"}},
            ),
        },
    )
    def post(self, request, pk):
        loan = services.cancel_loan(request.user, pk)
        return success_response(LoanSerializer(loan).data, message="Loan request cancelled.")


# --------------------------------------------------------------------------- #
# Repay
# --------------------------------------------------------------------------- #
class LoanRepayView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Repay loan from wallet balance (Repay button)",
        description=(
            "Pays an `active` loan from the caller's **available wallet balance**. Body is optional: omit `amount` to "
            "pay `min(available_balance, outstanding)`; send `amount` to pay a specific part (it is capped at what is "
            "owed).\n\n"
            "**If the wallet has no available money the server returns `400 no_funds_to_repay` with "
            "`data.action = \"deposit\"`. Show \"There is no money in your account balance. Deposit money to repay "
            "your loan\" and send the user to the Deposit page.** Deposits are applied to the loan automatically once "
            "approved, so the user does not need to come back and press Repay again."
            + docs.LOAN_GUIDE
        ),
        request=LoanRepaySerializer, parameters=[ID_PARAM],
        examples=[OpenApiExample("Partial payment", value=docs.REPAY_REQUEST, request_only=True)],
        responses={
            200: success_response_doc(
                "LoanRepaySuccess", "Payment applied.", example=docs.REPAY_EXAMPLE,
                message="Payment received. Your loan balance is now 4600.00 USD.",
            ),
            400: docs.wallet_error(
                "No funds, or the amount is more than the available balance.",
                {"code": "no_funds_to_repay",
                 "message": "There is no money in your account balance. Deposit funds to repay your loan.",
                 "data": {**docs.BALANCES, "loan_outstanding": "4600.00", "action": "deposit"}},
                {"code": "insufficient_balance",
                 "message": "The amount is more than your available balance. Deposit funds to repay more.",
                 "data": {**docs.BALANCES, "loan_outstanding": "4600.00", "action": "deposit"}},
                {"code": "validation_error", "message": "Please correct the highlighted fields.",
                 "errors": {"amount": ["Amount must be greater than 0."]}},
            ),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
            409: docs.wallet_error(
                "The loan is not active (still pending, rejected, cancelled) or is already repaid.",
                {"code": "loan_not_active", "message": "This loan is not active, so there is nothing to repay.",
                 "data": {"status": "pending"}},
                {"code": "conflict", "message": "This loan has already been repaid.", "data": {"status": "repaid"}},
            ),
        },
    )
    def post(self, request, pk):
        s = LoanRepaySerializer(data=request.data or {})
        s.is_valid(raise_exception=True)
        loan, repayment, wallet = services.repay_from_wallet(request.user, pk, s.validated_data.get("amount"))
        data = _loan_payload(loan, request.user, wallet)
        data["repayment"] = LoanRepaymentSerializer(repayment).data if repayment else None
        data["balances"] = wallet_services.balance_strings(wallet)
        message = (
            "Your loan is fully repaid. Thank you!" if loan.status == LoanStatus.REPAID
            else f"Payment received. Your loan balance is now {data['outstanding']} USD."
        )
        return success_response(data, message=message)


# --------------------------------------------------------------------------- #
# Cron
# --------------------------------------------------------------------------- #
class ProcessLoanRemindersView(APIView):
    authentication_classes = []
    permission_classes = [HasCronSecret]
    throttle_classes = []

    @extend_schema(
        tags=["Cron"], auth=[], summary="Send loan reminders and overdue notices",
        description=(
            "Emails (1) the halfway-to-due-date reminder and (2) the overdue notice for **active** loans, each "
            "**once per loan**. Max 100 of each per run. Idempotent and safe to run repeatedly; schedule it every "
            "15-60 minutes. Interest itself does not need a cron job, it is computed from timestamps."
        ),
        request=None,
        parameters=[OpenApiParameter("X-Cron-Secret", str, OpenApiParameter.HEADER, required=True,
                                     description="Shared secret (CRON_SECRET_KEY).")],
        responses={
            200: success_response_doc(
                "LoanProcessRemindersSuccess", "Summary of the run.", example=docs.CRON_EXAMPLE,
                message="Processed loan reminders.",
            ),
            403: docs.CRON_FORBIDDEN,
        },
    )
    def post(self, request):
        return success_response(services.process_reminders(), message="Processed loan reminders.")