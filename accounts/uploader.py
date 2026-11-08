"""Uploader sessions (accounts.UploaderAccess): who this session may upload for.

An access names a phone and/or an email.
- Phone: the uploader signs in with the organizer entry by OTP; without an account no User is created and
  the session only holds the verified phone (SESSION_KEY). A phone that has an account is simply signed in.
- Email (Nov 7): the organizer's invitation mail signs in to an account for that email (created if new);
  the account's verified email carries the rights. Later sign-ins: email + password, or a mail code.
Every uploader request re-checks the access rows, so revoking takes effect at once.
"""
from typing import NamedTuple

from django.db.models import Q

from . import kvkk
from .models import UploaderAccess

SESSION_KEY = "uploader_phone"


class Identity(NamedTuple):
    phone: str = ""
    email: str = ""

    def __bool__(self):
        return bool(self.phone or self.email)

    def q(self, prefix: str = "") -> Q:
        """Matches access rows of this phone or email (prefix: e.g. "uploaded_by__")."""
        q = Q(pk__in=[]) if not prefix else Q(**{prefix + "pk__in": []})
        if self.phone:
            q |= Q(**{prefix + "phone": self.phone})
        if self.email:
            q |= Q(**{prefix + "email": self.email})
        return q


def user_identity(user) -> Identity:
    """An account's phone and verified email."""
    if user is None or not user.is_authenticated or user.is_guest:
        return Identity()
    return Identity(user.phone or "", user.email if user.email and user.email_verified_at else "")


def session_identity(request) -> Identity:
    """The verified phone/email of this session for uploader purposes (empty when none)."""
    phone = request.session.get(SESSION_KEY, "")
    if phone:
        return Identity(phone, "")
    return user_identity(request.user)


def session_phone(request) -> str:
    return session_identity(request).phone


def active_accesses(who):
    """`who`: an Identity, or a phone (str)."""
    who = who if isinstance(who, Identity) else Identity(who or "", "")
    return (UploaderAccess.objects.filter(who.q(), status=UploaderAccess.Status.ACTIVE)
            .select_related("organizer").order_by("created_at"))


def touch_last_login(who: Identity) -> None:
    from django.utils import timezone
    UploaderAccess.objects.filter(who.q(), status=UploaderAccess.Status.ACTIVE).update(last_login_at=timezone.now())


def is_uploader_entry(user, role: str, event) -> bool:
    """An organizer-entry sign-in (no event) by an account that is not an organizer but some organizer's
    uploader: no organizer role is added; the client goes to the uploader home."""
    return (role == "organizer" and event is None and user is not None
            and not (user.is_organizer or user.is_staff) and active_accesses(user_identity(user)).exists())


def organizer_name(organizer) -> str:
    return organizer.full_name or "Organizatör"


def access_json(access: UploaderAccess) -> dict:
    org = access.organizer
    return {"id": str(access.pk), "organizerName": organizer_name(org), "uploaderName": access.full_name,
            # the organizer's balance, spent by this uploader's uploads (1 credit per photo)
            "creditBalance": org.credit_balance, "canUpload": org.credit_balance - org.effective_credit_floor >= 1}


def uploader_user_json(phone: str, accesses) -> dict:
    """The `user` object for an uploader session without an account (same shape as a user;
    `id` is empty, no roles, no consent, no selfie)."""
    first = next(iter(accesses), None)
    return {"id": "", "phoneNumber": phone, "fullName": first.full_name if first else None, "email": None,
            "profileCompleted": True, "accountRoles": [], "hasConsent": False, "consentVersion": None,
            "kvkk": kvkk.documents(), "selfieState": "no_selfie", "selfieThumbnailUrl": None,
            "isGuest": False}
