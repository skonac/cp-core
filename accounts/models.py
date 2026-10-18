"""Accounts: User and OtpChallenge. Fields: design/core.md, "Data model"."""
import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

phone_validator = RegexValidator(r"^\+[1-9]\d{6,14}$", "Phone must be in E.164 format, e.g. +905551234567.")


class Role(models.TextChoices):
    """Roles a sign-in can ask for (OtpChallenge.role). Admin is not requested, it is granted."""
    PARTICIPANT = "participant"
    ORGANIZER = "organizer"
    PHOTOGRAPHER = "photographer"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra):
        if not phone:
            raise ValueError("phone is required")
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()   # OTP-only accounts: password login impossible
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        # `manage.py createsuperuser` lands here; admins log in to /admin with phone + password.
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    # From AbstractBaseUser: password, last_login. From PermissionsMixin: is_superuser, groups, user_permissions.
    phone = models.CharField(max_length=16, unique=True, validators=[phone_validator])
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)   # NULLs don't collide in a unique column
    email_verified_at = models.DateTimeField(null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True)

    is_consent = models.BooleanField(default=False)        # KVKK explicit consent
    is_participant = models.BooleanField(default=False)
    is_organizer = models.BooleanField(default=False)
    is_photographer = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)          # may log in to /admin; "admin" in accountRoles
    is_active = models.BooleanField(default=True)          # False = account locked

    credit_balance = models.IntegerField(default=0)
    credit_floor = models.IntegerField(null=True, blank=True)   # NULL = settings.CREDIT_FLOOR_DEFAULT

    created_at = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"    # the login identifier
    REQUIRED_FIELDS = []        # createsuperuser asks only for phone + password

    def save(self, *args, **kwargs):
        self.email = self.email or None   # "" from forms would collide in the unique column
        super().save(*args, **kwargs)

    @property
    def effective_credit_floor(self) -> int:
        return settings.CREDIT_FLOOR_DEFAULT if self.credit_floor is None else self.credit_floor

    @property
    def account_roles(self) -> list[str]:
        roles = [("participant", self.is_participant), ("organizer", self.is_organizer),
                 ("photographer", self.is_photographer), ("admin", self.is_staff)]
        return [name for name, held in roles if held]

    def __str__(self):
        return self.phone


def new_challenge_id() -> str:
    return "otp_" + secrets.token_urlsafe(16)


class OtpChallenge(models.Model):
    """One SMS code sent to one phone. Rows older than 24 h are deleted on each successful
    verification; the recent ones are what the per-phone rate limit counts."""
    id = models.CharField(primary_key=True, max_length=32, default=new_challenge_id, editable=False)
    phone = models.CharField(max_length=16, validators=[phone_validator])
    code_hash = models.CharField(max_length=128)              # what verification checks against
    dev_code = models.CharField(max_length=6, blank=True)     # readable copy, only while OTP_DELIVERY = "log" (no SMS)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PARTICIPANT)
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)    # wrong codes entered
    consumed_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["phone", "created_at"], name="otp_phone_recent"),   # rate limit
            models.Index(fields=["created_at"], name="otp_created"),                 # purge
        ]

    def __str__(self):
        return f"{self.phone} {self.created_at:%Y-%m-%d %H:%M}"
