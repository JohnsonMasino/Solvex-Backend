# views.py

import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.openapi import error_response_doc, success_response_doc
from core.pagination import StandardPagination
from core.permissions import IsKYCApproved
from core.responses import success_response

from . import docs
from .models import Trade
from .serializers import OpenTradeSerializer, TradeFilterSerializer, TradeSerializer
from .services import (
    balances_payload,
    get_trading_config,
    get_user_limits,
    get_user_summary,
    open_trade,
)

TAG = "Trade"


def _trade_data(trade, user):
    """Serialized trade plus current wallet balances (used by the open endpoint)."""
    from wallet import services as wallet_services

    data = dict(TradeSerializer(trade).data)
    data["balances"] = balances_payload(wallet_services.get_balances(user))
    return data


# --------------------------------------------------------------------------- config
class TradingConfigView(APIView):
    @extend_schema(
        tags=[TAG],
        summary="Trading rules & market status",
        description=(
            "Returns market open/closed state, trade limits, allowed durations and open-trade limits by balance. "
            "next_close_at is when NEW trading is next blocked (weekend start); open trades are not auto-closed then."
        ),
        responses={
            200: success_response_doc(
                "TradeConfigSuccess",
                "Trading configuration.",
                example=docs.envelope(
                    {
                        "market_open": True, "is_weekend": False, "next_open_at": None,
                        "next_close_at": "2026-10-24T00:00:00Z", "min_amount": "10.00", "max_leverage": 100,
                        "durations_hours": [6, 12, 24], "currency": "USD",
                        "open_trade_limits": [
                            {"min_balance": "0.00", "max_balance": "499.99", "max_open_trades": 2},
                            {"min_balance": "500.00", "max_balance": "1500.00", "max_open_trades": 4},
                            {"min_balance": "1500.01", "max_balance": None, "max_open_trades": 10},
                        ],
                    }
                ),
            ),
            401: docs.err_unauthenticated(),
            429: docs.err_throttled(),
        },
    )
    def get(self, request):
        return success_response(get_trading_config(), "Trading configuration retrieved.")


# --------------------------------------------------------------------------- summary
class TradeSummaryView(APIView):
    @extend_schema(
        tags=[TAG],
        summary="My trading summary",
        description="Open-trade usage, wallet buckets and win/loss totals. wins/losses/total_pnl count settled (closed) trades only.",
        responses={
            200: success_response_doc(
                "TradeSummarySuccess",
                "Trading summary.",
                example=docs.envelope(
                    {
                        "open_trades": 1, "max_open_trades": 2, "slots_remaining": 1, "locked_balance": "300.00",
                        "available_balance": "850.00", "balance": "1250.00", "total_trades": 14,
                        "closed_trades": 13, "wins": 9, "losses": 4, "total_pnl": "12.45", "market_open": True,
                    }
                ),
            ),
            401: docs.err_unauthenticated(),
            429: docs.err_throttled(),
        },
    )
    def get(self, request):
        return success_response(get_user_summary(request.user), "Trading summary retrieved.")


# --------------------------------------------------------------------------- active
class ActiveTradesView(APIView):
    @extend_schema(
        tags=[TAG],
        summary="My open trades",
        description=(
            "All currently open trades (not paginated, max 10), soonest closing first. Trades are settled manually "
            "by staff: outcome, pnl_pct and pnl_amount stay null until then. awaiting_settlement is true when the "
            "trade's time is up and the result is pending."
        ),
        responses={
            200: success_response_doc(
                "ActiveTradesSuccess",
                "Open trades.",
                example=docs.envelope(
                    {"count": 2, "max_open_trades": 2, "results": [docs.AWAITING_TRADE, docs.OPEN_TRADE]}
                ),
            ),
            401: docs.err_unauthenticated(),
            429: docs.err_throttled(),
        },
    )
    def get(self, request):
        _, max_open, _ = get_user_limits(request.user)
        qs = Trade.objects.filter(user=request.user, status=Trade.Status.OPEN).order_by("scheduled_close_at")
        results = TradeSerializer(qs, many=True).data
        return success_response(
            {"count": len(results), "max_open_trades": max_open, "results": results},
            "Active trades retrieved.",
        )


# --------------------------------------------------------------------------- list / create
_FILTER_PARAMS = [
    OpenApiParameter("status", OpenApiTypes.STR, enum=["open", "closed"], description="Filter by status."),
    OpenApiParameter("side", OpenApiTypes.STR, enum=["buy", "sell"], description="Filter by side."),
    OpenApiParameter("market_type", OpenApiTypes.STR, enum=["crypto", "forex"], description="Filter by market."),
    OpenApiParameter("pair", OpenApiTypes.STR, description="Pair, e.g. BTC/USDT."),
    OpenApiParameter("date_from", OpenApiTypes.DATE, description="Opened on/after this date (YYYY-MM-DD)."),
    OpenApiParameter("date_to", OpenApiTypes.DATE, description="Opened on/before this date (YYYY-MM-DD)."),
    OpenApiParameter("ordering", OpenApiTypes.STR, enum=["-opened_at", "opened_at"], description="Sort order."),
    OpenApiParameter("page", OpenApiTypes.INT, description="Page number."),
    OpenApiParameter("page_size", OpenApiTypes.INT, description="Page size (max 100)."),
]


class TradeListCreateView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsKYCApproved()]
        return [IsAuthenticated()]

    @extend_schema(
        tags=[TAG],
        summary="Trade history",
        description=(
            "Paginated list of the current user's trades with optional filters. Trades are settled manually by "
            "staff; settled trades include outcome and P/L, open trades have them null."
        ),
        parameters=_FILTER_PARAMS,
        responses={
            200: success_response_doc(
                "TradeListSuccess",
                "Paginated trades.",
                example=docs.envelope(
                    {"count": 2, "next": None, "previous": None, "results": [docs.OPEN_TRADE, docs.CLOSED_TRADE]}
                ),
            ),
            400: error_response_doc(
                "Invalid filter value.",
                code="validation_error",
                message="Please correct the highlighted fields.",
                errors={"status": ['"done" is not a valid choice.']},
            ),
            401: docs.err_unauthenticated(),
            429: docs.err_throttled(),
        },
    )
    def get(self, request):
        filters = TradeFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        f = filters.validated_data

        qs = Trade.objects.filter(user=request.user)
        if "status" in f:
            qs = qs.filter(status=f["status"])
        if "side" in f:
            qs = qs.filter(side=f["side"])
        if "market_type" in f:
            qs = qs.filter(market_type=f["market_type"])
        if "pair" in f:
            qs = qs.filter(pair=f["pair"])
        if "date_from" in f:
            qs = qs.filter(opened_at__date__gte=f["date_from"])
        if "date_to" in f:
            qs = qs.filter(opened_at__date__lte=f["date_to"])
        qs = qs.order_by(f.get("ordering", "-opened_at"), "-created_at")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(TradeSerializer(page, many=True).data)

    @extend_schema(
        tags=[TAG],
        summary="Open a trade",
        description=(
            "Opens a trade for a Tier 2 (KYC-approved) user and locks the margin. Staff are notified and settle the "
            "result manually. Optional Idempotency-Key header replays the original trade with 200. "
            "TP/SL and leverage are informational only."
        ),
        request=OpenTradeSerializer,
        parameters=[
            OpenApiParameter(
                "Idempotency-Key", OpenApiTypes.STR, OpenApiParameter.HEADER, required=False,
                description="Unique key (max 64 chars) to make the request safely retryable.",
            )
        ],
        examples=[OpenApiExample("Valid request", value=docs.OPEN_REQUEST, request_only=True)],
        responses={
            200: success_response_doc(
                "TradeReplaySuccess",
                "Idempotency-Key replay: the existing trade is returned.",
                example=docs.envelope({**docs.OPEN_TRADE, "balances": docs.BALANCES}, "Trade already opened."),
            ),
            201: success_response_doc(
                "TradeOpenedSuccess",
                "Trade opened. Margin locked.",
                example=docs.envelope({**docs.OPEN_TRADE, "balances": docs.BALANCES}, "Trade opened successfully."),
            ),
            400: error_response_doc(
                "Validation error or insufficient available balance.",
                code="insufficient_funds",
                message="Insufficient available balance. Funds in open trades or pending withdrawals cannot be used.",
            ),
            401: docs.err_unauthenticated(),
            403: error_response_doc(
                "KYC required, market closed (weekend) or open-trade limit reached.",
                code="trade_limit_reached",
                message="You have reached the maximum number of open trades for your account balance. "
                "Please top up your account balance to open more trades.",
            ),
            429: docs.err_throttled(),
        },
    )
    def post(self, request):
        serializer = OpenTradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key = request.headers.get("Idempotency-Key")
        if key is not None:
            key = key.strip() or None
        if key and len(key) > 64:
            raise ValidationError({"Idempotency-Key": ["Idempotency-Key must be at most 64 characters."]})

        trade, created = open_trade(request.user, serializer.validated_data, idempotency_key=key)
        data = _trade_data(trade, request.user)
        if created:
            return success_response(data, "Trade opened successfully.", status=201)
        return success_response(data, "Trade already opened.", status=200)


# --------------------------------------------------------------------------- detail
class TradeDetailView(APIView):
    @extend_schema(
        tags=[TAG],
        summary="Trade detail",
        description="One of the current user's trades. Other users' trades return 404. Outcome fields stay null until staff settle the trade.",
        parameters=[OpenApiParameter("trade_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
        responses={
            200: success_response_doc("TradeDetailSuccess", "Trade detail.", example=docs.envelope(docs.CLOSED_TRADE)),
            401: docs.err_unauthenticated(),
            404: error_response_doc("Trade not found.", code="not_found", message="Not found."),
            429: docs.err_throttled(),
        },
    )
    def get(self, request, trade_id):
        try:
            uuid.UUID(str(trade_id))
        except ValueError:
            raise NotFound()
        trade = Trade.objects.filter(user=request.user, pk=trade_id).first()
        if trade is None:
            raise NotFound()
        return success_response(TradeSerializer(trade).data, "Trade retrieved.")