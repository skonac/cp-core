"""Participant-side rules (design/core.md, "Event access"): membership, who may see what."""
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from media.matching import is_my_photo, selfie_state

from .models import Event, EventMember


def touch_membership(user, event) -> None:
    """Any event visit by a signed-in user: membership (once), participant role, activity time
    (written at most once an hour, so browsing doesn't write on every request)."""
    member, created = EventMember.objects.get_or_create(user=user, event=event)
    if not created and member.last_activity_at < timezone.now() - timedelta(hours=1):
        EventMember.objects.filter(pk=member.pk).update(last_activity_at=timezone.now())
    if not user.is_participant:
        user.is_participant = True
        user.save(update_fields=["is_participant"])


@dataclass
class Access:
    blocked: str = ""          # "", "closed", "expired", "consent_required"
    can_match: bool = False    # may see "my photos"
    can_browse: bool = False   # may see all photos
    matching: str = ""         # consent_required | matching_off | no_selfie | selfie_processing | selfie_rejected | ready


def access_for(user, event) -> Access:
    is_host = user.is_staff or event.owner_id == user.pk
    if event.status == Event.Status.EXPIRED:
        return Access(blocked="expired")
    if event.status == Event.Status.CLOSED and not is_host:
        return Access(blocked="closed")
    if event.is_consent_required and not user.is_consent and not is_host:
        return Access(blocked="consent_required")
    a = Access(can_browse=event.is_browse_all or is_host)   # the host always sees everything
    if not event.is_processing_faces:
        a.matching = "matching_off"
    elif not user.is_consent:
        a.matching = "consent_required"     # event lets them in without consent: browse-all only
    else:
        a.can_match = True
        a.matching = selfie_state(user)
    return a


def may_see_photo(user, event, image, access: Access) -> bool:
    return access.can_browse or (access.can_match and is_my_photo(user, image))


def public_status(event) -> str:
    """Our status -> the cp-web contract's names."""
    return {Event.Status.OPEN: "active", Event.Status.CLOSED: "access_closed",
            Event.Status.EXPIRED: "expired"}[event.status]
