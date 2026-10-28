"""Admin pages for accounts. /admin is the staff tool until we build our own panels."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import Group

from .models import ConsentLog, ContactMessage, OtpChallenge, UploaderAccess, User


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
    list_display = ("id", "phone", "full_name", "email", "is_participant", "is_organizer", "kvkk_version",
                    "credit_balance", "discount_percent", "is_staff", "created_at")
    list_display_links = ("id", "phone")
    filter_horizontal = ()      # Django's UserAdmin lists groups/permissions here; we have none
    list_filter = ("is_participant", "is_organizer", "is_photographer", "kvkk_version", "is_staff", "is_active")
    readonly_fields = ("credit_balance", "kvkk_version", "created_at", "last_login")   # consent only via the consent step   # credits change via Credit transactions
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("full_name", "email", "phone_verified_at", "email_verified_at", "kvkk_version")}),
        ("Roles", {"fields": ("is_participant", "is_organizer", "is_photographer")}),
        ("Credits", {"fields": ("credit_balance", "credit_floor")}),
        ("Shop", {"fields": ("discount_percent",)}),
        ("Admin access", {"fields": ("is_active", "is_staff")}),
        ("Dates", {"fields": ("created_at", "last_login")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "usable_password", "password1", "password2")}),
    )


admin.site.unregister(Group)   # no Django groups in cp-core: staff = full access


@admin.register(OtpChallenge)
class OtpChallengeAdmin(admin.ModelAdmin):
    list_display = ("id", "phone", "dev_code", "role", "event", "created_at", "expires_at", "attempts", "consumed_at")
    search_fields = ("phone",)
    readonly_fields = [f.name for f in OtpChallenge._meta.fields]


@admin.register(UploaderAccess)
class UploaderAccessAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "phone", "organizer", "status", "created_at", "last_login_at")
    list_filter = ("status",)
    search_fields = ("phone", "full_name", "organizer__phone")
    raw_id_fields = ("organizer",)


@admin.register(ConsentLog)
class ConsentLogAdmin(admin.ModelAdmin):
    """The legal record: read-only, no adding or deleting from the admin."""
    list_display = ("id", "created_at", "phone", "full_name", "version", "locale", "ip")
    list_filter = ("version", "locale")
    search_fields = ("phone", "full_name")
    readonly_fields = [f.name for f in ConsentLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "name", "email", "subject", "user", "handled_at")
    list_filter = ("handled_at", "locale")
    search_fields = ("name", "email", "phone", "subject", "message")
    raw_id_fields = ("user", "handled_by")
    readonly_fields = ("name", "email", "phone", "subject", "message", "locale", "user", "ip", "user_agent", "created_at")
