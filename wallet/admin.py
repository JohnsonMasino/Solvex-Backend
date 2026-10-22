from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from core.exceptions import BusinessError

from . import services
from .models import (
    Deposit, DepositStatus, Wallet, WalletTransaction, Withdrawal, WithdrawalStatus,
)


def _err_text(exc):
    return getattr(exc, "message", None) or str(exc)


class ReadOnlyAdmin(admin.ModelAdmin):
    """No add / change / delete: ledger-style models are view-only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# --------------------------------------------------------------------------- #
# Wallet + ledger (view-only)
# --------------------------------------------------------------------------- #
@admin.register(Wallet)
class WalletAdmin(ReadOnlyAdmin):
    list_display = ("user_email", "balance", "locked_balance", "pending_withdrawal_balance", "available", "updated_at")
    search_fields = ("user__email",)
    ordering = ("-updated_at",)
    list_select_related = ("user",)

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Available")
    def available(self, obj):
        return obj.available_balance


@admin.register(WalletTransaction)
class WalletTransactionAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "user_email", "entry_type", "amount", "balance_delta",
                    "locked_delta", "pending_delta", "balance_after", "reference")
    list_filter = ("entry_type", "created_at")
    search_fields = ("user__email", "reference")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email


# --------------------------------------------------------------------------- #
# Deposits
# --------------------------------------------------------------------------- #
@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    EDITABLE = ("status", "decline_reason", "admin_note")

    list_display = ("reference", "user_email", "network", "usdt_amount", "usd_amount", "status", "created_at")
    list_filter = ("status", "network", "created_at")
    search_fields = ("reference", "user__email", "tx_hash")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["mark_completed", "mark_declined"]

    fieldsets = (
        ("Request", {"fields": (
            "user_email", "reference", "network", "usdt_amount", "rate", "usd_amount",
            "deposit_address", "tx_hash", "created_at",
        )}),
        ("Processing", {"fields": (
            "status", "decline_reason", "admin_note", "processed_by", "processed_at",
        )}),
    )

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = [f.name for f in self.model._meta.fields if f.name not in self.EDITABLE]
        readonly.append("user_email")
        if obj is not None and obj.status != DepositStatus.PENDING:
            readonly += ["status", "decline_reason"]
        return readonly

    def save_model(self, request, obj, form, change):
        if not change:
            return
        current = Deposit.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.decline_reason or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        obj.status = current.status  # never let Django persist the raw form instance

        try:
            if new_status != current.status:
                if current.status != DepositStatus.PENDING or new_status not in (
                    DepositStatus.COMPLETED, DepositStatus.DECLINED
                ):
                    messages.error(request, "Illegal status change. Only pending → completed or pending → declined is allowed.")
                elif new_status == DepositStatus.COMPLETED:
                    services.complete_deposit(current, request.user)
                else:
                    services.decline_deposit(current, request.user, reason)
            elif current.status == DepositStatus.PENDING:
                updates["decline_reason"] = reason  # draft reason
        except BusinessError as exc:
            messages.error(request, _err_text(exc))
        Deposit.objects.filter(pk=obj.pk).update(**updates)

    @admin.action(description="Mark selected completed (credits USD)")
    def mark_completed(self, request, queryset):
        done = skipped = 0
        for dep in queryset.select_related("user"):
            if dep.status != DepositStatus.PENDING:
                skipped += 1
                continue
            services.complete_deposit(dep, request.user)
            done += 1
        if done:
            self.message_user(request, f"{done} deposit(s) completed and credited.", messages.SUCCESS)
        if skipped:
            messages.error(request, f"{skipped} deposit(s) skipped: only pending deposits can be completed.")

    @admin.action(description="Mark selected declined")
    def mark_declined(self, request, queryset):
        done = skipped = 0
        for dep in queryset.select_related("user"):
            if dep.status != DepositStatus.PENDING:
                skipped += 1
                continue
            services.decline_deposit(dep, request.user, dep.decline_reason)
            done += 1
        if done:
            self.message_user(request, f"{done} deposit(s) declined.", messages.SUCCESS)
        if skipped:
            messages.error(request, f"{skipped} deposit(s) skipped: only pending deposits can be declined.")


# --------------------------------------------------------------------------- #
# Withdrawals
# --------------------------------------------------------------------------- #
class WithdrawalStatusFilter(admin.SimpleListFilter):
    """Status filter that defaults to the pending queue instead of 'All'."""

    title = "status"
    parameter_name = "wd_status"

    def lookups(self, request, model_admin):
        return [("all", "All")] + list(WithdrawalStatus.choices)

    def queryset(self, request, queryset):
        value = self.value()
        if value is None:
            return queryset.filter(status=WithdrawalStatus.PENDING)
        if value == "all":
            return queryset
        return queryset.filter(status=value)

    def choices(self, changelist):
        current = self.value() or WithdrawalStatus.PENDING
        for lookup, title in self.lookup_choices:
            yield {
                "selected": current == lookup,
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    EDITABLE = ("status", "admin_tx_hash", "failure_reason", "admin_note")

    list_display = ("reference", "user_email", "network", "usd_amount", "usdt_amount", "status", "created_at")
    list_filter = (WithdrawalStatusFilter, "network", "created_at")
    search_fields = ("reference", "user__email", "address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["mark_completed", "mark_failed"]

    fieldsets = (
        ("Payout instruction", {"fields": ("send_instruction",)}),
        ("Request", {"fields": (
            "user_email", "reference", "network", "address", "usd_amount", "rate", "usdt_amount",
            "confirmed_at", "expires_at", "created_at",
        )}),
        ("Processing", {"fields": (
            "status", "admin_tx_hash", "failure_reason", "admin_note", "processed_by", "processed_at",
        )}),
    )

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Send exactly")
    def send_instruction(self, obj):
        if obj is None or obj.pk is None:
            return "-"
        return format_html(
            "Send exactly <strong style='font-size:1.25em'>{} USDT</strong> on <strong>{}</strong> to "
            "<code style='user-select:all'>{}</code>",
            format(obj.usdt_amount.normalize(), "f"), obj.get_network_display(), obj.address,
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = [f.name for f in self.model._meta.fields if f.name not in self.EDITABLE]
        readonly += ["user_email", "send_instruction"]
        if obj is not None and obj.status != WithdrawalStatus.PENDING:
            readonly += ["status", "admin_tx_hash", "failure_reason"]
        return readonly

    def save_model(self, request, obj, form, change):
        if not change:
            return
        current = Withdrawal.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.failure_reason or "").strip()
        tx_hash = (obj.admin_tx_hash or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        obj.status = current.status

        try:
            if new_status != current.status:
                if current.status == WithdrawalStatus.AWAITING_OTP:
                    messages.error(request, "This request is still awaiting the user's OTP confirmation and can't be processed.")
                elif current.status != WithdrawalStatus.PENDING or new_status not in (
                    WithdrawalStatus.COMPLETED, WithdrawalStatus.FAILED
                ):
                    messages.error(request, "Illegal status change. Only pending → completed or pending → failed is allowed.")
                elif new_status == WithdrawalStatus.FAILED and not reason:
                    messages.error(request, "A failure reason is required to mark a withdrawal as failed.")
                elif new_status == WithdrawalStatus.COMPLETED:
                    services.complete_withdrawal(current, request.user, tx_hash)
                else:
                    services.fail_withdrawal(current, request.user, reason)
            elif current.status == WithdrawalStatus.PENDING:
                updates["admin_tx_hash"] = tx_hash[:100]  # drafts for the pending row
                updates["failure_reason"] = reason
        except BusinessError as exc:
            messages.error(request, _err_text(exc))
        Withdrawal.objects.filter(pk=obj.pk).update(**updates)

    @admin.action(description="Mark selected completed (deducts USD)")
    def mark_completed(self, request, queryset):
        done = skipped = 0
        for w in queryset.select_related("user"):
            if w.status != WithdrawalStatus.PENDING:
                skipped += 1
                continue
            services.complete_withdrawal(w, request.user, w.admin_tx_hash)
            done += 1
        if done:
            self.message_user(request, f"{done} withdrawal(s) completed.", messages.SUCCESS)
        if skipped:
            messages.error(request, f"{skipped} withdrawal(s) skipped: only pending (OTP-confirmed) withdrawals can be completed.")

    @admin.action(description="Mark selected failed (releases funds)")
    def mark_failed(self, request, queryset):
        done = skipped = missing = 0
        for w in queryset.select_related("user"):
            if w.status != WithdrawalStatus.PENDING:
                skipped += 1
                continue
            if not (w.failure_reason or "").strip():
                missing += 1
                continue
            services.fail_withdrawal(w, request.user, w.failure_reason)
            done += 1
        if done:
            self.message_user(request, f"{done} withdrawal(s) marked failed; funds released.", messages.SUCCESS)
        if missing:
            messages.error(request, f"{missing} withdrawal(s) skipped: add a failure reason on the withdrawal first.")
        if skipped:
            messages.error(request, f"{skipped} withdrawal(s) skipped: only pending (OTP-confirmed) withdrawals can be failed.")
