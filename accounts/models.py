"""The User model. Written in step 1 because Django needs AUTH_USER_MODEL before the first migrate.
OtpChallenge follows in step 2. Fields: design/core.md, "Data model"."""
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

phone_validator = RegexValidator(r"^\+[1-9]\d{6,14}$", "Phone must be in E.164 format, e.g. +905551234567.")


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
