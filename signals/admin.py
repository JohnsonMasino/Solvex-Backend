from django.contrib import admin, messages

from .models import EligibleEmail


@admin.register(EligibleEmail)
class EligibleEmailAdmin(admin.ModelAdmin):
    list_display = ("email", "is_active", "note", "created_at", "updated_at")
    list_editable = ("is_active",)          # toggle straight from the list page
    list_filter = ("is_active", "created_at")
    search_fields = ("email", "note")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50
    actions = ["activate_selected", "deactivate_selected"]

    fieldsets = (
        (None, {"fields": ("email", "is_active", "note")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.action(description="Mark selected emails as ACTIVE (eligible)")
    def activate_selected(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f"{n} email(s) activated.", messages.SUCCESS)

    @admin.action(description="Mark selected emails as INACTIVE (not eligible)")
    def deactivate_selected(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f"{n} email(s) deactivated.", messages.WARNING)