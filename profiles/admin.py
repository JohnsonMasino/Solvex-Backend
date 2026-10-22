from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """View-only: profile data changes only via the user's own API or the KYC flow."""
    list_display = ("user_email", "user_full_name", "nationality", "country", "phone_number", "updated_at")
    list_select_related = ("user",)
    list_filter = ("sex", "country", "nationality")
    search_fields = ("user__email", "user__full_name", "phone_number")
    ordering = ("-created_at",)

    @admin.display(description="Email", ordering="user__email")
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description="Full name", ordering="user__full_name")
    def user_full_name(self, obj):
        return obj.user.full_name

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
