from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
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


class UserEmailChoiceField(forms.ModelChoiceField):
    """Dropdown of all users, shown and sorted by email (used on the manual 'Add' pages)."""

    def label_from_instance(self, obj):
        return obj.email


def _user_choice_field():
    return UserEmailChoiceField(
        queryset=get_user_model().objects.order_by("email"),
        label="User",
        empty_label="Select a user by email",
        help_text="Pick the user by email. Click the list and type to jump to an email.",
    )


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


def _adopt(obj, created):
    """Make the unsaved admin form instance point at the record the service created, so Django's
    redirect / log entry after save_model work normally."""
    obj.__dict__.update({k: v for k, v in created.__dict__.items() if k != "_state"})
    obj._state.adding = False
    obj._state.db = created._state.db


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


class ManualAddMixin:
    """Shared plumbing for the 'Add' page of Deposits / Withdrawals (manual admin transactions).

    The record is created through the service layer (never through a plain model save), so if the
    service refuses (rate unavailable, insufficient funds, duplicate hash...) we show the error and
    send the admin back to the empty add page instead of crashing."""

    def log_addition(self, request, obj, message):
        if getattr(request, "_manual_failed", False):
            return None
        return super().log_addition(request, obj, message)

    def response_add(self, request, obj, post_url_continue=None):
        if getattr(request, "_manual_failed", False):
            return HttpResponseRedirect(request.get_full_path())
        return super().response_add(request, obj, post_url_continue)

    def has_delete_permission(self, request, obj=None):
        return False


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
    """Change form (any status)."""

    class Meta:
        model = Deposit
        fields = "__all__"


class ManualDepositForm(forms.ModelForm):
    """Add form: the admin adds a deposit to a user's account."""

    # Dropdown of users by email (replaces the raw-ID text box, which only accepted a UUID)
    user = _user_choice_field()

    credit_now = forms.BooleanField(
        required=False, initial=True,
        label="Credit the user's balance now",
        help_text=(
            "Ticked: the deposit is marked completed, the USD value is added to the user's balance and it "
            "appears in their transaction history. Unticked: it is created as a pending deposit."
        ),
    )

    class Meta:
        model = Deposit
        fields = (
            "user", "network", "amount", "rate", "usd_amount", "deposit_address",
            "tx_hash", "transaction_at", "admin_note",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("rate", "usd_amount", "deposit_address"):
            self.fields[name].required = False
        self.fields["amount"].help_text = "Amount in the crypto's own units (USDT / BTC / ETH), decided by the network."
        self.fields["rate"].help_text = "USD per 1 unit. Leave blank to use the live market rate."
        self.fields["usd_amount"].help_text = "Leave blank to use amount x rate."
        self.fields["deposit_address"].help_text = "Leave blank to use the company address of the network."
        self.fields["transaction_at"].help_text = "Leave blank to use the real time."


@admin.register(Deposit)
class DepositAdmin(ManualAddMixin, admin.ModelAdmin):
    form = DepositAdminForm
    raw_id_fields = ("user",)  # only affects the change page (user is read-only there); Add uses the dropdown

    # Editable in EVERY status.
    EDIT_KEYS = ("network", "amount", "rate", "usd_amount", "deposit_address", "tx_hash", "transaction_at")
    ALWAYS_EDITABLE = EDIT_KEYS + ("admin_note",)

    list_display = ("reference", "user_email", "network", "asset", "amount", "usd_amount", "status", "effective_time")
    list_filter = ("status", "asset", "network", "created_at")
    search_fields = ("reference", "user__email", "tx_hash")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["mark_completed", "mark_declined"]

    fieldsets = (
        ("Request (editable in any status)", {
            "description": (
                "You can change the crypto (network), amount, rate, USD value, address, tx hash and "
                "date/time at any time, even after the deposit is completed. If you change the network and "
                "leave rate / USD value untouched, the live rate of the new crypto is fetched and "
                "USD value = amount x rate. For a COMPLETED deposit the user's balance is corrected by the "
                "difference and the transaction history is updated with the new details."
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

    add_fieldsets = (
        ("Add a deposit to a user's account", {
            "description": (
                "Pick the user and enter the crypto and amount. Rate and USD value are filled in "
                "automatically when left blank. Tick 'Credit the user's balance now' to complete it "
                "immediately."
            ),
            "fields": (
                "user", "network", "amount", "rate", "usd_amount", "deposit_address",
                "tx_hash", "transaction_at", "admin_note", "credit_now",
            ),
        }),
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ManualDepositForm
        return super().get_form(request, obj, **kwargs)

    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Date shown to user")
    def effective_time(self, obj):
        return obj.effective_at

    def has_add_permission(self, request):
        return super(ManualAddMixin, self).has_add_permission(request)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        editable = set(self.ALWAYS_EDITABLE)
        if obj.status == DepositStatus.PENDING:
            editable |= {"status", "decline_reason"}
        elif obj.status == DepositStatus.DECLINED:
            editable |= {"decline_reason"}
        readonly = [f.name for f in self.model._meta.fields if f.name not in editable]
        readonly += ["user_email", "effective_time"]
        return readonly

    def save_model(self, request, obj, form, change):
        # ------------------------------------------------------------------ ADD (manual deposit)
        if not change:
            cd = form.cleaned_data
            try:
                created = services.admin_create_deposit(
                    request.user,
                    cd["user"],
                    cd["network"],
                    cd["amount"],
                    rate=cd.get("rate"),
                    usd_amount=cd.get("usd_amount"),
                    deposit_address=cd.get("deposit_address") or "",
                    tx_hash=cd.get("tx_hash") or "",
                    transaction_at=cd.get("transaction_at"),
                    admin_note=cd.get("admin_note") or "",
                    complete=bool(cd.get("credit_now")),
                )
            except BusinessError as exc:
                request._manual_failed = True
                messages.error(request, _err_text(exc))
                return
            except IntegrityError:
                request._manual_failed = True
                messages.error(request, "That transaction hash is already used by another live deposit.")
                return
            _adopt(obj, created)
            if created.status == DepositStatus.COMPLETED:
                messages.info(
                    request,
                    f"Deposit {created.reference}: {created.usd_amount} USD credited to {created.user.email}.",
                )
            else:
                messages.info(request, f"Deposit {created.reference} created as pending for {created.user.email}.")
            return

        # ------------------------------------------------------------------ CHANGE (any status)
        current = Deposit.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.decline_reason or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        edits = _collect_edits(form, self.EDIT_KEYS, current.created_at)
        obj.status = current.status  # never let Django persist the raw form instance

        try:
            if edits:
                was_completed = current.status == DepositStatus.COMPLETED
                current = services.admin_update_deposit(current, edits)
                suffix = " The user's balance and transaction history were updated." if was_completed else ""
                messages.info(
                    request,
                    f"Deposit {current.reference} updated: {current.amount.normalize():f} "
                    f"{str(current.asset).upper()} on {current.network.upper()} = {current.usd_amount} USD.{suffix}",
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
            elif current.status in (DepositStatus.PENDING, DepositStatus.DECLINED):
                updates["decline_reason"] = reason  # draft reason / edited reason
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
    """Change form (any status)."""

    class Meta:
        model = Withdrawal
        fields = "__all__"


class ManualWithdrawalForm(forms.ModelForm):
    """Add form: the admin adds a withdrawal to a user's account."""

    # Dropdown of users by email (replaces the raw-ID text box, which only accepted a UUID)
    user = _user_choice_field()

    complete_now = forms.BooleanField(
        required=False, initial=True,
        label="Deduct from the user's balance now",
        help_text=(
            "Ticked: the withdrawal is marked completed, amount + fee is removed from the user's balance "
            "and it appears in their transaction history. Unticked: the funds are reserved and the "
            "withdrawal waits as pending in the withdrawal queue."
        ),
    )

    class Meta:
        model = Withdrawal
        fields = (
            "user", "network", "address", "usd_amount", "fee_percent", "rate",
            "admin_tx_hash", "transaction_at", "admin_note",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fee_percent"].required = False
        self.fields["rate"].required = False
        self.initial["fee_percent"] = Decimal(str(settings.WITHDRAWAL_FEE_PERCENT)).quantize(Decimal("0.01"))
        self.fields["usd_amount"].help_text = "The USD value the user RECEIVES. The fee is added on top."
        self.fields["fee_percent"].help_text = "Defaults to the platform withdrawal fee. Enter 0 for no fee."
        self.fields["rate"].help_text = "USD per 1 USDT. Leave blank to use the live rate."
        self.fields["admin_tx_hash"].help_text = "Optional: the payout transaction hash."
        self.fields["transaction_at"].help_text = "Leave blank to use the real time."

    def clean_usd_amount(self):
        value = self.cleaned_data["usd_amount"]
        if value <= 0:
            raise forms.ValidationError("Amount must be greater than 0.")
        return value

    def clean(self):
        cd = super().clean()
        network, address = cd.get("network"), cd.get("address")
        if network and address:
            try:
                cd["address"] = services.validate_address(network, address)
            except BusinessError as exc:
                self.add_error("address", _err_text(exc))
        return cd


@admin.register(Withdrawal)
class WithdrawalAdmin(ManualAddMixin, admin.ModelAdmin):
    form = WithdrawalAdminForm
    raw_id_fields = ("user",)  # only affects the change page (user is read-only there); Add uses the dropdown

    # Editable in EVERY status.
    EDIT_KEYS = (
        "network", "address", "usd_amount", "fee_percent", "fee_usd", "total_usd",
        "rate", "usdt_amount", "transaction_at",
    )
    ALWAYS_EDITABLE = EDIT_KEYS + ("admin_note",)

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
        ("Request (editable in any status)", {
            "description": (
                "You can change the network, address, amounts, fee and rate at any time, even after the "
                "withdrawal is completed. Leave the derived values untouched and they are recalculated: "
                "fee = amount x fee %, total = amount + fee, USDT = amount / rate. While PENDING, a changed "
                "total adjusts the reserved funds (an 'adjustment' entry is added to the ledger). When "
                "COMPLETED, the user's balance is corrected by the difference and the transaction history "
                "is updated with the new details."
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

    add_fieldsets = (
        ("Add a withdrawal to a user's account", {
            "description": (
                "Pick the user, network, payout address and the USD amount the user receives. The fee is "
                "added on top. The user needs enough available balance to cover amount + fee. Tick "
                "'Deduct from the user's balance now' to complete it immediately."
            ),
            "fields": (
                "user", "network", "address", "usd_amount", "fee_percent", "rate",
                "admin_tx_hash", "transaction_at", "admin_note", "complete_now",
            ),
        }),
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ManualWithdrawalForm
        return super().get_form(request, obj, **kwargs)

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
        return super(ManualAddMixin, self).has_add_permission(request)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        editable = set(self.ALWAYS_EDITABLE)
        if obj.status == WithdrawalStatus.PENDING:
            editable |= {"status", "admin_tx_hash", "failure_reason"}
        elif obj.status == WithdrawalStatus.COMPLETED:
            editable |= {"admin_tx_hash"}
        elif obj.status == WithdrawalStatus.FAILED:
            editable |= {"failure_reason"}
        readonly = [f.name for f in self.model._meta.fields if f.name not in editable]
        readonly += ["user_email", "send_instruction", "effective_time"]
        return readonly

    def save_model(self, request, obj, form, change):
        # ------------------------------------------------------------------ ADD (manual withdrawal)
        if not change:
            cd = form.cleaned_data
            try:
                created = services.admin_create_withdrawal(
                    request.user,
                    cd["user"],
                    cd["network"],
                    cd["address"],
                    cd["usd_amount"],
                    fee_percent=cd.get("fee_percent"),
                    rate=cd.get("rate"),
                    transaction_at=cd.get("transaction_at"),
                    admin_tx_hash=cd.get("admin_tx_hash") or "",
                    admin_note=cd.get("admin_note") or "",
                    complete=bool(cd.get("complete_now")),
                )
            except BusinessError as exc:
                request._manual_failed = True
                messages.error(request, _err_text(exc))
                return
            _adopt(obj, created)
            if created.status == WithdrawalStatus.COMPLETED:
                messages.info(
                    request,
                    f"Withdrawal {created.reference}: {created.total_usd} USD "
                    f"({created.usd_amount} + {created.fee_usd} fee) deducted from {created.user.email}.",
                )
            else:
                messages.info(
                    request,
                    f"Withdrawal {created.reference} created as pending for {created.user.email}; "
                    f"{created.total_usd} USD is reserved.",
                )
            return

        # ------------------------------------------------------------------ CHANGE (any status)
        current = Withdrawal.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.failure_reason or "").strip()
        tx_hash = (obj.admin_tx_hash or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        edits = _collect_edits(form, self.EDIT_KEYS, current.created_at)
        obj.status = current.status

        try:
            if edits:
                was_completed = current.status == WithdrawalStatus.COMPLETED
                current = services.admin_update_withdrawal(current, edits)
                suffix = " The user's balance and transaction history were updated." if was_completed else ""
                messages.info(
                    request,
                    f"Withdrawal {current.reference} updated: user receives {current.usd_amount} USD "
                    f"({current.usdt_amount.normalize():f} USDT), fee {current.fee_usd}, "
                    f"total deducted {current.total_usd}.{suffix}",
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
            elif current.status == WithdrawalStatus.COMPLETED:
                updates["admin_tx_hash"] = tx_hash[:100]
            elif current.status == WithdrawalStatus.FAILED and reason:
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