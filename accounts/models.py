"""Accounts: User and OtpChallenge. Fields: design/core.md, "Data model"."""
import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
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
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser):
    # From AbstractBaseUser: password, last_login. No Django groups or per-model permissions:
    # staff (is_staff) may do everything in the admin; see has_perm below.
    phone = models.CharField(max_length=16, unique=True, validators=[phone_validator])
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)   # NULLs don't collide in a unique column
    email_verified_at = models.DateTimeField(null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True)

    kvkk_version = models.CharField(max_length=8, blank=True)   # KVKK document version consented to; "" = never
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

    # The admin asks these. One rule: an active staff account has every permission.
    def has_perm(self, perm, obj=None) -> bool:
        return self.is_active and self.is_staff

    def has_perms(self, perm_list, obj=None) -> bool:
        return self.is_active and self.is_staff

    def has_module_perms(self, app_label) -> bool:
        return self.is_active and self.is_staff

    @property
    def effective_credit_floor(self) -> int:
        return settings.CREDIT_FLOOR_DEFAULT if self.credit_floor is None else self.credit_floor

    @property
    def is_consent(self) -> bool:
        """KVKK explicit consent to the current document. A new document version voids older consent."""
        return bool(self.kvkk_version) and self.kvkk_version == settings.KVKK_VERSION

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
    dev_code = models.CharField(max_length=6, blank=True)     # readable copy, only while SMS_DELIVERY = "log" (no SMS)
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


class UploaderAccess(models.Model):
    """An organizer lets a phone number upload to some of their events: an uploader (cp-web's draft
    called it "photographer"; that word is the future account role, is_photographer). One row per
    organizer and phone, with the events it is assigned to (`events`, chosen by the organizer).
    Not an account: no User is created for the phone; signing in with it (organizer entry) gives a
    session that can only reach /api/uploader/…: the assigned events that are not expired, uploads on
    the organizer's credits. One phone may serve several organizers. Revoking keeps the row (the photos
    it uploaded point to it)."""

    class Status(models.TextChoices):
        ACTIVE = "active"
        REVOKED = "revoked"

    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="uploader_accesses")
    phone = models.CharField(max_length=16, validators=[phone_validator])
    full_name = models.CharField(max_length=150)              # as the organizer typed it
    events = models.ManyToManyField("events.Event", blank=True, related_name="uploader_accesses")   # where it may upload
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organizer", "phone"], condition=models.Q(status="active"),
                                               name="uploader_once_per_organizer")]
        indexes = [models.Index(fields=["phone"], condition=models.Q(status="active"), name="uploader_phone")]

    def __str__(self):
        return f"{self.full_name} ({self.phone}) for {self.organizer_id}"


class ConsentLog(models.Model):
    """One row per consent given: the legal record. Append-only (read-only in the admin).
    Kept when the account is deleted (user becomes NULL; phone and name are copies)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    phone = models.CharField(max_length=16)
    full_name = models.CharField(max_length=150, blank=True)
    version = models.CharField(max_length=8)                  # e.g. "002" = kvkk_tr_002.html + kvkk_en_002.html
    locale = models.CharField(max_length=2)                   # the language shown: tr | en
    sha256 = models.CharField(max_length=64)                  # of the shown file: proves the exact text
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"], name="consentlog_user")]

    def __str__(self):
        return f"{self.phone} {self.version}/{self.locale} {self.created_at:%Y-%m-%d %H:%M}"


class ContactMessage(models.Model):
    """A message from the site's contact form (public; linked to the account when signed in).
    Read and marked handled on the staff pages (/dj/staff/messages/) or in the admin."""
    name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=32, blank=True)
    subject = models.CharField(max_length=160)
    message = models.TextField(max_length=5000)
    locale = models.CharField(max_length=2, default="tr")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="contact_messages")
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        indexes = [models.Index(fields=["created_at"], name="contact_created")]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.email}: {self.subject}"
