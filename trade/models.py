import secrets
import string

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel, UUIDModel

# Allowed trade durations (hours). Constant in code, per the app document.
DURATION_CHOICES = (6, 12, 24)

_REF_ALPHABET = string.ascii_uppercase + string.digits


def generate_trade_reference() -> str:
    """TRD- + 8 random uppercase alphanumerics, e.g. TRD-AB12CD34."""
    return "TRD-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))


class Trade(UUIDModel, TimeStampedModel):
    class MarketType(models.TextChoices):
        CRYPTO = "crypto", "Crypto"
        FOREX = "forex", "Forex"

    class Side(models.TextChoices):
        BUY = "buy", "Buy"
        SELL = "sell", "Sell"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class CloseReason(models.TextChoices):
        EXPIRY = "expiry", "Expiry"
        WEEKEND = "weekend", "Weekend"

    class Outcome(models.TextChoices):
        PROFIT = "profit", "Profit"
        LOSS = "loss", "Loss"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="trades")
    reference = models.CharField(max_length=20, unique=True, default=generate_trade_reference, editable=False)
    pair = models.CharField(max_length=21)
    market_type = models.CharField(max_length=10, choices=MarketType.choices)
    side = models.CharField(max_length=4, choices=Side.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2, help_text="Margin used (USD), locked in wallet.")
    leverage = models.PositiveSmallIntegerField()
    take_profit_pct = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    stop_loss_pct = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    duration_hours = models.PositiveSmallIntegerField(choices=[(h, f"{h}h") for h in DURATION_CHOICES])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    opened_at = models.DateTimeField()
    scheduled_close_at = models.DateTimeField(db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=10, choices=CloseReason.choices, null=True, blank=True)

    # Engine bookkeeping (write-once, never exposed while open)
    sequence_number = models.PositiveIntegerField()
    block_index = models.PositiveIntegerField()
    slot_in_block = models.PositiveSmallIntegerField()
    planned_outcome = models.CharField(max_length=10, choices=Outcome.choices)
    planned_pnl_pct = models.DecimalField(max_digits=5, decimal_places=2)

    # Result (filled at close)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, null=True, blank=True)
    pnl_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    pnl_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    idempotency_key = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        ordering = ("-opened_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="uniq_trade_user_idempotency_key",
            ),
            models.UniqueConstraint(fields=["user", "sequence_number"], name="uniq_trade_user_sequence"),
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_close_at"], name="trade_status_close_idx"),
            models.Index(fields=["user", "status"], name="trade_user_status_idx"),
        ]

    def __str__(self):
        return f"{self.reference} {self.pair} {self.side} {self.amount}"


class TradeBlock(UUIDModel, TimeStampedModel):
    """Per-user block of TRADE_BLOCK_SIZE trades with its (secret) loss positions."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trade_blocks")
    block_index = models.PositiveIntegerField()
    loss_slots = models.JSONField(default=list)

    class Meta:
        ordering = ("user", "block_index")
        constraints = [
            models.UniqueConstraint(fields=["user", "block_index"], name="uniq_tradeblock_user_block"),
        ]

    def __str__(self):
        return f"Block {self.block_index} for {self.user_id}"


class TradeProfile(UUIDModel, TimeStampedModel):
    """One row per user; locked (select_for_update) to serialise trade numbering."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trade_profile")
    trades_opened_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"TradeProfile({self.user_id}, {self.trades_opened_count})"
