"""
wallet/services.py - ALL wallet business logic + the public cross-app contract (00 section 8):

    get_or_create_wallet(user) -> Wallet
    get_balances(user) -> dict[str, Decimal]
    has_completed_deposit(user) -> bool
    credit_wallet(user, amount, entry_type, reference="", description="") -> WalletTransaction
    lock_funds(user, amount, reference) -> WalletTransaction
    settle_trade(user, principal, pnl, reference) -> None
    complete_deposit / decline_deposit / complete_withdrawal / fail_withdrawal   (admin only)

Locking order everywhere: wallet row first, then the deposit/withdrawal row.
"""
import logging
import math
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import BusinessError
from core.utils import generate_otp, hash_value, money, verify_hash
from core.utils import usdt as to_usdt

from . import emails
from .models import (
    Deposit, DepositStatus, EntryType, Network, Wallet, WalletTransaction,
    Withdrawal, WithdrawalStatus, generate_reference,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
RATE_Q = Decimal("0.000001")
CRON_BATCH_SIZE = 100

ADDRESS_REGEX = {
    Network.BEP20: r"^0x[a-fA-F0-9]{40}$",
    Network.ERC20: r"^0x[a-fA-F0-9]{40}$",
    Network.TRC20: r"^T[1-9A-HJ-NP-Za-km-z]{33}$",
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class InsufficientFunds(BusinessError):
    """Raised when available funds are lower than the amount requested."""

    def __init__(self, message="Insufficient available funds.", data=None, code="insufficient_funds", status=400):
        super().__init__(code, message, status, data)


def _not_found():
    return BusinessError("not_found", "Not found.", status=404)


def _conflict(current_status, message):
    return BusinessError("conflict", message, status=409, data={"status": current_status})


# --------------------------------------------------------------------------- #
# Wallet helpers
# --------------------------------------------------------------------------- #
def get_or_create_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _locked_wallet(user) -> Wallet:
    """Must be called inside transaction.atomic()."""
    wallet = get_or_create_wallet(user)
    return Wallet.objects.select_for_update().get(pk=wallet.pk)


def _fmt(value) -> str:
    return format(money(value), "f")


def balance_strings(wallet) -> dict:
    return {
        "balance": _fmt(wallet.balance),
        "locked_balance": _fmt(wallet.locked_balance),
        "pending_withdrawal_balance": _fmt(wallet.pending_withdrawal_balance),
        "available_balance": _fmt(wallet.available_balance),
    }


def get_balances(user) -> dict:
    wallet = get_or_create_wallet(user)
    return {
        "balance": wallet.balance,
        "locked_balance": wallet.locked_balance,
        "pending_withdrawal_balance": wallet.pending_withdrawal_balance,
        "available_balance": wallet.available_balance,
    }


def has_completed_deposit(user) -> bool:
    return Deposit.objects.filter(user=user, status=DepositStatus.COMPLETED).exists()


def _existing_entry(entry_type, reference):
    if not reference:
        return None
    return WalletTransaction.objects.filter(entry_type=entry_type, reference=reference).first()


def _apply(wallet, entry_type, amount, *, balance=ZERO, locked=ZERO, pending=ZERO,
           reference="", description="") -> WalletTransaction:
    """Apply bucket deltas to a LOCKED wallet and append the ledger row. Never call without the lock."""
    new_balance = money(wallet.balance + balance)
    new_locked = money(wallet.locked_balance + locked)
    new_pending = money(wallet.pending_withdrawal_balance + pending)
    if min(new_balance, new_locked, new_pending) < 0 or new_balance < new_locked + new_pending:
        raise InsufficientFunds(data=balance_strings(wallet))

    wallet.balance, wallet.locked_balance, wallet.pending_withdrawal_balance = new_balance, new_locked, new_pending
    wallet.save(update_fields=["balance", "locked_balance", "pending_withdrawal_balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet,
        user_id=wallet.user_id,
        entry_type=entry_type,
        amount=money(amount),
        balance_delta=money(balance),
        locked_delta=money(locked),
        pending_delta=money(pending),
        balance_after=new_balance,
        locked_after=new_locked,
        pending_after=new_pending,
        reference=reference,
        description=(description or "")[:255],
    )


# --------------------------------------------------------------------------- #
# Public contract used by other apps
# --------------------------------------------------------------------------- #
_CREDIT_TYPES = {EntryType.DEPOSIT, EntryType.REFERRAL_BONUS, EntryType.ADJUSTMENT}


def credit_wallet(user, amount, entry_type, reference="", description="") -> WalletTransaction:
    amount = money(amount)
    if amount <= 0:
        raise BusinessError("invalid_amount", "Amount must be greater than zero.")
    if entry_type not in _CREDIT_TYPES:
        raise ValueError(f"credit_wallet does not support entry_type={entry_type!r}")
    with transaction.atomic():
        wallet = _locked_wallet(user)
        existing = _existing_entry(entry_type, reference)
        if existing is not None:
            return existing
        return _apply(wallet, entry_type, amount, balance=amount, reference=reference, description=description)


def lock_funds(user, amount, reference) -> WalletTransaction:
    amount = money(amount)
    if amount <= 0:
        raise BusinessError("invalid_amount", "Amount must be greater than zero.")
    with transaction.atomic():
        wallet = _locked_wallet(user)
        existing = _existing_entry(EntryType.TRADE_LOCK, reference)
        if existing is not None:
            return existing
        if wallet.available_balance < amount:
            raise InsufficientFunds(
                "Insufficient available balance to open this trade.", data=balance_strings(wallet)
            )
        return _apply(
            wallet, EntryType.TRADE_LOCK, amount, locked=amount,
            reference=reference, description="Margin locked for trade",
        )


def settle_trade(user, principal, pnl, reference) -> None:
    principal = money(principal)
    pnl = max(money(pnl), -principal)  # a trade can never lose more than its margin
    with transaction.atomic():
        wallet = _locked_wallet(user)
        if _existing_entry(EntryType.TRADE_SETTLE, reference) is not None:
            return
        if wallet.locked_balance < principal:
            raise BusinessError(
                "invalid_settlement", "Locked balance is lower than the trade principal.", status=400,
                data=balance_strings(wallet),
            )
        _apply(
            wallet, EntryType.TRADE_SETTLE, principal, balance=pnl, locked=-principal,
            reference=reference, description=f"Trade settled (P/L {_fmt(pnl)} USD)",
        )


# --------------------------------------------------------------------------- #
# Config / rate helpers
# --------------------------------------------------------------------------- #
def _rate() -> Decimal:
    rate = Decimal(str(settings.USDT_USD_RATE)).quantize(RATE_Q)
    if rate <= 0:
        raise BusinessError("rate_unavailable", "Exchange rate is not configured.", status=503)
    return rate


def _deposit_address(network) -> str:
    return (getattr(settings, f"DEPOSIT_ADDRESS_{str(network).upper()}", "") or "").strip()


def get_config() -> dict:
    return {
        "rate": format(_rate(), "f"),
        "currency": "USD",
        "min_deposit_usd": _fmt(settings.MIN_DEPOSIT_AMOUNT),
        "min_withdrawal_usd": _fmt(settings.MIN_WITHDRAWAL_AMOUNT),
        "networks": [
            {
                "value": value,
                "label": str(label),
                "deposit_address": _deposit_address(value) or None,
                "address_regex": ADDRESS_REGEX[value],
            }
            for value, label in Network.choices
        ],
        "withdrawal_request_expiry_minutes": settings.PENDING_TRANSACTION_EXPIRY_MINUTES,
    }


def _new_reference(model, prefix) -> str:
    for _ in range(10):
        ref = generate_reference(prefix)
        if not model.objects.filter(reference=ref).exists():
            return ref
    raise BusinessError("server_error", "Could not generate a reference. Please retry.", status=500)


# --------------------------------------------------------------------------- #
# Deposits (user side)
# --------------------------------------------------------------------------- #
def create_deposit(user, network, usdt_amount, tx_hash="") -> Deposit:
    address = _deposit_address(network)
    if not address:
        raise BusinessError(
            "deposit_unavailable",
            "Deposits on this network are temporarily unavailable. Please try another network later.",
            status=503,
        )

    usdt_amount = to_usdt(usdt_amount)
    rate = _rate()
    usd_amount = money(usdt_amount * rate)
    min_deposit = money(settings.MIN_DEPOSIT_AMOUNT)
    if usd_amount < min_deposit:
        raise BusinessError(
            "deposit_below_minimum",
            f"The minimum deposit is {_fmt(min_deposit)} USD.",
            data={"min_deposit_usd": _fmt(min_deposit)},
        )

    tx_hash = (tx_hash or "").strip().lower()
    try:
        with transaction.atomic():
            _locked_wallet(user)  # per-user mutex so the pending cap can't be raced
            pending = Deposit.objects.filter(user=user, status=DepositStatus.PENDING).count()
            if pending >= settings.WALLET_MAX_PENDING_DEPOSITS:
                raise BusinessError(
                    "too_many_pending_deposits",
                    "You have too many pending deposits. Please wait for them to be processed or cancel one.",
                    status=409,
                    data={"max_pending_deposits": settings.WALLET_MAX_PENDING_DEPOSITS},
                )
            if tx_hash and Deposit.objects.filter(
                tx_hash=tx_hash, status__in=[DepositStatus.PENDING, DepositStatus.COMPLETED]
            ).exists():
                raise BusinessError(
                    "tx_hash_already_used", "This transaction hash has already been submitted.", status=409
                )
            with transaction.atomic():
                deposit = Deposit.objects.create(
                    user=user,
                    reference=_new_reference(Deposit, "DEP-"),
                    network=network,
                    usdt_amount=usdt_amount,
                    rate=rate,
                    usd_amount=usd_amount,
                    deposit_address=address,
                    tx_hash=tx_hash,
                )
            transaction.on_commit(lambda: emails.send_deposit_requested_email(user, deposit))
            transaction.on_commit(lambda: emails.send_admin_new_deposit_email(deposit))
    except IntegrityError:
        raise BusinessError(
            "tx_hash_already_used", "This transaction hash has already been submitted.", status=409
        )
    return deposit


def cancel_deposit(user, deposit_id) -> Deposit:
    with transaction.atomic():
        deposit = Deposit.objects.select_for_update().filter(pk=deposit_id, user=user).first()
        if deposit is None:
            raise _not_found()
        if deposit.status != DepositStatus.PENDING:
            raise _conflict(deposit.status, "Only pending deposits can be cancelled.")
        deposit.status = DepositStatus.CANCELLED
        deposit.save(update_fields=["status", "updated_at"])
    return deposit


# --------------------------------------------------------------------------- #
# Deposits (admin side)
# --------------------------------------------------------------------------- #
def complete_deposit(deposit, admin_user) -> None:
    with transaction.atomic():
        wallet = _locked_wallet(deposit.user)
        locked = Deposit.objects.select_for_update().get(pk=deposit.pk)
        if locked.status != DepositStatus.PENDING:
            return
        reference = f"deposit:{locked.pk}"
        if _existing_entry(EntryType.DEPOSIT, reference) is None:
            _apply(
                wallet, EntryType.DEPOSIT, locked.usd_amount, balance=locked.usd_amount,
                reference=reference, description=f"Deposit {locked.reference}",
            )
        locked.status = DepositStatus.COMPLETED
        locked.processed_by = admin_user
        locked.processed_at = timezone.now()
        locked.save(update_fields=["status", "processed_by", "processed_at", "updated_at"])
        transaction.on_commit(lambda: emails.send_deposit_approved_email(locked.user, locked))
    deposit.refresh_from_db()


def decline_deposit(deposit, admin_user, reason="") -> None:
    with transaction.atomic():
        locked = Deposit.objects.select_for_update().get(pk=deposit.pk)
        if locked.status != DepositStatus.PENDING:
            return
        locked.status = DepositStatus.DECLINED
        locked.decline_reason = (reason or "").strip()
        locked.processed_by = admin_user
        locked.processed_at = timezone.now()
        locked.save(update_fields=["status", "decline_reason", "processed_by", "processed_at", "updated_at"])
        transaction.on_commit(lambda: emails.send_deposit_rejected_email(locked.user, locked))
    deposit.refresh_from_db()


# --------------------------------------------------------------------------- #
# Withdrawals (user side)
# --------------------------------------------------------------------------- #
def validate_address(network, address) -> str:
    address = (address or "").strip()
    if not re.match(ADDRESS_REGEX[Network(network)], address):
        raise BusinessError(
            "invalid_address", f"Enter a valid {Network(network).name} wallet address.", status=400
        )
    return address


def _check_funds(wallet, amount):
    """Raises insufficient_balance / funds_in_use with balances in `data`."""
    if amount > wallet.balance:
        raise BusinessError(
            "insufficient_balance", "Withdrawal amount exceeds your account balance.",
            data=balance_strings(wallet),
        )
    if amount > wallet.available_balance:
        raise BusinessError(
            "funds_in_use",
            "Part of your balance is in active trades or pending withdrawals and can't be withdrawn.",
            data=balance_strings(wallet),
        )


def _otp_expiry():
    return timezone.now() + timedelta(minutes=settings.PENDING_TRANSACTION_EXPIRY_MINUTES)


def create_withdrawal(user, network, address, usd_amount):
    """Returns (withdrawal, otp_sent). Funds are NOT reserved until the OTP is confirmed."""
    usd_amount = money(usd_amount)
    min_withdrawal = money(settings.MIN_WITHDRAWAL_AMOUNT)
    if usd_amount < min_withdrawal:
        raise BusinessError(
            "withdrawal_below_minimum",
            f"The minimum withdrawal is {_fmt(min_withdrawal)} USD.",
            data={"min_withdrawal_usd": _fmt(min_withdrawal)},
        )
    address = validate_address(network, address)
    rate = _rate()
    otp = generate_otp()
    now = timezone.now()
    expires_at = _otp_expiry()

    with transaction.atomic():
        wallet = _locked_wallet(user)
        _check_funds(wallet, usd_amount)

        Withdrawal.objects.filter(user=user, status=WithdrawalStatus.AWAITING_OTP).update(
            status=WithdrawalStatus.CANCELLED, updated_at=now
        )
        withdrawal = Withdrawal(
            user=user,
            reference=_new_reference(Withdrawal, "WDR-"),
            network=network,
            address=address,
            usd_amount=usd_amount,
            rate=rate,
            usdt_amount=to_usdt(usd_amount / rate),
            status=WithdrawalStatus.AWAITING_OTP,
            otp_expires_at=expires_at,
            otp_last_sent_at=now,
            expires_at=expires_at,
        )
        withdrawal.otp_hash = hash_value(f"{withdrawal.pk}:{otp}")
        withdrawal.save()

    # Sent right after commit (equivalent to on_commit) so the API can report `otp_sent`.
    otp_sent = bool(emails.send_withdrawal_otp_email(user, withdrawal, otp))
    return withdrawal, otp_sent


def confirm_withdrawal(user, withdrawal_id, otp):
    """Returns (withdrawal, balance_strings). State-changing failures (attempt counter,
    expiry) are committed first and the error is raised after the transaction closes."""
    error = None
    with transaction.atomic():
        wallet = _locked_wallet(user)
        w = Withdrawal.objects.select_for_update().filter(pk=withdrawal_id, user=user).first()
        if w is None:
            raise _not_found()
        if w.status != WithdrawalStatus.AWAITING_OTP:
            raise _conflict(w.status, "This withdrawal is no longer awaiting confirmation.")

        now = timezone.now()
        max_attempts = settings.OTP_MAX_ATTEMPTS
        if now > w.expires_at or (w.otp_expires_at and now > w.otp_expires_at):
            w.status = WithdrawalStatus.EXPIRED
            w.save(update_fields=["status", "updated_at"])
            error = BusinessError(
                "withdrawal_expired", "This withdrawal request has expired. Please start a new one."
            )
        elif w.otp_attempts >= max_attempts:
            w.status = WithdrawalStatus.CANCELLED
            w.save(update_fields=["status", "updated_at"])
            error = BusinessError(
                "otp_attempts_exceeded", "Too many incorrect codes. Please start a new withdrawal."
            )
        elif not verify_hash(f"{w.pk}:{otp}", w.otp_hash):
            w.otp_attempts += 1
            if w.otp_attempts >= max_attempts:
                w.status = WithdrawalStatus.CANCELLED
                w.save(update_fields=["otp_attempts", "status", "updated_at"])
                error = BusinessError(
                    "otp_attempts_exceeded", "Too many incorrect codes. Please start a new withdrawal."
                )
            else:
                w.save(update_fields=["otp_attempts", "updated_at"])
                error = BusinessError(
                    "invalid_otp", "The code you entered is incorrect.",
                    data={"attempts_remaining": max_attempts - w.otp_attempts},
                )
        else:
            _check_funds(wallet, w.usd_amount)  # balance may have changed since creation
            _apply(
                wallet, EntryType.WITHDRAWAL_RESERVE, w.usd_amount, pending=w.usd_amount,
                reference=f"withdrawal:{w.pk}", description=f"Withdrawal {w.reference} reserved",
            )
            w.status = WithdrawalStatus.PENDING
            w.confirmed_at = now
            w.otp_hash = ""  # single use
            w.save(update_fields=["status", "confirmed_at", "otp_hash", "updated_at"])
            transaction.on_commit(lambda: emails.send_withdrawal_requested_email(w.user, w))
            transaction.on_commit(lambda: emails.send_admin_new_withdrawal_email(w))

    if error is not None:
        raise error
    return w, balance_strings(wallet)


def resend_withdrawal_otp(user, withdrawal_id):
    """Returns otp_sent (bool)."""
    error = None
    otp = None
    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().filter(pk=withdrawal_id, user=user).first()
        if w is None:
            raise _not_found()
        if w.status != WithdrawalStatus.AWAITING_OTP:
            raise _conflict(w.status, "This withdrawal is no longer awaiting confirmation.")

        now = timezone.now()
        if now > w.expires_at:
            w.status = WithdrawalStatus.EXPIRED
            w.save(update_fields=["status", "updated_at"])
            error = BusinessError(
                "withdrawal_expired", "This withdrawal request has expired. Please start a new one."
            )
        elif w.otp_resend_count >= settings.WALLET_MAX_OTP_RESENDS:
            raise BusinessError(
                "otp_resend_limit", "You have reached the resend limit. Please start a new withdrawal.", status=429
            )
        else:
            if w.otp_last_sent_at:
                wait = settings.OTP_RESEND_COOLDOWN_SECONDS - (now - w.otp_last_sent_at).total_seconds()
                if wait > 0:
                    raise BusinessError(
                        "otp_cooldown", "Please wait before requesting another code.", status=429,
                        data={"retry_after_seconds": int(math.ceil(wait))},
                    )
            otp = generate_otp()
            expiry = _otp_expiry()
            w.otp_hash = hash_value(f"{w.pk}:{otp}")  # old code is now invalid
            w.otp_attempts = 0
            w.otp_resend_count += 1
            w.otp_last_sent_at = now
            w.otp_expires_at = expiry
            w.expires_at = expiry
            w.save(update_fields=[
                "otp_hash", "otp_attempts", "otp_resend_count", "otp_last_sent_at",
                "otp_expires_at", "expires_at", "updated_at",
            ])

    if error is not None:
        raise error
    return bool(emails.send_withdrawal_otp_email(user, w, otp))


def cancel_withdrawal(user, withdrawal_id) -> Withdrawal:
    with transaction.atomic():
        w = Withdrawal.objects.select_for_update().filter(pk=withdrawal_id, user=user).first()
        if w is None:
            raise _not_found()
        if w.status != WithdrawalStatus.AWAITING_OTP:
            raise _conflict(w.status, "Only withdrawals awaiting confirmation can be cancelled.")
        w.status = WithdrawalStatus.CANCELLED
        w.otp_hash = ""
        w.save(update_fields=["status", "otp_hash", "updated_at"])
    return w


# --------------------------------------------------------------------------- #
# Withdrawals (admin side)
# --------------------------------------------------------------------------- #
def complete_withdrawal(withdrawal, admin_user, tx_hash="") -> None:
    with transaction.atomic():
        wallet = _locked_wallet(withdrawal.user)
        w = Withdrawal.objects.select_for_update().get(pk=withdrawal.pk)
        if w.status != WithdrawalStatus.PENDING:
            return
        reference = f"withdrawal:{w.pk}"
        if _existing_entry(EntryType.WITHDRAWAL, reference) is None:
            _apply(
                wallet, EntryType.WITHDRAWAL, w.usd_amount,
                balance=-w.usd_amount, pending=-w.usd_amount,
                reference=reference, description=f"Withdrawal {w.reference} completed",
            )
        w.status = WithdrawalStatus.COMPLETED
        w.admin_tx_hash = (tx_hash or "").strip()[:100]
        w.processed_by = admin_user
        w.processed_at = timezone.now()
        w.save(update_fields=["status", "admin_tx_hash", "processed_by", "processed_at", "updated_at"])
        transaction.on_commit(lambda: emails.send_withdrawal_completed_email(w.user, w))
    withdrawal.refresh_from_db()


def fail_withdrawal(withdrawal, admin_user, reason) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("failure_reason_required", "A failure reason is required.")
    with transaction.atomic():
        wallet = _locked_wallet(withdrawal.user)
        w = Withdrawal.objects.select_for_update().get(pk=withdrawal.pk)
        if w.status != WithdrawalStatus.PENDING:
            return
        reference = f"withdrawal:{w.pk}"
        if _existing_entry(EntryType.WITHDRAWAL_RELEASE, reference) is None:
            _apply(
                wallet, EntryType.WITHDRAWAL_RELEASE, w.usd_amount, pending=-w.usd_amount,
                reference=reference, description=f"Withdrawal {w.reference} failed - funds released",
            )
        w.status = WithdrawalStatus.FAILED
        w.failure_reason = reason
        w.processed_by = admin_user
        w.processed_at = timezone.now()
        w.save(update_fields=["status", "failure_reason", "processed_by", "processed_at", "updated_at"])
        transaction.on_commit(lambda: emails.send_withdrawal_failed_email(w.user, w))
    withdrawal.refresh_from_db()


# --------------------------------------------------------------------------- #
# Cron
# --------------------------------------------------------------------------- #
def expire_pending_withdrawals() -> int:
    """Expire up to CRON_BATCH_SIZE unconfirmed requests. No money moves. Idempotent."""
    now = timezone.now()
    with transaction.atomic():
        ids = list(
            Withdrawal.objects.select_for_update(skip_locked=True)
            .filter(status=WithdrawalStatus.AWAITING_OTP, expires_at__lte=now)
            .order_by("expires_at")
            .values_list("pk", flat=True)[:CRON_BATCH_SIZE]
        )
        if not ids:
            return 0
        return Withdrawal.objects.filter(pk__in=ids, status=WithdrawalStatus.AWAITING_OTP).update(
            status=WithdrawalStatus.EXPIRED, otp_hash="", updated_at=now
        )
