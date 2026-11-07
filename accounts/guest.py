"""Guest access (design/core.md, "Email sign-up and guest access"): a participant without an account.

Someone with an event's QR starts a guest session for that event: a User row with no phone, no email and
no password (is_guest, guest_event), signed in to this browser. It works like a participant there
(consent, selfie, my photos, downloads) and nowhere else: no other event, no profile, no password, no
shop, no organizer pages (it has none of those roles). After GUEST_TTL_HOURS the guest is deleted with its
selfies, faces, "not me" choices, downloads log and membership; the purge runs whenever a new guest
starts (no periodic job), and an expired guest is signed out on its next request.

Switches: CP_GUEST_ACCESS (everything) and Event.is_guest_access (one event), both needed, and the event
must be open. Turning either off locks existing guests out at once.

Selfie uploads by a guest need a Cloudflare Turnstile token and are limited to GUEST_SELFIE_MAX per
GUEST_SELFIE_WINDOW seconds per guest.
"""
import json
import logging
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login, logout
from django.db import transaction
from django.utils import timezone

from common.api import ApiError
from common.ratelimit import allow

from .backends import SESSION_BACKEND
from .models import User

log = logging.getLogger("cp.guest")

TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def allowed(event) -> bool:
    """May guests use this event now?"""
    from events.models import Event
    return settings.GUEST_ACCESS and event.is_guest_access and event.status == Event.Status.OPEN


def is_expired(user) -> bool:
    return user.created_at < timezone.now() - timedelta(hours=settings.GUEST_TTL_HOURS)


def purge_expired() -> int:
    """Delete guests older than GUEST_TTL_HOURS, with their selfie files. -> how many."""
    from media.models import Image
    from media.storage import soft_delete
    cutoff = timezone.now() - timedelta(hours=settings.GUEST_TTL_HOURS)
    n = 0
    for user in User.objects.filter(is_guest=True, created_at__lt=cutoff)[:500]:
        with transaction.atomic():
            for image in Image.objects.filter(owner=user, kind=Image.Kind.SELFIE, deleted_at__isnull=True):
                soft_delete(image)    # files go after the commit
            user.delete()             # rows: selfies, faces, rejections, downloads, membership, send log
        n += 1
    if n:
        log.info("purged %d expired guests", n)
    return n


def start(request, event) -> User:
    """Sign this browser in as a guest of `event` (a signed-in guest of the same event keeps its session).
    Raises ApiError 403 guest_access_disabled."""
    if not allowed(event):
        raise ApiError(403, "guest_access_disabled", "Bu etkinlikte misafir girişi kapalı. Giriş yapman gerekiyor.")
    user = request.user
    if user.is_authenticated and user.is_guest and user.guest_event_id == event.pk and not is_expired(user):
        return user
    purge_expired()
    user = User.objects.create_user(is_guest=True, guest_event=event, is_participant=True)
    login(request, user, backend=SESSION_BACKEND)
    from events.participant import touch_membership   # events imports accounts: import here
    touch_membership(user, event)
    log.info("guest %s started for event %s", user.pk, event.code)
    return user


def sign_out_if_expired(request) -> None:
    """Middleware hook: an expired guest is signed out (the purge deletes it later)."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_guest and is_expired(user):
        logout(request)


def check_turnstile(token: str, ip: str | None) -> bool:
    secret = settings.TURNSTILE_SECRET
    if secret == settings.TURNSTILE_TEST_SECRET:
        return True   # Cloudflare's always-pass test key: no network call (dev, tests)
    if not token:
        return False
    data = urllib.parse.urlencode({"secret": secret, "response": token, **({"remoteip": ip} if ip else {})}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TURNSTILE_URL, data=data), timeout=8) as r:
            result = json.loads(r.read())
    except Exception as e:   # Cloudflare unreachable: refuse (the user can retry)
        log.warning("turnstile check failed: %s", e)
        return False
    if not result.get("success"):
        log.info("turnstile refused: %s", result.get("error-codes"))
    return bool(result.get("success"))


def before_selfie(request, token: str) -> None:
    """For a guest's selfie upload: the Turnstile token, then the per-guest rate. Raises ApiError.
    Does nothing for accounts."""
    user = request.user
    if not user.is_guest:
        return
    if not check_turnstile(token, request.META.get("REMOTE_ADDR")):
        raise ApiError(403, "turnstile_failed", "Doğrulama başarısız. Sayfayı yenileyip tekrar dene.")
    if not allow(f"guest-selfie:{user.pk}", settings.GUEST_SELFIE_MAX, settings.GUEST_SELFIE_WINDOW):
        raise ApiError(429, "rate_limit_exceeded", "Çok sık selfie yüklendi. Birkaç dakika sonra tekrar dene.")


def forbid(request) -> None:
    """For endpoints guests may not use (profile, password)."""
    if request.user.is_authenticated and request.user.is_guest:
        raise ApiError(403, "guest_not_allowed", "Misafir olarak bu işlem yapılamaz. Hesap oluştur.")
