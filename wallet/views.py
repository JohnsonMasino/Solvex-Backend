from django.conf import settings
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.exceptions import BusinessError
from core.openapi import success_response_doc
from core.pagination import StandardPagination
from core.permissions import HasCronSecret, IsKYCApproved
from core.responses import success_response
from core.throttles import WithdrawalThrottle

from . import docs, services
from .models import (
    Deposit, DepositStatus, EntryType, WalletTransaction, Withdrawal, WithdrawalStatus,
)
from .serializers import (
    ConfirmWithdrawalSerializer, DepositCreateSerializer, DepositFilterSerializer,
    DepositSerializer, TransactionFilterSerializer, TransactionSerializer, WalletSerializer,
    WithdrawalCreateSerializer, WithdrawalFilterSerializer, WithdrawalSerializer,
)

TAG = "Wallet"
ID_PARAM = OpenApiParameter("id", str, OpenApiParameter.PATH, description="Record UUID.")
PAGE_PARAMS = [
    OpenApiParameter("page", int, description="Page number (default 1)."),
    OpenApiParameter("page_size", int, description="Items per page (default 20, max 100)."),
]


def _paginated(view, request, queryset, serializer_class):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


def _fmt_usdt(value) -> str:
    return format(value.normalize(), "f")


# --------------------------------------------------------------------------- #
# Summary / config
# --------------------------------------------------------------------------- #
class WalletSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Wallet summary",
        description="Returns the caller's USD balance buckets. Tier 1 users see zeros.",
        responses={
            200: success_response_doc("WalletSummarySuccess", "Wallet balances.", example=docs.WALLET_EXAMPLE),
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        return success_response(WalletSerializer(services.get_or_create_wallet(request.user)).data)


class WalletConfigView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Deposit / withdrawal configuration",
        description="Returns the USDT/USD rate, minimums, supported networks, deposit addresses and address regexes for live conversion and validation.",
        responses={
            200: success_response_doc("WalletConfigSuccess", "Wallet configuration.", example=docs.CONFIG_EXAMPLE),
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        return success_response(services.get_config())


# --------------------------------------------------------------------------- #
# Deposits
# --------------------------------------------------------------------------- #
class DepositListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsKYCApproved()]
        return [IsAuthenticated()]

    @extend_schema(
        tags=[TAG], summary="My deposits",
        description="Paginated list of the caller's deposit requests, newest first.",
        parameters=[
            OpenApiParameter("status", str, enum=[s.value for s in DepositStatus], description="Filter by status."),
            *PAGE_PARAMS,
        ],
        responses={
            200: success_response_doc("DepositListSuccess", "Paginated deposits.", example=docs.DEPOSIT_LIST_EXAMPLE),
            400: docs.VALIDATION,
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        f = DepositFilterSerializer(data=request.query_params)
        f.is_valid(raise_exception=True)
        qs = Deposit.objects.filter(user=request.user).order_by("-created_at")
        if f.validated_data.get("status"):
            qs = qs.filter(status=f.validated_data["status"])
        return _paginated(self, request, qs, DepositSerializer)

    @extend_schema(
        tags=[TAG], summary="Create deposit request",
        description="Creates a USDT deposit request (Tier 2 only). The company address is returned; funds are credited after admin confirmation.",
        request=DepositCreateSerializer,
        examples=[OpenApiExample("Valid request", value=docs.DEPOSIT_REQUEST, request_only=True)],
        responses={
            201: success_response_doc(
                "DepositCreatedSuccess", "Deposit request created.", example=docs.DEPOSIT_CREATED_EXAMPLE,
            ),
            400: docs.wallet_error(
                "Invalid fields, or amount below the minimum.",
                {"code": "validation_error", "message": "Please correct the highlighted fields.",
                 "errors": {"network": ['"ton" is not a valid choice.']}},
                {"code": "deposit_below_minimum", "message": "The minimum deposit is 10.00 USD.",
                 "data": {"min_deposit_usd": "10.00"}},
            ),
            401: docs.UNAUTH,
            403: docs.KYC_REQUIRED,
            409: docs.wallet_error(
                "Pending-deposit cap reached or duplicate transaction hash.",
                {"code": "too_many_pending_deposits",
                 "message": "You have too many pending deposits. Please wait for them to be processed or cancel one.",
                 "data": {"max_pending_deposits": 5}},
                {"code": "tx_hash_already_used", "message": "This transaction hash has already been submitted."},
            ),
            429: docs.THROTTLED,
            503: docs.wallet_error(
                "Deposit address for this network is not configured.",
                {"code": "deposit_unavailable",
                 "message": "Deposits on this network are temporarily unavailable. Please try another network later."},
            ),
        },
    )
    def post(self, request):
        s = DepositCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        deposit = services.create_deposit(request.user, **s.validated_data)
        return success_response(
            DepositSerializer(deposit).data,
            message=(
                f"Deposit request created. Send exactly {_fmt_usdt(deposit.usdt_amount)} USDT "
                f"on {deposit.network.upper()} to the address shown."
            ),
            status=201,
        )


class DepositDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Deposit detail",
        description="Returns one of the caller's deposits. Other users' ids return 404.",
        parameters=[ID_PARAM],
        responses={
            200: success_response_doc("DepositDetailSuccess", "Deposit.", example=docs.DEPOSIT_DETAIL_EXAMPLE),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
        },
    )
    def get(self, request, pk):
        deposit = Deposit.objects.filter(pk=pk, user=request.user).first()
        if deposit is None:
            raise BusinessError("not_found", "Not found.", status=404)
        return success_response(DepositSerializer(deposit).data)


class DepositCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Cancel deposit",
        description="Cancels one of the caller's pending deposits.",
        request=None, parameters=[ID_PARAM],
        responses={
            200: success_response_doc(
                "DepositCancelSuccess", "Deposit cancelled.", example=docs.DEPOSIT_CANCELLED_EXAMPLE,
                message="Deposit cancelled.",
            ),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
            409: docs.wallet_error(
                "Only pending deposits can be cancelled.",
                {"code": "conflict", "message": "Only pending deposits can be cancelled.",
                 "data": {"status": "completed"}},
            ),
        },
    )
    def post(self, request, pk):
        deposit = services.cancel_deposit(request.user, pk)
        return success_response(DepositSerializer(deposit).data, message="Deposit cancelled.")


# --------------------------------------------------------------------------- #
# Withdrawals
# --------------------------------------------------------------------------- #
class WithdrawalListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsKYCApproved()]
        return [IsAuthenticated()]

    def get_throttles(self):
        if self.request.method == "POST":
            return [WithdrawalThrottle()]
        return super().get_throttles()

    @extend_schema(
        tags=[TAG], summary="My withdrawals",
        description="Paginated withdrawal history, newest first. OTP data and admin notes are never exposed.",
        parameters=[
            OpenApiParameter("status", str, enum=[s.value for s in WithdrawalStatus], description="Filter by status."),
            *PAGE_PARAMS,
        ],
        responses={
            200: success_response_doc("WithdrawalListSuccess", "Paginated withdrawals.", example=docs.WITHDRAWAL_LIST_EXAMPLE),
            400: docs.VALIDATION,
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        f = WithdrawalFilterSerializer(data=request.query_params)
        f.is_valid(raise_exception=True)
        qs = Withdrawal.objects.filter(user=request.user).order_by("-created_at")
        if f.validated_data.get("status"):
            qs = qs.filter(status=f.validated_data["status"])
        return _paginated(self, request, qs, WithdrawalSerializer)

    @extend_schema(
        tags=[TAG], summary="Start withdrawal",
        description="Validates the request (Tier 2 only) and emails a one-time code. Funds are reserved only after the code is confirmed.",
        request=WithdrawalCreateSerializer,
        examples=[OpenApiExample("Valid request", value=docs.WITHDRAWAL_REQUEST, request_only=True)],
        responses={
            201: success_response_doc(
                "WithdrawalCreatedSuccess", "Withdrawal created; OTP emailed.", example=docs.WITHDRAWAL_CREATED_EXAMPLE,
                message="We've sent a 6-digit code to your email to confirm this withdrawal.",
            ),
            400: docs.wallet_error(
                "Invalid fields, address, amount or insufficient funds.",
                {"code": "validation_error", "message": "Please correct the highlighted fields.",
                 "errors": {"usd_amount": ["A valid number is required."]}},
                {"code": "invalid_address", "message": "Enter a valid BEP20 wallet address."},
                {"code": "withdrawal_below_minimum", "message": "The minimum withdrawal is 20.00 USD.",
                 "data": {"min_withdrawal_usd": "20.00"}},
                {"code": "insufficient_balance", "message": "Withdrawal amount exceeds your account balance.",
                 "data": docs.BALANCES},
                {"code": "funds_in_use",
                 "message": "Part of your balance is in active trades or pending withdrawals and can't be withdrawn.",
                 "data": docs.BALANCES},
            ),
            401: docs.UNAUTH,
            403: docs.KYC_REQUIRED,
            429: docs.THROTTLED,
        },
    )
    def post(self, request):
        s = WithdrawalCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        withdrawal, otp_sent = services.create_withdrawal(request.user, **s.validated_data)
        data = WithdrawalSerializer(withdrawal).data
        data["otp_sent"] = otp_sent
        data["expires_in_minutes"] = _expiry_minutes()
        return success_response(
            data,
            message="We've sent a 6-digit code to your email to confirm this withdrawal.",
            status=201,
        )


def _expiry_minutes() -> int:
    return settings.PENDING_TRANSACTION_EXPIRY_MINUTES


class WithdrawalDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Withdrawal detail",
        description="Returns one of the caller's withdrawals. Other users' ids return 404.",
        parameters=[ID_PARAM],
        responses={
            200: success_response_doc("WithdrawalDetailSuccess", "Withdrawal.", example=docs.WITHDRAWAL_DETAIL_EXAMPLE),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
        },
    )
    def get(self, request, pk):
        w = Withdrawal.objects.filter(pk=pk, user=request.user).first()
        if w is None:
            raise BusinessError("not_found", "Not found.", status=404)
        return success_response(WithdrawalSerializer(w).data)


class WithdrawalConfirmView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [WithdrawalThrottle]

    @extend_schema(
        tags=[TAG], summary="Confirm withdrawal with OTP",
        description="Confirms the emailed code, reserves the funds and queues the withdrawal for admin processing.",
        request=ConfirmWithdrawalSerializer, parameters=[ID_PARAM],
        examples=[OpenApiExample("Valid request", value=docs.CONFIRM_REQUEST, request_only=True)],
        responses={
            200: success_response_doc(
                "WithdrawalConfirmSuccess", "Withdrawal confirmed and pending admin processing.",
                example=docs.WITHDRAWAL_CONFIRMED_EXAMPLE,
                message="Withdrawal request submitted. It will be processed shortly.",
            ),
            400: docs.wallet_error(
                "Invalid code, expired request, or funds changed since creation.",
                {"code": "validation_error", "message": "Please correct the highlighted fields.",
                 "errors": {"otp": ["Enter the 6-digit code sent to your email."]}},
                {"code": "invalid_otp", "message": "The code you entered is incorrect.",
                 "data": {"attempts_remaining": 4}},
                {"code": "otp_attempts_exceeded",
                 "message": "Too many incorrect codes. Please start a new withdrawal."},
                {"code": "withdrawal_expired",
                 "message": "This withdrawal request has expired. Please start a new one."},
                {"code": "insufficient_balance", "message": "Withdrawal amount exceeds your account balance.",
                 "data": docs.BALANCES},
                {"code": "funds_in_use",
                 "message": "Part of your balance is in active trades or pending withdrawals and can't be withdrawn.",
                 "data": docs.BALANCES},
            ),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
            409: docs.wallet_error(
                "Not awaiting confirmation.",
                {"code": "conflict", "message": "This withdrawal is no longer awaiting confirmation.",
                 "data": {"status": "pending"}},
            ),
            429: docs.THROTTLED,
        },
    )
    def post(self, request, pk):
        s = ConfirmWithdrawalSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        withdrawal, balances = services.confirm_withdrawal(request.user, pk, s.validated_data["otp"])
        data = WithdrawalSerializer(withdrawal).data
        data["balances"] = balances
        return success_response(data, message="Withdrawal request submitted. It will be processed shortly.")


class WithdrawalResendOTPView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [WithdrawalThrottle]

    @extend_schema(
        tags=[TAG], summary="Resend withdrawal OTP",
        description="Issues a new code (the old one stops working) subject to a cooldown and a per-request resend limit.",
        request=None, parameters=[ID_PARAM],
        responses={
            200: success_response_doc(
                "WithdrawalResendSuccess", "New code emailed.", example=docs.WITHDRAWAL_RESEND_EXAMPLE,
                message="A new code has been sent to your email.",
            ),
            400: docs.wallet_error(
                "Request expired.",
                {"code": "withdrawal_expired", "message": "This withdrawal request has expired. Please start a new one."},
            ),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
            409: docs.wallet_error(
                "Not awaiting confirmation.",
                {"code": "conflict", "message": "This withdrawal is no longer awaiting confirmation.",
                 "data": {"status": "pending"}},
            ),
            429: docs.wallet_error(
                "Cooldown, resend limit, or throttled.",
                {"code": "otp_cooldown", "message": "Please wait before requesting another code.",
                 "data": {"retry_after_seconds": 37}},
                {"code": "otp_resend_limit",
                 "message": "You have reached the resend limit. Please start a new withdrawal."},
                {"code": "throttled", "message": "Request was throttled.", "data": {"retry_after_seconds": 42}},
            ),
        },
    )
    def post(self, request, pk):
        otp_sent = services.resend_withdrawal_otp(request.user, pk)
        return success_response(
            {"otp_sent": otp_sent, "expires_in_minutes": _expiry_minutes()},
            message="A new code has been sent to your email." if otp_sent else "We couldn't send the email. Please try again.",
        )


class WithdrawalCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Cancel withdrawal",
        description="Cancels one of the caller's withdrawals that is still awaiting OTP confirmation.",
        request=None, parameters=[ID_PARAM],
        responses={
            200: success_response_doc(
                "WithdrawalCancelSuccess", "Withdrawal cancelled.", example=docs.WITHDRAWAL_CANCELLED_EXAMPLE,
                message="Withdrawal cancelled.",
            ),
            401: docs.UNAUTH, 404: docs.NOT_FOUND,
            409: docs.wallet_error(
                "Once confirmed, only an admin can handle the withdrawal.",
                {"code": "conflict", "message": "Only withdrawals awaiting confirmation can be cancelled.",
                 "data": {"status": "pending"}},
            ),
        },
    )
    def post(self, request, pk):
        w = services.cancel_withdrawal(request.user, pk)
        return success_response(WithdrawalSerializer(w).data, message="Withdrawal cancelled.")


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
class TransactionListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=[TAG], summary="Ledger",
        description="Paginated ledger of every balance movement, newest first, with optional type and date filters.",
        parameters=[
            OpenApiParameter("entry_type", str, enum=[e.value for e in EntryType], description="Filter by entry type."),
            OpenApiParameter("date_from", str, description="YYYY-MM-DD (inclusive)."),
            OpenApiParameter("date_to", str, description="YYYY-MM-DD (inclusive)."),
            *PAGE_PARAMS,
        ],
        responses={
            200: success_response_doc("TransactionListSuccess", "Paginated ledger.", example=docs.TRANSACTIONS_EXAMPLE),
            400: docs.wallet_error(
                "Invalid filter value.",
                {"code": "validation_error", "message": "Please correct the highlighted fields.",
                 "errors": {"date_from": ["Date has wrong format. Use one of these formats instead: YYYY-MM-DD."]}},
            ),
            401: docs.UNAUTH,
        },
    )
    def get(self, request):
        f = TransactionFilterSerializer(data=request.query_params)
        f.is_valid(raise_exception=True)
        v = f.validated_data
        qs = WalletTransaction.objects.filter(user=request.user).order_by("-created_at")
        if v.get("entry_type"):
            qs = qs.filter(entry_type=v["entry_type"])
        if v.get("date_from"):
            qs = qs.filter(created_at__date__gte=v["date_from"])
        if v.get("date_to"):
            qs = qs.filter(created_at__date__lte=v["date_to"])
        return _paginated(self, request, qs, TransactionSerializer)


# --------------------------------------------------------------------------- #
# Cron
# --------------------------------------------------------------------------- #
class ExpirePendingWithdrawalsView(APIView):
    authentication_classes = []
    permission_classes = [HasCronSecret]
    throttle_classes = []

    @extend_schema(
        tags=["Cron"], auth=[], summary="Expire unconfirmed withdrawals",
        description="Marks awaiting_otp withdrawals past their expiry as expired (max 100 per run). No money moves. Safe to run repeatedly.",
        request=None,
        parameters=[OpenApiParameter("X-Cron-Secret", str, OpenApiParameter.HEADER, required=True,
                                     description="Shared secret (CRON_SECRET_KEY).")],
        responses={
            200: success_response_doc(
                "WalletExpirePendingSuccess", "Summary of the run.", example=docs.CRON_EXAMPLE,
                message="Expired pending withdrawals.",
            ),
            403: docs.CRON_FORBIDDEN,
        },
    )
    def post(self, request):
        count = services.expire_pending_withdrawals()
        return success_response({"expired_withdrawals": count}, message="Expired pending withdrawals.")
