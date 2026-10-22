from django.contrib import admin, messages
from django.utils import timezone

from core.exceptions import BusinessError

from . import services
from .models import KYCStatus, KYCSubmission

EDITABLE_FIELDS = ("status", "rejection_reason", "admin_note")


def _err_text(exc):
    return getattr(exc, "message", None) or str(exc)


@admin.register(KYCSubmission)
class KYCSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "user_email", "full_name", "status", "id_type", "nationality",
        "attempt_number", "created_at", "reviewed_at",
    )
    list_filter = ("status", "nationality", "id_type", "created_at")
    search_fields = ("user__email", "full_name", "id_number")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["approve_selected", "reject_selected"]

    fieldsets = (
        ("Review", {"fields": (
            "user_email", "attempt_number", "status", "rejection_reason", "admin_note",
            "reviewed_by", "reviewed_at",
        )}),
        ("Personal Biodata", {"fields": (
            "full_name", "date_of_birth", "nationality", "sex", "phone_number",
        )}),
        ("Residential Address", {"fields": (
            "address_line1", "address_line2", "city", "state", "postal_code",
            "country", "address_entry_method",
        )}),
        ("Government ID", {"fields": ("id_type", "id_number")}),
        ("Employment & Occupation", {"fields": (
            "employment_status", "source_of_income", "occupation", "employer_name",
            "annual_income_range", "expected_annual_volume",
            "transaction_frequency", "typical_transaction_size",
        )}),
        ("Consent Records", {"fields": (
            "accuracy_declaration", "privacy_notice_accepted", "terms_accepted",
            "marketing_consent", "consent_at", "consent_ip", "consent_user_agent", "policy_version",
        )}),
    )

    # ---- display helpers --------------------------------------------------
    @admin.display(description="User email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    # ---- permissions ------------------------------------------------------
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = [f.name for f in self.model._meta.fields if f.name not in EDITABLE_FIELDS]
        readonly.append("user_email")
        # Once a submission is final only the internal note stays editable.
        if obj is not None and obj.status != KYCStatus.PENDING:
            readonly += ["status", "rejection_reason"]
        return readonly

    # ---- saving: ALWAYS through the services -------------------------------
    def save_model(self, request, obj, form, change):
        if not change:
            return
        current = KYCSubmission.objects.select_related("user").get(pk=obj.pk)
        new_status = obj.status
        reason = (obj.rejection_reason or "").strip()
        note = obj.admin_note
        updates = {"admin_note": note, "updated_at": timezone.now()}

        # Never let the in-memory form instance be persisted by Django itself.
        obj.status = current.status

        try:
            if new_status != current.status:
                if current.status != KYCStatus.PENDING or new_status not in (
                    KYCStatus.APPROVED, KYCStatus.REJECTED
                ):
                    messages.error(request, "Illegal status change. Only pending → approved or pending → rejected is allowed.")
                elif new_status == KYCStatus.REJECTED and not reason:
                    messages.error(request, "A rejection reason is required to reject a submission.")
                elif new_status == KYCStatus.APPROVED:
                    services.approve_submission(current, request.user)
                else:
                    services.reject_submission(current, request.user, reason)
            elif current.status == KYCStatus.PENDING:
                updates["rejection_reason"] = reason  # draft reason for the bulk "Reject" action
        except BusinessError as exc:
            messages.error(request, _err_text(exc))

        KYCSubmission.objects.filter(pk=obj.pk).update(**updates)

    # ---- bulk actions ------------------------------------------------------
    @admin.action(description="Approve selected submissions")
    def approve_selected(self, request, queryset):
        done = skipped = 0
        for sub in queryset.select_related("user"):
            if sub.status != KYCStatus.PENDING:
                skipped += 1
                continue
            services.approve_submission(sub, request.user)
            done += 1
        if done:
            self.message_user(request, f"{done} submission(s) approved.", messages.SUCCESS)
        if skipped:
            messages.error(request, f"{skipped} submission(s) skipped: only pending submissions can be approved.")

    @admin.action(description="Reject selected submissions")
    def reject_selected(self, request, queryset):
        done = skipped = missing_reason = 0
        for sub in queryset.select_related("user"):
            if sub.status != KYCStatus.PENDING:
                skipped += 1
                continue
            if not (sub.rejection_reason or "").strip():
                missing_reason += 1
                continue
            services.reject_submission(sub, request.user, sub.rejection_reason)
            done += 1
        if done:
            self.message_user(request, f"{done} submission(s) rejected.", messages.SUCCESS)
        if missing_reason:
            messages.error(
                request,
                f"{missing_reason} submission(s) skipped: add a rejection reason on the submission first.",
            )
        if skipped:
            messages.error(request, f"{skipped} submission(s) skipped: only pending submissions can be rejected.")
