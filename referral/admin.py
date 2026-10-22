from django.contrib import admin

from referral.models import Referral, ReferralProfile


class ReadOnlyAdmin(admin.ModelAdmin):
    """Everything read-only: no add, change or delete (payouts only via the trade hook)."""

    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralProfile)
class ReferralProfileAdmin(ReadOnlyAdmin):
    list_display = ("user_email", "code", "created_at")
    search_fields = ("code", "user__email")
    list_select_related = ("user",)

    @admin.display(description="User", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email


@admin.register(Referral)
class ReferralAdmin(ReadOnlyAdmin):
    list_display = ("referrer_email", "referred_email", "status", "verified_at", "rewarded_at", "bonus_amount", "created_at")
    list_filter = ("status", "created_at", "verified_at", "rewarded_at")
    search_fields = ("referrer__email", "referred__email", "code_used")
    date_hierarchy = "created_at"
    list_select_related = ("referrer", "referred")

    @admin.display(description="Referrer", ordering="referrer__email")
    def referrer_email(self, obj):
        return obj.referrer.email

    @admin.display(description="Referred", ordering="referred__email")
    def referred_email(self, obj):
        return obj.referred.email
