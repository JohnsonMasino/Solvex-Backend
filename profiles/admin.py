from django.contrib import admin
from django.utils.html import format_html

from . import services
from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """View-only: profile data changes only via the user's own API or the KYC flow."""
    list_display = (
        "avatar_thumb", "user_email", "user_full_name", "nationality", "country", "phone_number", "updated_at",
    )
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

    @admin.display(description="Photo")
    def avatar_thumb(self, obj):
        url = services.avatar_url_for(obj, size=64)
        if not url:
            return "—"
        return format_html(
            '<img src="{}" alt="" style="width:36px;height:36px;border-radius:50%;object-fit:cover">', url
        )

    @admin.display(description="Profile picture")
    def profile_photo_preview(self, obj):
        if obj is None:
            return "—"
        url = services.avatar_url_for(obj, size=520)
        if not url:
            return "No profile picture"
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener noreferrer">'
            '<img src="{url}" alt="" style="width:200px;height:200px;border-radius:8px;'
            'border:1px solid #ccc;object-fit:cover"></a>',
            url=url,
        )

    def get_readonly_fields(self, request, obj=None):
        return ["profile_photo_preview"] + [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False