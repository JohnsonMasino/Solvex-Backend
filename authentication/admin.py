from django import forms
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

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
    actions = ["unlock_accounts", "deactivate_accounts", "delete_selected_users"]

    # ------------------------------------------------------------------ account actions
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

    # ------------------------------------------------------------------ deleting users
    # Rules:
    #   * Any user can be deleted, and everything tied to the account is deleted with it
    #     (wallet, ledger, deposits, withdrawals, KYC, loans, trades, referrals, OTPs, reset
    #     tokens...). That is done by the database CASCADE on each model's user foreign key.
    #   * Superusers (including the one created during migration) can NEVER be deleted.
    #   * Nobody can delete their own account.
    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_superuser or obj.pk == request.user.pk):
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Replace Django's built-in bulk delete with the safe version below.
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Delete selected users and ALL their data", permissions=["delete"])
    def delete_selected_users(self, request, queryset):
        protected = queryset.filter(is_superuser=True) | queryset.filter(pk=request.user.pk)
        protected_count = protected.distinct().count()
        deletable = queryset.exclude(is_superuser=True).exclude(pk=request.user.pk)

        if protected_count:
            self.message_user(
                request,
                f"{protected_count} account(s) skipped: superusers and your own account can't be deleted.",
                messages.WARNING,
            )
        if not deletable.exists():
            return None
        return delete_selected(self, request, deletable)

    def get_deleted_objects(self, objs, request):
        """Django refuses to delete a user if the admin lacks delete permission on any RELATED
        model, and several apps' admin classes are view-only (delete disabled). A superuser
        may delete users together with that related data, so the permission check is lifted."""
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        if request.user.is_superuser:
            perms_needed = set()
        return to_delete, model_count, perms_needed, protected

    def delete_model(self, request, obj):
        if obj.is_superuser or obj.pk == request.user.pk:
            self.message_user(request, "This account can't be deleted.", messages.ERROR)
            return
        with transaction.atomic():
            # simplejwt's OutstandingToken.user is SET_NULL, so remove the user's tokens explicitly.
            OutstandingToken.objects.filter(user=obj).delete()
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        queryset = queryset.exclude(is_superuser=True).exclude(pk=request.user.pk)
        with transaction.atomic():
            OutstandingToken.objects.filter(user__in=queryset).delete()
            queryset.delete()


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