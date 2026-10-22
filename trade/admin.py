from django.contrib import admin, messages

from .models import Trade, TradeBlock, TradeProfile
from .services import close_trade


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
class TradeAdmin(ReadOnlyAdmin):
    list_display = (
        "reference", "user_email", "pair", "side", "amount", "duration_hours",
        "status", "outcome", "pnl_amount", "scheduled_close_at",
    )
    list_filter = ("status", "outcome", "market_type", "side", "close_reason", "opened_at")
    search_fields = ("reference", "user__email", "pair")
    date_hierarchy = "opened_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["close_selected_now"]

    @admin.display(description="User", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    def has_close_permission(self, request):
        return request.user.is_active and request.user.is_staff

    @admin.action(description="Close selected open trades now", permissions=["close"])
    def close_selected_now(self, request, queryset):
        closed = skipped = failed = 0
        for trade_id in queryset.values_list("id", flat=True):
            try:
                if close_trade(trade_id, Trade.CloseReason.EXPIRY) is None:
                    skipped += 1
                else:
                    closed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.message_user(request, f"Could not close {trade_id}: {exc}", messages.ERROR)
        self.message_user(request, f"Closed {closed}, skipped {skipped} (already closed), failed {failed}.")


@admin.register(TradeBlock)
class TradeBlockAdmin(ReadOnlyAdmin):
    list_display = ("user", "block_index", "created_at")
    search_fields = ("user__email",)
    ordering = ("-created_at",)


@admin.register(TradeProfile)
class TradeProfileAdmin(ReadOnlyAdmin):
    list_display = ("user", "trades_opened_count", "updated_at")
    search_fields = ("user__email",)
    ordering = ("-created_at",)
