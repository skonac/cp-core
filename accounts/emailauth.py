"""Email sign-in and passwords (design/core.md, "Email sign-up and guest access").

One mail serves sign-up, sign-in and "forgot password": a link and a 4-digit code. Either one signs in,
or creates the account when the email is new. An account without a usable password (new, or signed up
by phone) is then asked to choose one; purpose `reset` asks for a new one. Choosing a password needs no
old password for PASSWORD_SET_WINDOW after an email sign-in (session flag), otherwise the current one.

While EMAIL_DELIVERY = "log" nothing leaves the server: the mail goes to the server log and the code
to EmailChallenge.dev_code (visible in the admin).
"""
import hashlib
import hmac
import logging
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from common.api import ApiError
from common.ratelimit import allow
from notify import dispatch
from notify.alerts import alert

from . import uploader
from .models import EmailChallenge, Role, User, normalize_email
from .otp import is_master_pass

log = logging.getLogger("cp.emailauth")

SESSION_PASSWORD_UNTIL = "password_set_until"   # session: an email sign-in allows a password without the old one
SESSION_EVENT = "sign_in_event"                  # session: the event of the last sign-in (nextStep after the password)
SESSION_UPLOADER = "uploader_entry"              # session: the last sign-in was an uploader entry (nextStep after the password)

ROLE_FLAGS = {"participant": "is_participant", "organizer": "is_organizer", "photographer": "is_photographer"}

TEXTS = {
    ("sign_in", "tr"): ("Cleverphotos giriş kodun: {code}",
                        "Merhaba,\n\nCleverphotos'a girmek için bu bağlantıya tıkla:\n{link}\n\n"
                        "ya da şu kodu gir: {code}\n\n"
                        "Bağlantı ve kod {minutes} dakika geçerli ve bir kez kullanılabilir. "
                        "Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin.\n\nCleverphotos"),
    ("sign_in", "en"): ("Your Cleverphotos sign-in code: {code}",
                        "Hello,\n\nClick this link to sign in to Cleverphotos:\n{link}\n\n"
                        "or enter this code: {code}\n\n"
                        "The link and the code are valid for {minutes} minutes and work once. "
                        "If you didn't ask for this, you can ignore this email.\n\nCleverphotos"),
    # Invitations carry the link only (no code to type; Nov 8) and stay valid longer (EMAIL_INVITE_TTL_HOURS).
    ("invite", "tr"): ("{organizer} seni Cleverphotos'a fotoğraf yüklemek için ekledi",
                       "Merhaba,\n\n{organizer} seni Cleverphotos'ta etkinlik fotoğraflarını yüklemen için ekledi.\n\n"
                       "Başlamak için bu bağlantıya tıkla:\n{link}\n\n"
                       "Ardından bir şifre seçeceksin; sonraki girişlerde e-postan ve şifrenle, organizatör "
                       "girişinden girebilirsin. Bağlantı {hours} saat geçerli ve bir kez kullanılabilir; süresi "
                       "geçerse {organizer} sana yeniden gönderebilir.\n\nCleverphotos"),
    ("invite", "en"): ("{organizer} added you to Cleverphotos to upload photos",
                       "Hello,\n\n{organizer} added you on Cleverphotos to upload their event photos.\n\n"
                       "Click this link to get started:\n{link}\n\n"
                       "Then you'll choose a password; next time sign in with your email and password through the "
                       "organizer entry. The link is valid for {hours} hours and works once; if it expires, "
                       "{organizer} can send it again.\n\nCleverphotos"),
    ("reset", "tr"): ("Cleverphotos şifre sıfırlama kodun: {code}",
                      "Merhaba,\n\nCleverphotos şifreni yenilemek için bu bağlantıya tıkla:\n{link}\n\n"
                      "ya da şu kodu gir: {code}\n\nArdından yeni şifreni seçeceksin. "
                      "Bağlantı ve kod {minutes} dakika geçerli ve bir kez kullanılabilir. "
                      "Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin; şifren değişmez.\n\nCleverphotos"),
    ("reset", "en"): ("Your Cleverphotos password reset code: {code}",
                      "Hello,\n\nClick this link to reset your Cleverphotos password:\n{link}\n\n"
                      "or enter this code: {code}\n\nThen you'll choose a new password. "
                      "The link and the code are valid for {minutes} minutes and work once. "
                      "If you didn't ask for this, you can ignore this email; your password stays as it is.\n\nCleverphotos"),
}


def _hmac(text: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), text.encode(), hashlib.sha256).hexdigest()


def clean_email(text: str) -> str:
    email = normalize_email(text)
    try:
        validate_email(email)
    except ValidationError:
        raise ApiError(422, "invalid_email", "Geçerli bir e-posta adresi gir.") from None
    return email


# The link's `flow` parameter, so the page knows what the person came for (Nov 9).
LINK_FLOWS = {"sign_up": "signup", "sign_in": "signin", "reset": "reset", "invite": "uploader"}


def mail_link(link_base: str, token: str, purpose: str) -> str:
    """link_base + EMAIL_LINK_PATH with the token, and `flow` (signup | signin | reset | uploader): through a
    {flow} placeholder in the path, or appended as a query parameter."""
    flow = LINK_FLOWS[purpose]
    path = settings.EMAIL_LINK_PATH
    if "{flow}" in path:
        path = path.format(token=token, flow=flow)
    else:
        path = path.format(token=token)
        path += ("&" if "?" in path else "?") + f"flow={flow}"
    return link_base.rstrip("/") + path


def _ttl(purpose: str) -> int:
    """Seconds a mail's link (and code) stays valid."""
    if purpose == EmailChallenge.Purpose.INVITE:
        return settings.EMAIL_INVITE_TTL_HOURS * 3600
    return settings.EMAIL_CODE_TTL


def _fails_today(email: str, now) -> int:
    return (EmailChallenge.objects.filter(email=email, created_at__gte=now - timedelta(hours=24))
            .aggregate(n=Sum("attempts"))["n"] or 0)


def request_mail(email: str, purpose: str, role: str, event, locale: str, ip: str | None,
                 link_base: str, organizer_name: str = "") -> EmailChallenge:
    """Create a challenge and mail its link and code. `link_base` = scheme + host for the link
    (request.build_absolute_uri("/")). Raises ApiError."""
    email = clean_email(email)
    if purpose not in EmailChallenge.Purpose.values:
        raise ApiError(422, "invalid_purpose", "purpose must be sign_up, sign_in or reset.")
    if purpose == EmailChallenge.Purpose.SIGN_UP:
        existing = User.objects.filter(email=email).first()
        if existing is not None and existing.has_usable_password() and existing.email_verified_at:
            raise ApiError(409, "email_already_registered",
                           "Bu e-posta ile zaten bir hesap var. Şifrenle giriş yap ya da şifreni sıfırla.")
    if role not in Role.values:
        raise ApiError(422, "invalid_role", f"role must be one of {', '.join(Role.values)}.")
    locale = locale if locale in ("tr", "en") else "tr"
    now = timezone.now()
    recent = EmailChallenge.objects.filter(email=email, created_at__gte=now - timedelta(minutes=15))
    if recent.count() >= settings.EMAIL_CODE_MAX_PER_15MIN:
        raise ApiError(429, "rate_limit_exceeded", "Çok fazla e-posta istendi. Birkaç dakika sonra tekrar dene.")
    last = recent.order_by("-created_at").first()
    if last and last.created_at > now - timedelta(seconds=settings.EMAIL_CODE_RESEND_AFTER):
        raise ApiError(429, "rate_limit_exceeded", "Yeni e-posta istemeden önce biraz bekle.")

    code, token = f"{secrets.randbelow(10 ** 4):04d}", secrets.token_urlsafe(24)
    challenge = EmailChallenge(email=email, purpose=purpose, role=role, event=event, locale=locale, ip=ip,
                               expires_at=now + timedelta(seconds=_ttl(purpose)))
    challenge.code_hash = _hmac(f"{challenge.pk}:{code}")
    challenge.link_hash = hashlib.sha256(token.encode()).hexdigest()
    if settings.EMAIL_DELIVERY == "log":
        challenge.dev_code = code
    link = mail_link(link_base, token, purpose)
    subject, text = TEXTS[("sign_in" if purpose == EmailChallenge.Purpose.SIGN_UP else purpose, locale)]
    subject, text = subject.replace("{organizer}", organizer_name), text.replace("{organizer}", organizer_name)
    with transaction.atomic():
        challenge.save()
        dispatch.email(email, subject.format(code=code),
                       text.format(code=code, link=link, minutes=settings.EMAIL_CODE_TTL // 60,
                                   hours=settings.EMAIL_INVITE_TTL_HOURS),
                       kind=f"email_{purpose}", user=User.objects.filter(email=email).first(), event=event)
    log.info("sign-in mail (%s) for %s queued (challenge %s)", purpose, email, challenge.pk)
    if purpose == EmailChallenge.Purpose.RESET:
        account = User.objects.filter(email=email).values_list("pk", flat=True).first()
        if account is not None:      # no address in the message; an unknown address is not worth a message
            alert("accounts.resetpass", f"Password reset mail requested for account {account}", key=f"reset:{account}")
    return challenge


def verify(challenge_id: str = "", code: str = "", token: str = ""):
    """By the code (challenge_id + code) or by the link (token).
    -> (user, is_new_user, event or None, purpose, uploader entry). Raises ApiError on any failure.
    An uploader entry (organizer entry by some organizer's uploader, accounts.uploader.is_uploader_entry)
    adds no organizer role."""
    now = timezone.now()
    failure = None
    is_new, event, user, uploader_entry = False, None, None, False
    with transaction.atomic():
        if token:
            challenge = EmailChallenge.objects.select_for_update().filter(
                link_hash=hashlib.sha256(token.strip().encode()).hexdigest()).first()
        else:
            challenge = EmailChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        if challenge is None or challenge.consumed_at:
            failure = ApiError(422, "invalid_email_code", "Kod veya bağlantı geçersiz ya da kullanılmış.")
        elif challenge.expires_at < now:
            failure = ApiError(410, "email_code_expired", "Kodun süresi doldu. Yeni e-posta iste.")
        elif not token and (challenge.attempts >= settings.EMAIL_CODE_MAX_ATTEMPTS
                            or _fails_today(challenge.email, now) >= settings.EMAIL_CODE_MAX_FAILS_PER_DAY):
            failure = ApiError(429, "rate_limit_exceeded", "Çok fazla hatalı deneme. E-postadaki bağlantıyı kullan.")
        elif not token and not (hmac.compare_digest(challenge.code_hash, _hmac(f"{challenge.pk}:{(code or '').strip()}"))
                                or is_master_pass(code)):
            challenge.attempts += 1                      # saved: the failure is raised after the commit
            challenge.save(update_fields=["attempts"])
            failure = ApiError(422, "invalid_email_code", "Kod hatalı.")
        else:
            challenge.consumed_at = now
            challenge.save(update_fields=["consumed_at"])
            user = User.objects.select_for_update().filter(email=challenge.email).first()
            if user is not None and user.email_verified_at is None and user.phone:
                # An address typed into a phone account's profile, never verified: it belongs to whoever
                # proves it. It leaves that account; the prover gets their own.
                log.warning("unverified %s removed from account %s (proved by email sign-in)", challenge.email, user.pk)
                user.email = None
                user.save(update_fields=["email"])
                user = None
            is_new = user is None
            if is_new:
                user = User.objects.create_user(email=challenge.email)
                transaction.on_commit(lambda u=user, r=challenge.role: alert(
                    "accounts.new", f"New account {u.pk} by email ({r})", key=f"new:{u.pk}"))
            user.email_verified_at = user.email_verified_at or now
            event = challenge.event
            uploader_entry = uploader.is_uploader_entry(user, challenge.role, event)
            if uploader_entry:
                uploader.touch_last_login(uploader.user_identity(user))
            flag = ROLE_FLAGS["participant" if uploader_entry else challenge.role]   # an uploader is no organizer
            setattr(user, flag, True)
            user.save(update_fields=[flag, "email_verified_at"])
    if failure:
        raise failure
    if event is not None:
        from events.participant import touch_membership   # events imports accounts: import here
        touch_membership(user, event)
    # housekeeping: rows older than a day, once expired (uploader invitations live longer)
    EmailChallenge.objects.filter(created_at__lt=now - timedelta(hours=24), expires_at__lt=now).delete()
    log.info("email sign-in: %s (%s, %s)", user.email, challenge.purpose, "new" if is_new else "existing")
    return user, is_new, event, challenge.purpose, uploader_entry


def needs_password(user: User, purpose: str) -> bool:
    return purpose == EmailChallenge.Purpose.RESET or not user.has_usable_password()


def after_sign_in(request, event, may_set_password: bool, uploader_entry: bool = False) -> None:
    """Session bookkeeping after login(): which event the sign-in was for, whether it was an uploader entry,
    and the window for choosing a password without the old one."""
    request.session[SESSION_EVENT] = event.pk if event else None
    request.session[SESSION_UPLOADER] = uploader_entry
    if may_set_password:
        request.session[SESSION_PASSWORD_UNTIL] = time.time() + settings.PASSWORD_SET_WINDOW
    else:
        request.session.pop(SESSION_PASSWORD_UNTIL, None)


def in_password_window(request) -> bool:
    return request.session.get(SESSION_PASSWORD_UNTIL, 0) > time.time()


def set_password(request, password: str, current_password: str | None = None) -> None:
    """Set the signed-in user's password (min PASSWORD_MIN_LENGTH characters, nothing else). Without a
    usable password, or within the window after an email sign-in, the current one is not needed.
    Keeps the session signed in. Raises ApiError."""
    from django.contrib.auth import update_session_auth_hash
    user = request.user
    password = password or ""
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        raise ApiError(422, "password_too_short", f"Şifre en az {settings.PASSWORD_MIN_LENGTH} karakter olmalı.")
    if len(password) > 128:
        raise ApiError(422, "validation_error", "Şifre en fazla 128 karakter olabilir.")
    if user.has_usable_password() and not in_password_window(request) and not user.check_password(current_password or ""):
        raise ApiError(403, "current_password_invalid", "Mevcut şifre hatalı.")
    user.set_password(password)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)   # stays signed in
    request.session.pop(SESSION_PASSWORD_UNTIL, None)
    log.info("password set: %s", user.label)


def password_sign_in(identifier: str, password: str, role: str):
    """Phone or email + password. Adds `role` like a code sign-in does (not organizer for an uploader entry).
    -> (user, uploader entry). Raises ApiError."""
    from django.contrib.auth import authenticate
    if role not in Role.values:
        raise ApiError(422, "invalid_role", f"role must be one of {', '.join(Role.values)}.")
    key = (identifier or "").strip().lower()
    if not key or not password:
        raise ApiError(422, "validation_error", "Telefon/e-posta ve şifre gerekli.")
    from common.ratelimit import hits
    if hits(f"pwfail:{key}", 900) >= settings.PASSWORD_SIGN_IN_MAX_FAILS:
        raise ApiError(429, "rate_limit_exceeded", "Çok fazla hatalı deneme. Biraz sonra tekrar dene "
                                                   "ya da e-posta ile giriş yap.")
    user = authenticate(username=identifier, password=password)
    if user is None:
        allow(f"pwfail:{key}", settings.PASSWORD_SIGN_IN_MAX_FAILS, 900)   # counts the failure
        raise ApiError(422, "invalid_credentials", "Telefon/e-posta veya şifre hatalı.")
    uploader_entry = uploader.is_uploader_entry(user, role, None)
    if uploader_entry:
        uploader.touch_last_login(uploader.user_identity(user))
    flag = ROLE_FLAGS["participant" if uploader_entry else role]
    if not getattr(user, flag):
        setattr(user, flag, True)
        user.save(update_fields=[flag])
    return user, uploader_entry
