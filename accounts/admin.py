"""Admin pages for accounts. /admin is the staff tool until we build our own panels."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import OtpChallenge, User


class UserCreateForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("phone",)


class UserEditForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    # Django's UserAdmin assumes a `username`; these settings point it at our fields.
    add_form = UserCreateForm
    form = UserEditForm
    ordering = ("-created_at",)
    search_fields = ("phone", "email", "full_name")
    list_display = ("phone", "full_name", "email", "is_participant", "is_organizer", "is_consent",
                    "credit_balance", "is_staff", "created_at")
    list_filter = ("is_participant", "is_organizer", "is_photographer", "is_consent", "is_staff", "is_active")
    readonly_fields = ("credit_balance", "created_at", "last_login")   # credits change via Credit transactions
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("full_name", "email", "phone_verified_at", "email_verified_at", "is_consent")}),
        ("Roles", {"fields": ("is_participant", "is_organizer", "is_photographer")}),
        ("Credits", {"fields": ("credit_balance", "credit_floor")}),
        ("Admin access", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("created_at", "last_login")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "usable_password", "password1", "password2")}),
    )


@admin.register(OtpChallenge)
class OtpChallengeAdmin(admin.ModelAdmin):
    list_display = ("phone", "role", "event", "created_at", "expires_at", "attempts", "consumed_at")
    search_fields = ("phone",)
    readonly_fields = [f.name for f in OtpChallenge._meta.fields]
