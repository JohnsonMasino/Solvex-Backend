from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.utils import timezone

from . import services
from .models import EmailOTP, PasswordResetToken, User


class AdminUserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Password confirmation", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("email", "full_name", "is_email_verified", "is_staff", "is_superuser")

    def clean_password2(self):
        p1, p2 = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self._save_m2m()
        return user


class AdminUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = AdminUserChangeForm
    add_form = AdminUserCreationForm
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "tier", "kyc_status", "is_email_verified", "is_active", "date_joined")
    list_filter = ("tier", "kyc_status", "is_email_verified", "is_active")
    search_fields = ("email", "full_name")
    readonly_fields = ("tier", "kyc_status", "last_login", "date_joined",
                       "failed_login_attempts", "locked_until", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name",)}),
        ("Verification / KYC (managed by the KYC flow)", {"fields": ("is_email_verified", "tier", "kyc_status")}),
        ("Login security", {"fields": ("failed_login_attempts", "locked_until")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": (
            "email", "full_name", "password1", "password2", "is_email_verified", "is_staff", "is_superuser")}),
    )
    actions = ["unlock_accounts", "deactivate_accounts"]

    @admin.action(description="Unlock selected accounts")
    def unlock_accounts(self, request, queryset):
        n = queryset.update(failed_login_attempts=0, locked_until=None, updated_at=timezone.now())
        self.message_user(request, f"Unlocked {n} account(s).", messages.SUCCESS)

    @admin.action(description="Deactivate selected")
    def deactivate_accounts(self, request, queryset):
        queryset = queryset.exclude(pk=request.user.pk)  # never deactivate yourself
        count = 0
        for user in queryset:
            user.is_active = False
            user.save(update_fields=["is_active", "updated_at"])
            services.blacklist_all_tokens(user)
            count += 1
        self.message_user(request, f"Deactivated {count} account(s).", messages.SUCCESS)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(EmailOTP)
class EmailOTPAdmin(ReadOnlyAdmin):
    list_display = ("user", "purpose", "is_used", "attempts", "expires_at", "last_sent_at", "created_at")
    list_filter = ("purpose", "is_used")
    search_fields = ("user__email",)
    exclude = ("code_hash",)
    ordering = ("-created_at",)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(ReadOnlyAdmin):
    list_display = ("user", "expires_at", "used_at", "created_at")
    search_fields = ("user__email",)
    exclude = ("token_hash",)
    ordering = ("-created_at",)
