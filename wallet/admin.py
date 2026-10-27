from django import forms
from django.contrib import admin, messages
from django.db import IntegrityError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.exceptions import BusinessError

from . import services
from .models import (
    Deposit, DepositAddressSettings, DepositStatus, Wallet, WalletTransaction,
    Withdrawal, WithdrawalStatus,
)


def _err_text(exc):
    return getattr(exc, "message", None) or str(exc)


def _collect_edits(form, keys, created_at):
    """Return {field: new value} for the editable fields the admin really changed.

    `transaction_at` is special: the form pre-fills it with the actual time, so an untouched value
    (or one within a second of the actual time) is stored as None = "use the real time".
    Clearing the field also resets it to the real time.
    """
    edits = {}
    for key in keys:
        if key not in form.changed_data:
            continue
        value = form.cleaned_data.get(key)
        if key == "transaction_at":
            if value is None or abs((value - created_at).total_seconds()) < 1:
                value = None
        edits[key] = value
    return edits


class ReadOnlyAdmin(admin.ModelAdmin):
    """No add / change / delete: ledger-style models are view-only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TransactionTimeForm(forms.ModelForm):
    """Pre-fills `transaction_at` with the actual creation time when the admin hasn't overridden it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance.pk and "transaction_at" in self.fields and not instance.transaction_at:
            self.initial["transaction_at"] = instance.created_at


# --------------------------------------------------------------------------- #
# Company deposit addresses (replaces the DEPOSIT_ADDRESS_* env vars)
# --------------------------------------------------------------------------- #
@admin.register(DepositAddressSettings)
class DepositAddressSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Company deposit addresses", {
            "description": (
                "These are the addresses shown to users when they make a deposit. Changes go live "
                "immediately. Leave an address blank to disable deposits on that network. "
                "Deposits that already exist keep the address the user was shown."
            ),
            "fields": ("btc_address", "eth_address", "bep20_address", "erc20_address", "trc20_address"),
        }),
        ("Last change", {"fields": ("updated_by", "updated_at")}),
    )
    readonly_fields = ("updated_by", "updated_at")

    def has_add_permission(self, request):
        return not DepositAddressSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # It's a single settings row: go straight to its edit page.
        obj = DepositAddressSettings.load()
        return HttpResponseRedirect(reverse("admin:wallet_depositaddresssettings_change", args=[obj.pk]))

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


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
class DepositAdminForm(TransactionTimeForm):
    class Meta:
        model = Deposit
        fields = "__all__"


@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    form = DepositAdminForm

    # Editable while the deposit is pending (before the admin approves / declines it).
    EDIT_KEYS = ("network", "amount", "rate", "usd_amount", "deposit_address", "tx_hash", "transaction_at")
    PENDING_EDITABLE = EDIT_KEYS + ("status", "decline_reason", "admin_note")
    ALWAYS_EDITABLE = ("admin_note",)

    list_display = ("reference", "user_email", "network", "asset", "amount", "usd_amount", "status", "effective_time")
    list_filter = ("status", "asset", "network", "created_at")
    search_fields = ("reference", "user__email", "tx_hash")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["mark_completed", "mark_declined"]

    fieldsets = (
        ("Request (editable while pending)", {
            "description": (
                "While the deposit is pending you can change the crypto (network), amount, rate, USD value, "
                "address, tx hash and date/time. If you change the network and leave rate / USD value untouched, "
                "the live rate of the new crypto is fetched and USD value = amount x rate. "
                "Whatever is saved here is what gets credited and shown in the user's history."
            ),
            "fields": (
                "user_email", "reference", "network", "asset", "amount", "rate", "usd_amount",
                "deposit_address", "tx_hash",
            ),
        }),
        ("Date & time", {"fields": ("created_at", "transaction_at", "effective_time")}),
        ("Processing", {"fields": (
            "status", "decline_reason", "admin_note", "processed_by", "processed_at",
        )}),
    )

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Date shown to user")
    def effective_time(self, obj):
        return obj.effective_at

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status == DepositStatus.PENDING:
            editable = set(self.PENDING_EDITABLE)
        else:
            editable = set(self.ALWAYS_EDITABLE)
        readonly = [f.name for f in self.model._meta.fields if f.name not in editable]
        readonly += ["user_email", "effective_time"]
        return readonly

    def save_model(self, request, obj, form, change):
        if not change:
            return
        current = Deposit.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.decline_reason or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        edits = _collect_edits(form, self.EDIT_KEYS, current.created_at) if current.status == DepositStatus.PENDING else {}
        obj.status = current.status  # never let Django persist the raw form instance

        try:
            if edits:
                current = services.admin_update_deposit(current, edits)
                messages.info(
                    request,
                    f"Deposit {current.reference} updated: {current.amount.normalize():f} "
                    f"{str(current.asset).upper()} on {current.network.upper()} = {current.usd_amount} USD.",
                )
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
        except IntegrityError:
            messages.error(request, "That transaction hash is already used by another live deposit.")
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


class WithdrawalAdminForm(TransactionTimeForm):
    class Meta:
        model = Withdrawal
        fields = "__all__"


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    form = WithdrawalAdminForm

    # Editable while the withdrawal is pending (OTP confirmed, before the admin completes / fails it).
    EDIT_KEYS = (
        "network", "address", "usd_amount", "fee_percent", "fee_usd", "total_usd",
        "rate", "usdt_amount", "transaction_at",
    )
    PENDING_EDITABLE = EDIT_KEYS + ("status", "admin_tx_hash", "failure_reason", "admin_note")
    ALWAYS_EDITABLE = ("admin_note",)

    list_display = (
        "reference", "user_email", "network", "usd_amount", "fee_usd", "total_usd",
        "usdt_amount", "status", "effective_time",
    )
    list_filter = (WithdrawalStatusFilter, "network", "created_at")
    search_fields = ("reference", "user__email", "address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["mark_completed", "mark_failed"]

    fieldsets = (
        ("Payout instruction", {"fields": ("send_instruction",)}),
        ("Request (editable while pending)", {
            "description": (
                "While the withdrawal is pending you can change the network, address, amounts, fee and rate. "
                "Leave the derived values untouched and they are recalculated: fee = amount x fee %, "
                "total = amount + fee, USDT = amount / rate. If the total changes, the reserved funds are "
                "adjusted (an 'adjustment' entry is added to the ledger). Completing it removes the new total."
            ),
            "fields": (
                "user_email", "reference", "network", "address",
                "usd_amount", "fee_percent", "fee_usd", "total_usd",
                "rate", "usdt_amount",
                "confirmed_at", "expires_at",
            ),
        }),
        ("Date & time", {"fields": ("created_at", "transaction_at", "effective_time")}),
        ("Processing", {"fields": (
            "status", "admin_tx_hash", "failure_reason", "admin_note", "processed_by", "processed_at",
        )}),
    )

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Date shown to user")
    def effective_time(self, obj):
        return obj.effective_at

    @admin.display(description="Send exactly")
    def send_instruction(self, obj):
        if obj is None or obj.pk is None:
            return "-"
        total = obj.total_usd if obj.total_usd else obj.usd_amount
        return format_html(
            "Send exactly <strong style='font-size:1.25em'>{} USDT</strong> on <strong>{}</strong> to "
            "<code style='user-select:all'>{}</code><br>"
            "<small>User receives ${} · fee ${} ({}%) · ${} will be deducted from their balance when completed.</small>",
            format(obj.usdt_amount.normalize(), "f"), obj.get_network_display(), obj.address,
            obj.usd_amount, obj.fee_usd, format(obj.fee_percent.normalize(), "f"), total,
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status == WithdrawalStatus.PENDING:
            editable = set(self.PENDING_EDITABLE)
        else:
            editable = set(self.ALWAYS_EDITABLE)
        readonly = [f.name for f in self.model._meta.fields if f.name not in editable]
        readonly += ["user_email", "send_instruction", "effective_time"]
        return readonly

    def save_model(self, request, obj, form, change):
        if not change:
            return
        current = Withdrawal.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.failure_reason or "").strip()
        tx_hash = (obj.admin_tx_hash or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        edits = _collect_edits(form, self.EDIT_KEYS, current.created_at) if current.status == WithdrawalStatus.PENDING else {}
        obj.status = current.status

        try:
            if edits:
                current = services.admin_update_withdrawal(current, edits)
                messages.info(
                    request,
                    f"Withdrawal {current.reference} updated: user receives {current.usd_amount} USD "
                    f"({current.usdt_amount.normalize():f} USDT), fee {current.fee_usd}, "
                    f"total deducted {current.total_usd}.",
                )
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

    @admin.action(description="Mark selected completed (deducts amount + fee)")
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

    @admin.action(description="Mark selected failed (releases amount + fee)")
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