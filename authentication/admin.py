from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError
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


def _purge(queryset, _depth=0):
    """Delete `queryset`, first deleting any rows that PROTECT (or RESTRICT) it.
    Must be called inside transaction.atomic()."""
    if _depth > 25:
        raise RuntimeError("Could not resolve protected relations while deleting (too deep).")
    for _ in range(50):
        try:
            queryset.delete()
            return
        except (ProtectedError, RestrictedError) as exc:
            blockers = getattr(exc, "protected_objects", None) or getattr(exc, "restricted_objects", None) or []
            by_model = {}
            for obj in blockers:
                by_model.setdefault(obj.__class__, set()).add(obj.pk)
            if not by_model:
                raise
            for model, pks in by_model.items():
                _purge(model._base_manager.filter(pk__in=pks), _depth + 1)
    raise RuntimeError("Could not resolve protected relations while deleting.")


def _is_protected_account(request, user):
    """Accounts the current admin may never delete."""
    if user.is_superuser:           # incl. the superuser created during migration
        return True
    if user.pk == request.user.pk:  # nobody deletes themselves
        return True
    if user.is_staff and not request.user.is_superuser:  # staff can't delete other staff
        return True
    return False


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
    # Django's built-in "Delete selected users" action stays enabled (do NOT remove it in get_actions).
    actions = ["unlock_accounts", "activate_accounts", "deactivate_accounts"]

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # staff must not be able to promote themselves or demote other admins
            readonly += ["is_superuser", "is_staff", "groups", "user_permissions"]
        return readonly

    # ------------------------------------------------------------------ account actions
    @admin.action(description="Unlock selected accounts")
    def unlock_accounts(self, request, queryset):
        n = queryset.update(failed_login_attempts=0, locked_until=None, updated_at=timezone.now())
        self.message_user(request, f"Unlocked {n} account(s).", messages.SUCCESS)

    @admin.action(description="Activate selected accounts")
    def activate_accounts(self, request, queryset):
        n = queryset.update(is_active=True, updated_at=timezone.now())
        self.message_user(request, f"Activated {n} account(s).", messages.SUCCESS)

    @admin.action(description="Deactivate selected (does NOT delete)")
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
    #   * Normal users are deleted together with ALL their data. Several models use
    #     on_delete=PROTECT, so _purge() deletes the blocking rows first.
    #   * Superusers can never be deleted. Nobody can delete their own account.
    #   * Staff can't delete other staff; only a superuser can.
    def has_delete_permission(self, request, obj=None):
        if obj is not None and _is_protected_account(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        if request.user.has_perm("authentication.delete_user"):
            perms_needed = set()
            if protected:
                to_delete = list(to_delete) + ["Also deleted along with this account:", list(protected)]
                protected = []
        return to_delete, model_count, perms_needed, protected

    def delete_model(self, request, obj):
        if _is_protected_account(request, obj):
            self.message_user(request, "This account can't be deleted.", messages.ERROR)
            return
        with transaction.atomic():
            OutstandingToken.objects.filter(user=obj).delete()
            _purge(User.objects.filter(pk=obj.pk))

    def delete_queryset(self, request, queryset):
        ids = [u.pk for u in queryset if not _is_protected_account(request, u)]
        skipped = queryset.count() - len(ids)
        if skipped:
            self.message_user(
                request,
                f"{skipped} account(s) skipped: superusers, staff accounts (unless you are a "
                f"superuser) and your own account can't be deleted.",
                messages.WARNING,
            )
        queryset = queryset.filter(pk__in=ids)
        with transaction.atomic():
            OutstandingToken.objects.filter(user__in=queryset).delete()
            _purge(queryset)


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