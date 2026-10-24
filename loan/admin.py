from django.contrib import admin, messages
from django.utils import timezone

from core.exceptions import BusinessError
from wallet.models import Wallet

from . import services
from .calculations import compute_state
from .models import Loan, LoanRepayment, LoanStatus


def _err_text(exc):
    return getattr(exc, "message", None) or str(exc)


class RepaymentInline(admin.TabularInline):
    model = LoanRepayment
    extra = 0
    can_delete = False
    fields = ("created_at", "amount", "source", "outstanding_after", "reference")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LoanStatusFilter(admin.SimpleListFilter):
    """Status filter that defaults to the pending-approval queue instead of 'All'."""

    title = "status"
    parameter_name = "loan_status"

    def lookups(self, request, model_admin):
        return [("all", "All")] + list(LoanStatus.choices)

    def queryset(self, request, queryset):
        value = self.value()
        if value is None:
            return queryset.filter(status=LoanStatus.PENDING)
        if value == "all":
            return queryset
        return queryset.filter(status=value)

    def choices(self, changelist):
        current = self.value() or LoanStatus.PENDING
        for lookup, title in self.lookup_choices:
            yield {
                "selected": current == lookup,
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    # The only things an admin can change: the decision and the notes.
    EDITABLE = ("status", "decline_reason", "admin_note")

    list_display = (
        "reference", "user_email", "principal", "duration", "status",
        "interest_now", "amount_repaid", "outstanding_now", "overdue", "due_at", "created_at",
    )
    list_filter = (LoanStatusFilter, "duration", "created_at")
    search_fields = ("reference", "user__email")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["approve_loans", "reject_loans"]
    inlines = [RepaymentInline]

    fieldsets = (
        ("Request", {"fields": (
            "user_email", "user_balance", "reference", "principal", "duration", "duration_days",
            "daily_rate_percent", "overdue_rate_percent", "created_at",
        )}),
        ("Decision", {"fields": (
            "status", "decline_reason", "admin_note", "processed_by", "processed_at",
        )}),
        ("Once approved", {"fields": (
            "started_at", "halfway_at", "due_at", "closed_at", "credited_to_wallet",
            "interest_now", "amount_repaid", "outstanding_now",
            "halfway_reminder_sent_at", "overdue_notice_sent_at",
        )}),
    )

    # ---- computed columns -------------------------------------------------
    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="User wallet balance")
    def user_balance(self, obj):
        value = Wallet.objects.filter(user_id=obj.user_id).values_list("balance", flat=True).first()
        return value if value is not None else "-"

    @admin.display(description="Interest so far")
    def interest_now(self, obj):
        return compute_state(obj)["interest_accrued"]

    @admin.display(description="Outstanding")
    def outstanding_now(self, obj):
        return compute_state(obj)["outstanding"]

    @admin.display(description="Overdue", boolean=True)
    def overdue(self, obj):
        return compute_state(obj)["is_overdue"]

    # ---- permissions ------------------------------------------------------
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = [f.name for f in self.model._meta.fields if f.name not in self.EDITABLE]
        readonly += ["user_email", "user_balance", "interest_now", "outstanding_now"]
        if obj is not None and obj.status != LoanStatus.PENDING:
            readonly += ["status", "decline_reason"]
        return readonly

    # ---- change form: set status to Active to approve, Rejected (+ reason) to reject ----
    def save_model(self, request, obj, form, change):
        if not change:
            return
        current = Loan.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.decline_reason or "").strip()
        updates = {"admin_note": obj.admin_note, "updated_at": timezone.now()}
        obj.status = current.status  # never let Django persist the raw form instance

        try:
            if new_status != current.status:
                if current.status != LoanStatus.PENDING or new_status not in (
                    LoanStatus.ACTIVE, LoanStatus.REJECTED
                ):
                    messages.error(
                        request,
                        "Illegal status change. Only pending → active (approve) or pending → rejected is allowed.",
                    )
                elif new_status == LoanStatus.REJECTED and not reason:
                    messages.error(request, "A reason is required to reject a loan request.")
                elif new_status == LoanStatus.ACTIVE:
                    services.approve_loan(current, request.user)
                    messages.success(request, f"Loan {current.reference} approved. The user has been emailed.")
                else:
                    services.reject_loan(current, request.user, reason)
                    messages.success(request, f"Loan {current.reference} rejected. The user has been emailed.")
            elif current.status == LoanStatus.PENDING:
                updates["decline_reason"] = reason  # draft reason
        except BusinessError as exc:
            messages.error(request, _err_text(exc))
        Loan.objects.filter(pk=obj.pk).update(**updates)

    # ---- bulk actions -----------------------------------------------------
    @admin.action(description="Approve selected loan requests")
    def approve_loans(self, request, queryset):
        done = skipped = 0
        for loan in queryset.select_related("user"):
            if loan.status != LoanStatus.PENDING:
                skipped += 1
                continue
            services.approve_loan(loan, request.user)
            done += 1
        if done:
            self.message_user(request, f"{done} loan(s) approved. Users have been emailed.", messages.SUCCESS)
        if skipped:
            messages.error(request, f"{skipped} loan(s) skipped: only pending requests can be approved.")

    @admin.action(description="Reject selected loan requests (needs a reason on each)")
    def reject_loans(self, request, queryset):
        done = skipped = missing = 0
        for loan in queryset.select_related("user"):
            if loan.status != LoanStatus.PENDING:
                skipped += 1
                continue
            if not (loan.decline_reason or "").strip():
                missing += 1
                continue
            services.reject_loan(loan, request.user, loan.decline_reason)
            done += 1
        if done:
            self.message_user(request, f"{done} loan(s) rejected. Users have been emailed.", messages.SUCCESS)
        if missing:
            messages.error(request, f"{missing} loan(s) skipped: add a decline reason on the loan first.")
        if skipped:
            messages.error(request, f"{skipped} loan(s) skipped: only pending requests can be rejected.")