"""
trade/services.py — all business logic for the trade app (v2: manual admin settlement).

No function here is consumed by other apps (00 §8). Cross-app imports
(wallet, referral) are lazy and go through their service contracts only.
"""
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

import pycountry
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.exceptions import BusinessError
from core.utils import is_weekend, money

from .models import DURATION_CHOICES, Trade, TradeProfile

logger = logging.getLogger(__name__)

METAL_CODES = {"XAU", "XAG"}
_PAIR_SPLIT = re.compile(r"[/\-_]")
_PAIR_SIDE = re.compile(r"^[A-Z0-9]{2,10}$")

MAX_PROFIT = Decimal("1000000.00")

LIMIT_MSG_STANDARD = (
    "You have reached the maximum number of open trades for your account balance. "
    "Please top up your account balance to open more trades."
)
LIMIT_MSG_MAX = "You have reached the maximum of 10 open trades. Wait for a trade to close before opening another."


# --------------------------------------------------------------------------- helpers
def iso(dt):
    """ISO-8601 UTC with a trailing Z."""
    if dt is None:
        return None
    return dt.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


def balances_payload(balances: dict, full: bool = False) -> dict:
    keys = ["balance", "locked_balance", "available_balance"]
    if full:
        keys.insert(2, "pending_withdrawal_balance")
    return {k: str(money(balances[k])) for k in keys if k in balances}


def _trading_tz():
    return ZoneInfo(settings.TRADING_TIMEZONE)


def get_next_open_at(now=None):
    """Monday 00:00 (TRADING_TIMEZONE) following the current weekend, as aware UTC datetime."""
    local = (now or timezone.now()).astimezone(_trading_tz())
    days = 7 - local.weekday()  # Sat(5) -> 2, Sun(6) -> 1
    target = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return target.astimezone(ZoneInfo("UTC"))


def get_next_close_at(now=None):
    """
    Next Saturday 00:00 (TRADING_TIMEZONE), i.e. when NEW trading is next blocked,
    or None when it is already the weekend. Open trades are NOT auto-closed then.
    """
    local = (now or timezone.now()).astimezone(_trading_tz())
    if local.weekday() >= 5:
        return None
    days = 5 - local.weekday()
    target = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return target.astimezone(ZoneInfo("UTC"))


def get_market_status() -> dict:
    weekend = is_weekend()
    return {
        "market_open": not weekend,
        "is_weekend": weekend,
        "next_open_at": iso(get_next_open_at()) if weekend else None,
        "next_close_at": None if weekend else iso(get_next_close_at()),
    }


def get_trading_config() -> dict:
    data = get_market_status()
    data.update(
        {
            "min_amount": str(money(settings.TRADE_MIN_AMOUNT)),
            "max_leverage": settings.TRADE_MAX_LEVERAGE,
            "durations_hours": list(DURATION_CHOICES),
            "currency": "USD",
            "open_trade_limits": [
                {"min_balance": "0.00", "max_balance": "499.99", "max_open_trades": 2},
                {"min_balance": "500.00", "max_balance": "1500.00", "max_open_trades": 4},
                {"min_balance": "1500.01", "max_balance": None, "max_open_trades": 10},
            ],
        }
    )
    return data


# --------------------------------------------------------------------------- pair rules
def normalize_pair(raw: str) -> tuple[str, str, str]:
    """Return (pair, base, quote). Raises ValueError with a user-safe message."""
    value = (raw or "").strip().upper()
    if not _PAIR_SPLIT.search(value):
        raise ValueError("Pair must contain a separator, e.g. BTC/USDT, BTC-USDT or BTC_USDT.")
    parts = _PAIR_SPLIT.split(value)
    if len(parts) != 2:
        raise ValueError("Pair must have exactly two parts, e.g. BTC/USDT.")
    base, quote = parts
    if not (_PAIR_SIDE.match(base) and _PAIR_SIDE.match(quote)):
        raise ValueError("Each side of the pair must be 2-10 letters or digits.")
    if base == quote:
        raise ValueError("Base and quote of the pair must be different.")
    return f"{base}/{quote}", base, quote


@lru_cache(maxsize=1)
def _currency_codes() -> frozenset:
    return frozenset(c.alpha_3 for c in pycountry.currencies) | frozenset(METAL_CODES)


def detect_market_type(base: str, quote: str) -> str:
    codes = _currency_codes()
    if base in codes and quote in codes:
        return Trade.MarketType.FOREX
    return Trade.MarketType.CRYPTO


# --------------------------------------------------------------------------- limits
def get_max_open_trades(balance: Decimal) -> int:
    balance = money(balance)
    if balance < Decimal("500.00"):
        return 2
    if balance <= Decimal("1500.00"):
        return 4
    return 10


def _limit_message(max_open: int) -> str:
    return LIMIT_MSG_MAX if max_open >= 10 else LIMIT_MSG_STANDARD


def get_user_limits(user):
    """Return (open_count, max_open_trades, balances)."""
    from wallet import services as wallet_services

    balances = wallet_services.get_balances(user)
    open_count = Trade.objects.filter(user=user, status=Trade.Status.OPEN).count()
    return open_count, get_max_open_trades(balances["balance"]), balances


# --------------------------------------------------------------------------- opening
def _unique_reference() -> str:
    from .models import generate_trade_reference

    while True:
        ref = generate_trade_reference()
        if not Trade.objects.filter(reference=ref).exists():
            return ref


def _queue_opened_emails(user, trade):
    """on_commit: user confirmation + staff notification. Neither can ever fail the request."""

    def _send():
        try:
            from .emails import send_trade_opened_email

            send_trade_opened_email(user, trade)
        except Exception:
            logger.exception("trade: opened email failed for %s", trade.id)
        try:
            from .emails import send_admin_new_trade_email

            send_admin_new_trade_email(trade)
        except Exception:
            logger.exception("trade: admin notification email failed for %s", trade.id)

    transaction.on_commit(_send)


def open_trade(user, data, idempotency_key=None) -> tuple[Trade, bool]:
    """
    Open a trade. Returns (trade, created). created=False means an idempotent replay.
    Everything runs in one transaction; any exception rolls back funds + counter + trade.
    The result of the trade is decided later by an admin (settle_trade_by_admin).
    """
    from wallet import services as wallet_services

    with transaction.atomic():
        # (3) Lock the user's trade profile first: serialises numbering, limit checks and idempotency.
        profile, _ = TradeProfile.objects.get_or_create(user=user)
        profile = TradeProfile.objects.select_for_update().get(pk=profile.pk)

        # (1) idempotency
        if idempotency_key:
            existing = Trade.objects.filter(user=user, idempotency_key=idempotency_key).first()
            if existing is not None:
                return existing, False

        # (2) weekend (only blocks OPENING)
        if is_weekend():
            raise BusinessError(
                "market_closed",
                "Trading is closed on weekends. The market reopens on Monday.",
                status=403,
                data={"next_open_at": iso(get_next_open_at())},
            )

        try:
            pair, base, quote = normalize_pair(data["pair"])
        except ValueError as exc:
            raise BusinessError(
                "validation_error", "Please correct the highlighted fields.", errors={"pair": [str(exc)]}
            )
        market_type = detect_market_type(base, quote)
        amount = money(data["amount"])

        # (4)+(5) balances and open-trade limit
        balances = wallet_services.get_balances(user)
        open_count = Trade.objects.filter(user=user, status=Trade.Status.OPEN).count()
        max_open = get_max_open_trades(balances["balance"])
        if open_count >= max_open:
            raise BusinessError(
                "trade_limit_reached",
                _limit_message(max_open),
                status=403,
                data={
                    "open_trades": open_count,
                    "max_open_trades": max_open,
                    "balance": str(money(balances["balance"])),
                },
            )

        # (6) lock funds (trade id is pre-generated so the ledger reference is exact)
        trade_id = uuid.uuid4()
        try:
            wallet_services.lock_funds(user, amount, reference=f"trade:{trade_id}")
        except wallet_services.InsufficientFunds:
            raise BusinessError(
                "insufficient_funds",
                "Insufficient available balance. Funds in open trades or pending withdrawals cannot be used.",
                status=400,
                data=balances_payload(wallet_services.get_balances(user), full=True),
            )

        # (7) sequence number
        n = profile.trades_opened_count + 1

        # (8) create trade (open, no result)
        now = timezone.now()
        duration = int(data["duration_hours"])
        trade = Trade.objects.create(
            id=trade_id,
            user=user,
            reference=_unique_reference(),
            pair=pair,
            market_type=market_type,
            side=data["side"],
            amount=amount,
            leverage=int(data["leverage"]),
            take_profit_pct=data.get("take_profit_pct"),
            stop_loss_pct=data.get("stop_loss_pct"),
            duration_hours=duration,
            status=Trade.Status.OPEN,
            opened_at=now,
            scheduled_close_at=now + timedelta(hours=duration),
            sequence_number=n,
            idempotency_key=idempotency_key or None,
        )

        # (9) counter only increments on success
        profile.trades_opened_count = n
        profile.save(update_fields=["trades_opened_count", "updated_at"])

        # (10) emails after commit (user + admin)
        _queue_opened_emails(user, trade)
        return trade, True


# --------------------------------------------------------------------------- settlement
def _after_close(user, trade):
    """Runs on_commit: closed email, then referral hook. Never raises."""
    try:
        from .emails import send_trade_closed_email

        send_trade_closed_email(user, trade)
    except Exception:
        logger.exception("trade: closed email failed for %s", trade.id)
    try:
        from referral import services as referral_services

        referral_services.process_first_trade_completion(user)
    except Exception:
        logger.exception("trade: referral hook failed for user %s", user.pk)


def close_trade(trade_id, outcome, pnl_amount, reason=Trade.CloseReason.ADMIN, admin_user=None, note=""):
    """
    Apply a result to ONE open trade and settle the wallet.

    `pnl_amount` is signed USD (+profit / -loss). Idempotent: returns None if the trade is
    missing or no longer open (nothing is changed, nobody is paid twice).
    """
    from wallet import services as wallet_services

    if outcome not in (Trade.Outcome.PROFIT, Trade.Outcome.LOSS):
        raise BusinessError("validation_error", "Choose profit or loss.", errors={"outcome": ["Choose profit or loss."]})
    pnl_amount = money(pnl_amount)
    if (outcome == Trade.Outcome.PROFIT and pnl_amount <= 0) or (outcome == Trade.Outcome.LOSS and pnl_amount >= 0):
        raise BusinessError(
            "validation_error", "Amount sign does not match the chosen result.", errors={"amount": ["Invalid amount."]}
        )

    with transaction.atomic():
        trade = Trade.objects.select_for_update().filter(pk=trade_id).first()
        if trade is None or trade.status != Trade.Status.OPEN:
            return None

        if outcome == Trade.Outcome.LOSS and abs(pnl_amount) > trade.amount:
            raise BusinessError(
                "loss_exceeds_amount",
                f"A loss cannot be larger than the trade amount (USD {trade.amount}).",
                status=400,
            )

        user = trade.user
        wallet_services.settle_trade(user, principal=trade.amount, pnl=pnl_amount, reference=f"trade:{trade.id}")

        trade.status = Trade.Status.CLOSED
        trade.closed_at = timezone.now()
        trade.close_reason = reason
        trade.outcome = outcome
        trade.pnl_amount = pnl_amount
        trade.pnl_pct = money(pnl_amount / trade.amount * Decimal(100))
        trade.settled_by = admin_user
        trade.admin_note = (note or "").strip()
        trade.save(
            update_fields=[
                "status", "closed_at", "close_reason", "outcome", "pnl_amount", "pnl_pct",
                "settled_by", "admin_note", "updated_at",
            ]
        )
        transaction.on_commit(lambda: _after_close(user, trade))
        return trade


def settle_trade_by_admin(trade_id, admin_user, outcome, amount, note=""):
    """
    Called from Django admin. `amount` is a POSITIVE USD size; the sign comes from `outcome`.
    Raises BusinessError on invalid input. Returns the closed Trade, or None if already closed.
    """
    if outcome not in (Trade.Outcome.PROFIT, Trade.Outcome.LOSS):
        raise BusinessError("validation_error", "Choose profit or loss.", errors={"outcome": ["Choose profit or loss."]})
    try:
        amount = money(amount)
    except (InvalidOperation, ValueError, TypeError):
        raise BusinessError(
            "validation_error", "Enter a positive amount with at most 2 decimal places.",
            errors={"amount": ["Enter a positive amount with at most 2 decimal places."]},
        )
    if amount <= 0:
        raise BusinessError(
            "validation_error", "Enter a positive amount with at most 2 decimal places.",
            errors={"amount": ["Enter a positive amount with at most 2 decimal places."]},
        )
    if outcome == Trade.Outcome.PROFIT and amount > MAX_PROFIT:
        raise BusinessError("profit_too_large", "Profit is above the allowed maximum.")

    pnl_amount = amount if outcome == Trade.Outcome.PROFIT else -amount
    return close_trade(
        trade_id, outcome, pnl_amount, reason=Trade.CloseReason.ADMIN, admin_user=admin_user, note=note
    )


# --------------------------------------------------------------------------- summary
def get_user_summary(user) -> dict:
    open_count, max_open, balances = get_user_limits(user)
    agg = Trade.objects.filter(user=user).aggregate(
        total=Count("id"),
        closed=Count("id", filter=Q(status=Trade.Status.CLOSED)),
        wins=Count("id", filter=Q(status=Trade.Status.CLOSED, outcome=Trade.Outcome.PROFIT)),
        losses=Count("id", filter=Q(status=Trade.Status.CLOSED, outcome=Trade.Outcome.LOSS)),
        total_pnl=Sum("pnl_amount", filter=Q(status=Trade.Status.CLOSED)),
    )
    return {
        "open_trades": open_count,
        "max_open_trades": max_open,
        "slots_remaining": max(0, max_open - open_count),
        "locked_balance": str(money(balances["locked_balance"])),
        "available_balance": str(money(balances["available_balance"])),
        "balance": str(money(balances["balance"])),
        "total_trades": agg["total"] or 0,
        "closed_trades": agg["closed"] or 0,
        "wins": agg["wins"] or 0,
        "losses": agg["losses"] or 0,
        "total_pnl": str(money(agg["total_pnl"] or Decimal("0"))),
        "market_open": not is_weekend(),
    }