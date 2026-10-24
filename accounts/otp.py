"""SMS one-time-code sign-in (design/core.md, "Sign-in"). One flow for login and registration:
a verified phone logs in, or creates the account if it is new.

The code is sent by notify.dispatch.otp() (SMS, routed by phone number; design/notifications.md).
While SMS_DELIVERY = "log" no SMS leaves the server: the code goes to the server log and to
OtpChallenge.dev_code (visible in the admin).
"""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.api import ApiError
from notify import dispatch

from .models import OtpChallenge, UploaderAccess, Role, User

log = logging.getLogger("cp.otp")
E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(text: str) -> str:
    return re.sub(r"[\s\-()]", "", text or "")


def mask_phone(phone: str) -> str:
    return "•" * (len(phone) - 4) + phone[-4:]


def _hash(challenge_id: str, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _deliver(challenge: OtpChallenge, code: str) -> None:
    if settings.SMS_DELIVERY == "log":
        challenge.dev_code = code
    try:
        provider = dispatch.otp(challenge.phone, code)
    except dispatch.SendError as e:
        log.warning("OTP for %s not sent: %s", challenge.phone, e)
        raise ApiError(502, "sms_not_sent", "Kod gönderilemedi. Biraz sonra tekrar dene.")
    log.info("OTP for %s sent via %s (challenge %s)", challenge.phone, provider, challenge.pk)


def request_code(phone: str, role: str, event, ip: str | None) -> OtpChallenge:
    phone = normalize_phone(phone)
    if not E164.match(phone):
        raise ApiError(422, "invalid_phone_number", "Geçerli bir cep telefonu numarası gir (+90…).")
    if role not in Role.values:
        raise ApiError(422, "invalid_role", f"role must be one of {', '.join(Role.values)}.")
    now = timezone.now()
    recent = OtpChallenge.objects.filter(phone=phone, created_at__gte=now - timedelta(minutes=15))
    if recent.count() >= settings.OTP_MAX_PER_15MIN:
        raise ApiError(429, "rate_limit_exceeded", "Çok fazla kod istendi. Birkaç dakika sonra tekrar dene.")
    last = recent.order_by("-created_at").first()
    if last and last.created_at > now - timedelta(seconds=settings.OTP_RESEND_AFTER):
        raise ApiError(429, "rate_limit_exceeded", "Yeni kod istemeden önce biraz bekle.")

    code = f"{secrets.randbelow(10 ** 6):06d}"
    challenge = OtpChallenge(phone=phone, role=role, event=event, ip=ip,
                             expires_at=now + timedelta(seconds=settings.OTP_TTL))
    challenge.code_hash = _hash(challenge.pk, code)
    _deliver(challenge, code)
    challenge.save()
    return challenge


def is_master_pass(value) -> bool:
    """settings.MASTER_PASS (non-production only; see settings). Logged when used."""
    master = settings.MASTER_PASS
    if master and value and hmac.compare_digest(str(value).strip(), master):
        log.warning("master pass used")
        return True
    return False


def is_uploader_sign_in(challenge: OtpChallenge, user) -> bool:
    """Organizer entry (no event) by a phone some organizer added as uploader, and not an organizer
    account itself: an uploader session, no account created, no role added."""
    return (challenge.role == Role.ORGANIZER and challenge.event_id is None
            and not (user and (user.is_organizer or user.is_staff))
            and UploaderAccess.objects.filter(phone=challenge.phone, status=UploaderAccess.Status.ACTIVE).exists())


def verify_code(challenge_id: str, code: str):
    """-> (user or None, is_new_user, event or None, uploader phone or "").
    An uploader sign-in (is_uploader_sign_in) creates no account and adds no role: user is the
    phone's existing account or None. Raises ApiError on any failure."""
    now = timezone.now()
    failure = None
    uploader_phone, is_new, event = "", False, None
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        if challenge is None or challenge.consumed_at:
            failure = ApiError(422, "invalid_otp", "Doğrulama kodu hatalı.")
        elif challenge.expires_at < now:
            failure = ApiError(410, "otp_expired", "Kodun süresi doldu. Yeni kod iste.")
        elif challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            failure = ApiError(429, "rate_limit_exceeded", "Çok fazla hatalı deneme. Yeni kod iste.")
        elif not (hmac.compare_digest(challenge.code_hash, _hash(challenge.pk, (code or "").strip()))
                  or is_master_pass(code)):
            challenge.attempts += 1                       # saved: the failure is raised after the commit
            challenge.save(update_fields=["attempts"])
            failure = ApiError(422, "invalid_otp", "Doğrulama kodu hatalı.")
        else:
            challenge.consumed_at = now
            challenge.save(update_fields=["consumed_at"])
            user = User.objects.filter(phone=challenge.phone).first()
            if is_uploader_sign_in(challenge, user):
                uploader_phone = challenge.phone
                UploaderAccess.objects.filter(phone=challenge.phone, status=UploaderAccess.Status.ACTIVE) \
                    .update(last_login_at=now)
            else:
                is_new = user is None
                if is_new:
                    user = User.objects.create_user(challenge.phone)
                role_flag = {"participant": "is_participant", "organizer": "is_organizer",
                             "photographer": "is_photographer"}[challenge.role]
                setattr(user, role_flag, True)
                user.phone_verified_at = user.phone_verified_at or now
                user.save(update_fields=[role_flag, "phone_verified_at"])
                event = challenge.event
    if failure:
        raise failure
    if event is not None:
        from events.participant import touch_membership   # events imports accounts: import here
        touch_membership(user, event)
    # Housekeeping hooked to a frequent event instead of a cron job: old challenges go.
    OtpChallenge.objects.filter(created_at__lt=now - timedelta(hours=24)).delete()
    return user, is_new, event, uploader_phone
