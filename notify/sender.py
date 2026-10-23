"""The sender: a small thread pool inside the cp-core process (runserver now, each gunicorn process later).
Request threads only write Notification rows and queue their ids here (after the commit); these threads
make the provider calls, so a slow or hanging provider never holds a request. design/notifications.md.

A row is claimed by moving it pending -> sending in one UPDATE, so a row queued twice (normal path and
the restart pickup) is sent once. Rows a dead process left behind (pending, or sending for too long)
are queued again by resume(), run once per process on its first queue() call.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connections
from django.db.models import F, Q
from django.utils import timezone

from .models import Notification, PushDevice
from .providers import SendError, send_email, send_push, send_sms

log = logging.getLogger("cp.notify")
MAX_ATTEMPTS = 3
STALE_SENDING = timedelta(minutes=5)   # a claim older than this belongs to a dead process

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_resumed = False
_last_purge = None


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=settings.NOTIFY_THREADS, thread_name_prefix="notify")
        return _pool


def queue(ids) -> None:
    """Send these rows: on the pool ("thread"), or right here ("inline": tests, management commands)."""
    global _resumed
    ids = list(ids)
    if not _resumed:
        _resumed = True
        ids += [i for i in resume_ids() if i not in ids]
    _purge_now_and_then()
    for row_id in ids:
        if settings.NOTIFY_SENDER == "inline":
            deliver(row_id)
        else:
            _get_pool().submit(_deliver_in_thread, row_id)


def resume_ids() -> list[int]:
    """Rows left behind: pending for over a minute, or claimed by a sender that never finished."""
    now = timezone.now()
    return list(Notification.objects.filter(
        Q(status=Notification.Status.PENDING, created_at__lt=now - timedelta(minutes=1))
        | Q(status=Notification.Status.SENDING, claimed_at__lt=now - STALE_SENDING))
        .values_list("id", flat=True)[:1000])


def _deliver_in_thread(row_id: int) -> None:
    close_old_connections()        # our own thread: Django only manages connections around requests
    try:
        deliver(row_id)
    except Exception:
        log.exception("notification %s: sender crashed", row_id)
    finally:
        connections.close_all()    # this thread's connections only; sends are rare, a new connection is cheap


def deliver(row_id: int) -> None:
    now = timezone.now()
    claimed = (Notification.objects
               .filter(Q(status=Notification.Status.PENDING)
                       | Q(status=Notification.Status.SENDING, claimed_at__lt=now - STALE_SENDING), pk=row_id)
               .update(status=Notification.Status.SENDING, claimed_at=now, attempts=F("attempts") + 1))
    if not claimed:
        return                     # sent, failed, or another thread has it
    row = Notification.objects.select_related("device").get(pk=row_id)
    try:
        if row.channel == Notification.Channel.SMS:
            provider, ref = send_sms(row.to, row.text, row.kind)
        elif row.channel == Notification.Channel.EMAIL:
            provider, ref = send_email(row.to, row.title, row.text, row.kind)
        else:
            if row.device is None:
                raise SendError("push device removed")
            provider, ref = send_push(row.device, row.title, row.text, row.url)
    except SendError as e:
        retry = e.retry and row.attempts < MAX_ATTEMPTS
        row.status = Notification.Status.PENDING if retry else Notification.Status.FAILED
        row.error = str(e)[:2000]
        row.save(update_fields=["status", "error"])
        if e.gone and row.device_id:
            PushDevice.objects.filter(pk=row.device_id).delete()
        elif row.device_id:
            PushDevice.objects.filter(pk=row.device_id).update(failures=F("failures") + 1)
        log.warning("notification %s (%s to %s): %s%s", row.pk, row.kind, row.to, e, " (will retry)" if retry else "")
        return
    row.status, row.provider, row.provider_ref, row.sent_at = Notification.Status.SENT, provider, ref, timezone.now()
    row.save(update_fields=["status", "provider", "provider_ref", "sent_at"])
    if row.device_id:
        PushDevice.objects.filter(pk=row.device_id).update(last_success_at=row.sent_at, failures=0)


def _purge_now_and_then() -> None:
    """The log is temporary: rows older than NOTIFY_KEEP_DAYS go (at most once an hour per process)."""
    global _last_purge
    now = timezone.now()
    if _last_purge and now - _last_purge < timedelta(hours=1):
        return
    _last_purge = now
    Notification.objects.filter(created_at__lt=now - timedelta(days=settings.NOTIFY_KEEP_DAYS)).delete()
