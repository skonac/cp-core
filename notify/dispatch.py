"""What the rest of cp-core calls. design/notifications.md.

    from notify import dispatch
    dispatch.push(user, "new_matches", "Derya & Can", "12 yeni fotoğrafın var", url="/e/RK238", event=event)
    dispatch.sms(user.phone, "…", kind="event_end", user=user, event=event)
    dispatch.otp(phone, code)          # sent here and now; no row

sms/email/push return at once: they write the rows, and the sender (notify/sender.py) sends them after
the transaction commits (nothing is announced for data that was rolled back).
"""
from django.conf import settings
from django.db import transaction

from . import sender
from .models import Notification, PushDevice
from .providers import SendError, send_sms


def _queue_after_commit(rows: list[Notification]) -> list[Notification]:
    ids = [r.pk for r in rows]
    if ids:
        transaction.on_commit(lambda: sender.queue(ids))
    return rows


def sms(phone: str, text: str, kind: str, user=None, event=None) -> Notification:
    row = Notification.objects.create(kind=kind, channel=Notification.Channel.SMS, to=phone, text=text,
                                      user=user, event=event)
    return _queue_after_commit([row])[0]


def email(address: str, subject: str, text: str, kind: str, user=None, event=None) -> Notification:
    row = Notification.objects.create(kind=kind, channel=Notification.Channel.EMAIL, to=address, title=subject,
                                      text=text, user=user, event=event)
    return _queue_after_commit([row])[0]


def push(user, kind: str, title: str, text: str, url: str = "", event=None) -> list[Notification]:
    """One row per device of the user; none (and nothing sent) when they have no device."""
    rows = [Notification.objects.create(kind=kind, channel=Notification.Channel.PUSH, to=f"device {d.pk}",
                                        device=d, title=title, text=text, url=url, user=user, event=event)
            for d in PushDevice.objects.filter(user=user)]
    return _queue_after_commit(rows)


def otp(phone: str, code: str) -> str:
    """Send a sign-in code now, in the caller's thread (the user is waiting for it). No Notification row:
    the code lives only in accounts.OtpChallenge. -> provider name. Raises SendError."""
    provider, _ = send_sms(phone, settings.OTP_SMS_TEXT.format(code=code, domain=settings.SITE_DOMAIN), "otp")
    return provider


__all__ = ["sms", "email", "push", "otp", "SendError"]
