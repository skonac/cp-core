"""The site's operation mode: a kill switch for incidents (staff page /dj/staff/mode/, or
`manage.py operation_mode`).

    normal      everything works
    restricted  payments are off: POST /api/shop/purchases answers 503 payments_disabled
    lockdown    only staff can use the site: everything else answers 503 site_locked

Staff are never blocked. Always open, in every mode: ALWAYS_OPEN below (payment callbacks so
payments in progress still get their credits, the face worker, provider reports, sign-in for
staff, the admin, health and the mode itself, so cp-web can show the state).

Read on every request, so it is cached in the process's RAM for CACHE_SECONDS: a change made in
another process (the command, a second gunicorn worker) takes effect within that time.
"""
import logging
import time

from django.db import transaction

from .models import OperationMode

log = logging.getLogger("cp.mode")

Mode = OperationMode.Mode
CACHE_SECONDS = 5

# What staff see on the page when picking a mode (internal text, English like the staff pages).
DESCRIPTIONS = {
    Mode.NORMAL: "Everything works.",
    Mode.RESTRICTED: "Payments are disabled: starting a purchase is refused (503 payments_disabled). "
                     "Everything else works, and payments already in progress still complete.",
    Mode.LOCKDOWN: "All access for non-staff accounts and visitors is disabled (503 site_locked, or a "
                   "temporarily closed page). Still open: staff sign-in, admin, staff pages, payment "
                   "callbacks, the face worker.",
}

ALWAYS_OPEN = (
    "/api/health",
    "/api/operation-mode",
    "/api/faceworker/",          # the face worker keeps processing the queue
    "/api/shop/iyzico/",         # callback + webhook: payments in progress still complete
    "/api/notify/",              # providers' delivery reports
    "/admin/",                   # has its own staff-only login
    "/dj/login/",                # staff sign in here; other accounts then meet the lock
    "/dj/logout/",
    "/static/",
)
PAYMENT_START = ("POST", "/api/shop/purchases")

_cached: tuple[str, float] = ("", 0.0)   # (mode, expires at, time.monotonic)


def current() -> str:
    """The current mode, from the process cache (at most CACHE_SECONDS old)."""
    global _cached
    mode, expires = _cached
    if time.monotonic() < expires:
        return mode
    row = OperationMode.objects.only("mode").first()
    mode = row.mode if row else Mode.NORMAL
    _cached = (mode, time.monotonic() + CACHE_SECONDS)
    return mode


def payments_enabled(user=None) -> bool:
    """What the shop tells this caller (catalog `paymentsEnabled`): the middleware's rule, staff always pass."""
    return current() == Mode.NORMAL or bool(user is not None and user.is_active and user.is_staff)


def latest() -> OperationMode | None:
    return OperationMode.objects.select_related("changed_by").first()


def set_mode(mode: str, reason: str = "", by=None, wait_alert: bool = False) -> OperationMode:
    """Record a change. A reason is required for restricted and lockdown, ignored for normal.
    wait_alert: send the admin alert before returning (the command: its process ends right after)."""
    global _cached
    if mode not in Mode.values:
        raise ValueError(f"unknown mode {mode!r}: one of {', '.join(Mode.values)}")
    reason = "" if mode == Mode.NORMAL else (reason or "").strip()
    if mode != Mode.NORMAL and not reason:
        raise ValueError("a reason is required for restricted and lockdown")
    old = current()
    row = OperationMode.objects.create(mode=mode, reason=reason, changed_by=by)
    _cached = (mode, time.monotonic() + CACHE_SECONDS)   # this process sees it at once
    who = f"account {by.pk}" if by else "the command line"
    log.warning("operation mode %s -> %s by %s: %s", old, mode, who, reason)
    from notify.alerts import alert   # no emails or phone numbers in alerts: the account id is enough
    text = f"{old} -> {mode} by {who}" + (f": {reason}" if reason else "")
    transaction.on_commit(lambda: alert("system.mode", text, key=f"mode-{row.pk}", wait=wait_alert))
    return row


def _reset() -> None:
    """Forget the cached mode (tests)."""
    global _cached
    _cached = ("", 0.0)
