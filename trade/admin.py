from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.safestring import mark_safe

from core.exceptions import BusinessError

from .models import Trade, TradeProfile
from .services import MAX_PROFIT, settle_trade_by_admin

AMOUNT_MSG = "Enter a positive amount with at most 2 decimal places."


# --------------------------------------------------------------------------- list filters
class StateFilter(admin.SimpleListFilter):
    """Status filter that defaults to OPEN so staff land on trades waiting for action."""

    title = "status"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return (("open", "Open (waiting)"), ("closed", "Closed"), ("all", "All"))

    def value(self):
        return super().value()

    def queryset(self, request, queryset):
        value = self.value()
        if value is None or value == "open":
            return queryset.filter(status=Trade.Status.OPEN)
        if value == "closed":
            return queryset.filter(status=Trade.Status.CLOSED)
        return queryset

    def choices(self, changelist):
        current = self.value() or "open"
        for lookup, title in self.lookup_choices:
            yield {
                "selected": current == str(lookup),
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }


class OverdueFilter(admin.SimpleListFilter):
    title = "overdue"
    parameter_name = "overdue"

    def lookups(self, request, model_admin):
        return (("yes", "Overdue (time is up, not settled)"),)

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(status=Trade.Status.OPEN, scheduled_close_at__lte=timezone.now())
        return queryset


# --------------------------------------------------------------------------- settle form
class TradeSettleForm(forms.ModelForm):
    """Non-model fields settle_outcome / settle_amount; the model field admin_note is saved by the service."""

    settle_outcome = forms.ChoiceField(
        label="Result",
        required=False,
        choices=[("", "---------"), ("profit", "Profit"), ("loss", "Loss")],
    )
    settle_amount = forms.DecimalField(
        label="Amount (USD)",
        required=False,
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Size of the profit or of the loss, as a positive number (e.g. 12.50).",
        error_messages={
            "invalid": AMOUNT_MSG,
            "min_value": AMOUNT_MSG,
            "max_decimal_places": AMOUNT_MSG,
            "max_digits": AMOUNT_MSG,
            "max_whole_digits": AMOUNT_MSG,
        },
    )

    class Meta:
        model = Trade
        fields = ("admin_note",)

    def clean(self):
        cleaned = super().clean()
        trade = self.instance
        if not trade.pk:
            return cleaned

        # Re-check against the database: someone else may have settled it meanwhile.
        current_status = Trade.objects.filter(pk=trade.pk).values_list("status", flat=True).first()
        if current_status != Trade.Status.OPEN:
            raise ValidationError("This trade is already closed.")

        outcome = cleaned.get("settle_outcome")
        amount = cleaned.get("settle_amount")

        if not outcome:
            self.add_error("settle_outcome", "Choose profit or loss.")
        if amount is None:
            if "settle_amount" not in self.errors:
                self.add_error("settle_amount", AMOUNT_MSG)
        elif outcome == Trade.Outcome.LOSS and amount > trade.amount:
            self.add_error("settle_amount", f"A loss cannot be larger than the trade amount (USD {trade.amount}).")
        elif outcome == Trade.Outcome.PROFIT and amount > MAX_PROFIT:
            self.add_error("settle_amount", "Profit is above the allowed maximum.")
        return cleaned


# --------------------------------------------------------------------------- admins
class ReadOnlyAdmin(admin.ModelAdmin):
    """Fully read-only: no add / change / delete."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    form = TradeSettleForm
    list_display = (
        "reference", "user_email", "pair", "side", "amount", "duration_hours", "status",
        "awaiting_settlement_display", "outcome", "pnl_amount", "opened_at", "scheduled_close_at", "settled_by",
    )
    list_filter = (StateFilter, OverdueFilter, "outcome", "market_type", "side", "close_reason", "opened_at")
    search_fields = ("reference", "user__email", "pair")
    date_hierarchy = "opened_at"
    list_select_related = ("user", "settled_by")

    _CUSTOM_READONLY = ("user_email", "current_balance", "awaiting_settlement_display")

    # ---- list helpers
    @admin.display(description="User", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Awaiting settlement")
    def awaiting_settlement_display(self, obj):
        if obj.status == Trade.Status.OPEN and obj.scheduled_close_at <= timezone.now():
            # Static HTML with nothing to interpolate: use mark_safe (format_html needs args in Django 5+).
            return mark_safe('<span style="color:#dc2626;font-weight:700;">OVERDUE</span>')
        if obj.status == Trade.Status.OPEN:
            return "Running"
        return "—"

    @admin.display(description="User's current balance (USD)")
    def current_balance(self, obj):
        try:
            from wallet import services as wallet_services

            return f"{wallet_services.get_balances(obj.user)['balance']:,.2f}"
        except Exception:
            return "unavailable"

    # ---- ordering: open trades by most overdue first
    def get_ordering(self, request):
        if request.GET.get("state") in ("closed", "all"):
            return ("-opened_at",)
        return ("scheduled_close_at",)

    # ---- permissions
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Closed trades are final: view-only.
        if obj is not None and obj.status == Trade.Status.CLOSED:
            return False
        return super().has_change_permission(request, obj)

    # ---- fields
    def get_readonly_fields(self, request, obj=None):
        names = [f.name for f in self.model._meta.fields]
        if obj is not None and obj.status == Trade.Status.OPEN:
            names.remove("admin_note")  # the only editable model field, and only while open
        return names + list(self._CUSTOM_READONLY)

    def get_fieldsets(self, request, obj=None):
        summary = (
            "Trade",
            {
                "fields": (
                    "reference", "user_email", "current_balance", "pair", "market_type", "side", "amount",
                    "leverage", "take_profit_pct", "stop_loss_pct", "duration_hours", "status",
                    "opened_at", "scheduled_close_at", "awaiting_settlement_display",
                )
            },
        )
        if obj is None:
            return (summary,)
        if obj.status == Trade.Status.OPEN:
            return (
                summary,
                ("Settle this trade", {"fields": ("settle_outcome", "settle_amount", "admin_note")}),
            )
        return (
            summary,
            (
                "Result",
                {"fields": ("outcome", "pnl_amount", "pnl_pct", "closed_at", "close_reason", "settled_by", "admin_note")},
            ),
        )

    # ---- saving goes ONLY through the service
    def save_model(self, request, obj, form, change):
        if not change or obj.status != Trade.Status.OPEN:
            return
        outcome = form.cleaned_data["settle_outcome"]
        amount = form.cleaned_data["settle_amount"]
        note = form.cleaned_data.get("admin_note", "")
        try:
            result = settle_trade_by_admin(obj.pk, request.user, outcome, amount, note)
        except BusinessError as exc:
            self.message_user(request, getattr(exc, "message", str(exc)), messages.ERROR)
            return
        if result is None:
            self.message_user(request, "This trade was already closed.", messages.ERROR)
            return
        self.message_user(
            request,
            f"Trade settled: {outcome} of USD {amount:,.2f}. Wallet updated.",
            messages.SUCCESS,
        )


@admin.register(TradeProfile)
class TradeProfileAdmin(ReadOnlyAdmin):
    list_display = ("user", "trades_opened_count", "updated_at")
    search_fields = ("user__email",)
    ordering = ("-created_at",)