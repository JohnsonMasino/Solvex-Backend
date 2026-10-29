import re
import secrets
import string

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from core.models import TimeStampedModel, UUIDModel

def _check(condition, name):
    """CheckConstraint compatible with Django 5.0 (check=) and 5.1+/6.x (condition=)."""
    try:
        return models.CheckConstraint(condition=condition, name=name)
    except TypeError:  # Django < 5.1
        return models.CheckConstraint(check=condition, name=name)


_REF_ALPHABET = string.ascii_uppercase + string.digits


def generate_reference(prefix: str) -> str:
    return prefix + "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))


# --------------------------------------------------------------------------- #
# Choices
# --------------------------------------------------------------------------- #
class Network(models.TextChoices):
    BEP20 = "bep20", "USDT (BEP20 – BNB Smart Chain)"
    ERC20 = "erc20", "USDT (ERC20 – Ethereum)"
    TRC20 = "trc20", "USDT (TRC20 – TRON)"
    BTC = "btc", "BTC (Bitcoin)"
    ETH = "eth", "ETH (Ethereum)"


class Asset(models.TextChoices):
    USDT = "usdt", "USDT"
    BTC = "btc", "BTC"
    ETH = "eth", "ETH"


# Each network carries exactly one asset.
NETWORK_ASSET = {
    Network.BEP20: Asset.USDT,
    Network.ERC20: Asset.USDT,
    Network.TRC20: Asset.USDT,
    Network.BTC: Asset.BTC,
    Network.ETH: Asset.ETH,
}

# Max decimal places we accept / store per asset.
ASSET_DECIMALS = {Asset.USDT: 6, Asset.BTC: 8, Asset.ETH: 8}

# Withdrawals are still paid out in USDT only.
USDT_NETWORKS = (Network.BEP20, Network.ERC20, Network.TRC20)
WITHDRAWAL_NETWORK_CHOICES = [(n.value, n.label) for n in USDT_NETWORKS]

# Address format per network (used for withdrawal addresses AND the admin-managed deposit addresses).
ADDRESS_REGEX = {
    Network.BEP20: r"^0x[a-fA-F0-9]{40}$",
    Network.ERC20: r"^0x[a-fA-F0-9]{40}$",
    Network.TRC20: r"^T[1-9A-HJ-NP-Za-km-z]{33}$",
    Network.BTC: r"^(bc1[02-9ac-hj-np-z]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$",
    Network.ETH: r"^0x[a-fA-F0-9]{40}$",
}

# Which DepositAddressSettings column holds the company address of each network.
ADDRESS_FIELDS = {
    Network.BEP20: "bep20_address",
    Network.ERC20: "erc20_address",
    Network.TRC20: "trc20_address",
    Network.BTC: "btc_address",
    Network.ETH: "eth_address",
}


def asset_for_network(network) -> "Asset":
    return NETWORK_ASSET[Network(network)]


class EntryType(models.TextChoices):
    DEPOSIT = "deposit", "Deposit"
    WITHDRAWAL_RESERVE = "withdrawal_reserve", "Withdrawal reserved"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    WITHDRAWAL_RELEASE = "withdrawal_release", "Withdrawal released"
    TRADE_LOCK = "trade_lock", "Trade margin locked"
    TRADE_SETTLE = "trade_settle", "Trade settled"
    REFERRAL_BONUS = "referral_bonus", "Referral bonus"
    ADJUSTMENT = "adjustment", "Adjustment"
    LOAN_DISBURSEMENT = "loan_disbursement", "Loan disbursed"
    LOAN_REPAYMENT = "loan_repayment", "Loan repayment"


class DepositStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"


class WithdrawalStatus(models.TextChoices):
    AWAITING_OTP = "awaiting_otp", "Awaiting OTP"
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


# --------------------------------------------------------------------------- #
# Company deposit addresses (managed in the admin console, replaces the env vars)
# --------------------------------------------------------------------------- #
class DepositAddressSettings(UUIDModel, TimeStampedModel):
    """Singleton row holding the company deposit address of every network.

    Edit it in the admin ("Deposit addresses"). Changes go live immediately, no redeploy needed.
    A blank address disables deposits on that network. Deposits that already exist keep the
    address that was shown to the user when they were created.
    """

    bep20_address = models.CharField("USDT (BEP20 – BNB Smart Chain) address", max_length=100, blank=True)
    erc20_address = models.CharField("USDT (ERC20 – Ethereum) address", max_length=100, blank=True)
    trc20_address = models.CharField("USDT (TRC20 – TRON) address", max_length=100, blank=True)
    btc_address = models.CharField("BTC (Bitcoin) address", max_length=100, blank=True)
    eth_address = models.CharField("ETH (Ethereum) address", max_length=100, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "Deposit addresses"
        verbose_name_plural = "Deposit addresses"

    def __str__(self):
        return "Company deposit addresses"

    @classmethod
    def load(cls) -> "DepositAddressSettings":
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    def address_for(self, network) -> str:
        return (getattr(self, ADDRESS_FIELDS[Network(network)]) or "").strip()

    def clean(self):
        errors = {}
        for network, field in ADDRESS_FIELDS.items():
            value = (getattr(self, field) or "").strip()
            setattr(self, field, value)
            if value and not re.match(ADDRESS_REGEX[network], value):
                errors[field] = f"This is not a valid {network.label} address."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self._state.adding and DepositAddressSettings.objects.exists():
            raise RuntimeError("Only one DepositAddressSettings row is allowed. Edit the existing one.")
        super().save(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Wallet
# --------------------------------------------------------------------------- #
class Wallet(UUIDModel, TimeStampedModel):
    # CASCADE so a brand-new user can be deleted; users with ledger history are protected
    # because WalletTransaction.wallet / .user are PROTECT.
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    locked_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    pending_withdrawal_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        constraints = [
            _check(Q(balance__gte=0), "wallet_balance_gte_0"),
            _check(Q(locked_balance__gte=0), "wallet_locked_gte_0"),
            _check(Q(pending_withdrawal_balance__gte=0), "wallet_pending_gte_0"),
            _check(
                Q(balance__gte=F("locked_balance") + F("pending_withdrawal_balance")),
                "wallet_balance_covers_locked_and_pending",
            ),
        ]

    def __str__(self):
        return f"Wallet<{self.user_id}> {self.balance}"

    @property
    def available_balance(self):
        return max(self.balance - self.locked_balance - self.pending_withdrawal_balance, 0)


class WalletTransaction(UUIDModel, TimeStampedModel):
    """Append-only ledger."""

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet_transactions")
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    balance_delta = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    locked_delta = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    pending_delta = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    balance_after = models.DecimalField(max_digits=18, decimal_places=2)
    locked_after = models.DecimalField(max_digits=18, decimal_places=2)
    pending_after = models.DecimalField(max_digits=18, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["entry_type", "reference"],
                condition=~Q(reference=""),
                name="wallet_tx_unique_ref",
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="wallet_tx_user_created_idx")]

    def __str__(self):
        return f"{self.entry_type} {self.amount} ({self.reference})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Ledger entries are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Ledger entries cannot be deleted.")


# --------------------------------------------------------------------------- #
# Deposit
# --------------------------------------------------------------------------- #
class Deposit(UUIDModel, TimeStampedModel):
    Status = DepositStatus

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="deposits")
    reference = models.CharField(max_length=20, unique=True)
    network = models.CharField(max_length=10, choices=Network.choices)
    asset = models.CharField(max_length=10, choices=Asset.choices, default=Asset.USDT)
    amount = models.DecimalField(
        max_digits=24, decimal_places=8,
        help_text="Amount in the asset's own units (USDT / BTC / ETH).",
    )
    rate = models.DecimalField(max_digits=18, decimal_places=6, help_text="USD per 1 unit of the asset, frozen at creation.")
    usd_amount = models.DecimalField(max_digits=18, decimal_places=2, help_text="Frozen: amount x rate.")
    deposit_address = models.CharField(max_length=100, help_text="Company address shown to the user.")
    tx_hash = models.CharField(max_length=100, blank=True, help_text="Optional user-supplied proof.")
    status = models.CharField(max_length=10, choices=DepositStatus.choices, default=DepositStatus.PENDING, db_index=True)
    decline_reason = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)

    # ---- payment proof (Cloudinary). Only the public_id is stored; URLs are generated on demand
    # (signed, because the image is uploaded as a private "authenticated" asset). See core/images.py.
    screenshot_public_id = models.CharField(
        max_length=255, blank=True,
        help_text="Cloudinary public_id of the payment screenshot uploaded by the user.",
    )
    screenshot_uploaded_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the user clicked 'I have paid'. Once set, the screenshot can no longer be changed.",
    )

    transaction_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Admin override of the transaction date/time. Leave as shown to keep the actual time.",
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # One live deposit per tx hash (race-safe backstop for the service-level check).
            models.UniqueConstraint(
                fields=["tx_hash"],
                condition=~Q(tx_hash="") & Q(status__in=["pending", "completed"]),
                name="wallet_deposit_unique_live_txhash",
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="wallet_dep_user_created_idx")]

    def __str__(self):
        return f"{self.reference} {self.amount} {str(self.asset).upper()} = {self.usd_amount} USD ({self.status})"

    @property
    def effective_at(self):
        """The date/time shown to the user: the admin override if set, else the real creation time."""
        return self.transaction_at or self.created_at

    @property
    def has_screenshot(self) -> bool:
        return bool(self.screenshot_public_id)

    @property
    def usdt_amount(self):
        """Deprecated alias kept so old email templates / code that read `deposit.usdt_amount`
        don't crash. Prefer `amount` + `asset`."""
        return self.amount


# --------------------------------------------------------------------------- #
# Withdrawal
# --------------------------------------------------------------------------- #
class Withdrawal(UUIDModel, TimeStampedModel):
    """
    Money model (all frozen at creation, admin can edit while the withdrawal is `pending`):

        usd_amount   USD value the user RECEIVES (paid out in USDT)
        fee_percent  fee rate applied (e.g. 1.50)
        fee_usd      usd_amount x fee_percent / 100, rounded to cents
        total_usd    usd_amount + fee_usd  -> the amount reserved, then removed from the user's balance
        rate         live USD per 1 USDT at creation
        usdt_amount  usd_amount / rate     -> exact USDT the admin must send
    """

    Status = WithdrawalStatus

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="withdrawals")
    reference = models.CharField(max_length=20, unique=True)
    network = models.CharField(max_length=10, choices=WITHDRAWAL_NETWORK_CHOICES)
    address = models.CharField(max_length=100)
    usd_amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fee_usd = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_usd = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    usdt_amount = models.DecimalField(max_digits=24, decimal_places=6, help_text="Exact USDT the admin must send.")
    status = models.CharField(
        max_length=15, choices=WithdrawalStatus.choices, default=WithdrawalStatus.AWAITING_OTP, db_index=True
    )

    otp_hash = models.CharField(max_length=64, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_last_sent_at = models.DateTimeField(null=True, blank=True)
    otp_resend_count = models.PositiveSmallIntegerField(default=0)

    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    admin_tx_hash = models.CharField(max_length=100, blank=True)
    failure_reason = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    transaction_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Admin override of the transaction date/time. Leave as shown to keep the actual time.",
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "-created_at"], name="wallet_wd_user_created_idx"),
            models.Index(fields=["status", "expires_at"], name="wallet_wd_status_expires_idx"),
        ]

    def __str__(self):
        return f"{self.reference} {self.usd_amount} USD + {self.fee_usd} fee ({self.status})"

    @property
    def effective_at(self):
        """The date/time shown to the user: the admin override if set, else the real creation time."""
        return self.transaction_at or self.created_at