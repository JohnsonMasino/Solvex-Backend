import logging

from django.conf import settings

from core.mailer import send_email

logger = logging.getLogger(__name__)


def _fmt_dt(dt):
    return dt.strftime("%d %b %Y, %H:%M UTC") if dt else ""


def _user_name(user):
    return (getattr(user, "full_name", "") or "").strip() or "Trader"


def send_trade_opened_email(user, trade):
    """Explicit, safe context. Nothing about the result exists yet."""
    context = {
        "user_name": _user_name(user),
        "reference": trade.reference,
        "pair": trade.pair,
        "side": trade.side.upper(),
        "amount": f"{trade.amount:,.2f}",
        "leverage": trade.leverage,
        "take_profit_pct": f"{trade.take_profit_pct:.2f}" if trade.take_profit_pct is not None else None,
        "stop_loss_pct": f"{trade.stop_loss_pct:.2f}" if trade.stop_loss_pct is not None else None,
        "duration_hours": trade.duration_hours,
        "opened_at": _fmt_dt(trade.opened_at),
        "scheduled_close_at": _fmt_dt(trade.scheduled_close_at),
    }
    return send_email(
        to=user.email,
        subject=f"Your SolvexMarketInc trade {trade.reference} is open",
        template="trade/trade_opened.html",
        context=context,
        from_email=settings.NOTICE_FROM_EMAIL,
    )


def send_trade_closed_email(user, trade):
    """Settlement email. Never includes admin_note or settled_by."""
    new_balance = None
    try:
        from wallet import services as wallet_services

        new_balance = f"{wallet_services.get_balances(user)['balance']:,.2f}"
    except Exception:  # never let a balance lookup block the email
        logger.exception("trade email: could not load balance for trade %s", trade.id)

    pnl = trade.pnl_amount
    is_profit = trade.outcome == "profit"
    sign = "+" if (pnl is not None and pnl >= 0) else "-"
    pnl_display = f"{sign}${abs(pnl):,.2f}" if pnl is not None else ""
    pct = trade.pnl_pct
    pct_display = f"{'+' if is_profit else '-'}{abs(pct):,.2f}%" if pct is not None else ""
    context = {
        "user_name": _user_name(user),
        "reference": trade.reference,
        "pair": trade.pair,
        "side": trade.side.upper(),
        "amount": f"{trade.amount:,.2f}",
        "is_profit": is_profit,
        "outcome_label": "Profit" if is_profit else "Loss",
        "pnl_amount": pnl_display,
        "pnl_pct": pct_display,
        "closed_at": _fmt_dt(trade.closed_at),
        "new_balance": new_balance,
    }
    return send_email(
        to=user.email,
        subject=f"Your trade {trade.reference} has been settled ({context['outcome_label']})",
        template="trade/trade_closed.html",
        context=context,
        from_email=settings.NOTICE_FROM_EMAIL,
    )


def send_admin_new_trade_email(trade):
    """Staff alert for EVERY opened trade. No-op (returns False) when ADMIN_NOTIFICATION_EMAIL is empty."""
    to = (getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "") or "").strip()
    if not to:
        return False

    user = trade.user
    balance = None
    try:
        from wallet import services as wallet_services

        balance = f"{wallet_services.get_balances(user)['balance']:,.2f}"
    except Exception:
        logger.exception("trade admin email: could not load balance for trade %s", trade.id)

    backend = (getattr(settings, "BACKEND_URL", "") or "").rstrip("/")
    context = {
        "reference": trade.reference,
        "user_name": _user_name(user),
        "user_email": user.email,
        "pair": trade.pair,
        "side": trade.side.upper(),
        "amount": f"{trade.amount:,.2f}",
        "leverage": trade.leverage,
        "take_profit_pct": f"{trade.take_profit_pct:.2f}" if trade.take_profit_pct is not None else None,
        "stop_loss_pct": f"{trade.stop_loss_pct:.2f}" if trade.stop_loss_pct is not None else None,
        "duration_hours": trade.duration_hours,
        "opened_at": _fmt_dt(trade.opened_at),
        "scheduled_close_at": _fmt_dt(trade.scheduled_close_at),
        "user_balance": balance,
        "admin_url": f"{backend}/admin/trade/trade/{trade.id}/change/",
    }
    return send_email(
        to=to,
        subject=f"New trade opened: {trade.reference} ({trade.pair}, ${trade.amount:,.2f})",
        template="trade/admin_new_trade.html",
        context=context,
        from_email=settings.NOTICE_FROM_EMAIL,
    )