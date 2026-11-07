"""Admin alerts: instant "a human should know" messages for us, routed by topic. Users get SMS / WhatsApp / email
through dispatch.py; this is for the admins. design/notifications.md, "Admin alerts".

    from notify.alerts import alert
    alert("images.fail", "Image 123 failed ...", key="max_attempts")

The code names a topic (<app>.<tag>, listed in TOPICS); the env file says where each topic goes:

    CP_NOTIFY_IMAGES_FAIL=telegram.critical
    CP_NOTIFY_SHOP_FAIL=telegram.critical, email.ggolem@gmail.com
    CP_NOTIFY_ACCOUNTS_NEW=none            # or comment the line out

Targets: telegram.critical / telegram.info (chat ids in CP_CHANNEL_TELEGRAM_*), email.<address> (through
dispatch.email), log (the log only), none. A topic without a line in the env is off (log only). A topic the
code uses but TOPICS does not list is a bug: it goes to telegram.critical so somebody notices.

alert() never raises and never waits for Telegram (a short-lived daemon thread posts). All state is in RAM
(no table, no disk writes): a topic+key is sent at most once per DEDUPE_SECONDS, and each target takes at most
RATE_PER_MINUTE messages a minute; a restart forgets both, which is fine.

CP_TELEGRAM_DELIVERY: "log" (default: nothing leaves for Telegram) | "live" (needs CP_TELEGRAM_BOT_TOKEN).
A basic Telegram group that is upgraded to a supergroup gets a new chat id: the old id then answers "group chat
was upgraded" and the error below names the new one.
"""
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request

from django.conf import settings

from .providers import SendError

log = logging.getLogger("cp.alerts")

# topic -> what it means. Add a topic here, then a line in .env.example, then alert() it from the code.
TOPICS = {
    "accounts.new": "a new account was created",
    "accounts.resetpass": "a password reset was requested",
    "events.new": "an organizer created an event",
    "shop.success": "a purchase was paid",
    "shop.fail": "a payment failed",
    "shop.stuck": "a purchase has been pending for too long",
    "images.fail": "an image ended up failed (not the uploader's unreadable file)",
    "worker.silent": "images are waiting and no worker has checked in",
    "notify.fail": "every provider of a channel (SMS, email) is failing",
    "system.error": "an unhandled server error",
    "system.disk": "the photo disk is nearly full",
    "contact.new": "a contact form message arrived",
    "report.daily": "the daily summary",
}

TIMEOUT = 5             # seconds, connect + answer
DEDUPE_SECONDS = 300    # the same topic+key is not repeated within this time
RATE_PER_MINUTE = {"telegram": 10, "email": 5}   # per target
MAX_TEXT = 3500         # Telegram's limit is 4096 characters

_lock = threading.Lock()
_last: dict[tuple, float] = {}
_recent: dict[str, list[float]] = {}


def _reset() -> None:
    """Forget the dedupe and rate-limit state (tests)."""
    with _lock:
        _last.clear()
        _recent.clear()


def routes(topic: str) -> list[str]:
    """The targets of a topic, from CP_NOTIFY_<APP>_<TAG>. No line, an empty value or "none": no targets."""
    raw = settings.NOTIFY_ROUTES.get(topic.replace(".", "_"), "")
    return [t for t in (x.strip() for x in raw.split(",")) if t and t.lower() != "none"]


def send_telegram(chat_id: str, text: str) -> int:
    """Post one message to a Telegram chat, whatever CP_TELEGRAM_DELIVERY says -> the message_id.
    Raises SendError (retry=True for network trouble, 429 and 5xx)."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not (token and chat_id):
        raise SendError("telegram: CP_TELEGRAM_BOT_TOKEN or the channel's chat id is not set")
    body = json.dumps({"chat_id": chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "cp-core"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:        # non-2xx: the body still says why
        status, raw = e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise SendError(f"telegram unreachable: {getattr(e, 'reason', e)}", retry=True)   # no URL: it holds the token
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = {}
    if status == 200 and answer.get("ok"):
        return int(answer["result"]["message_id"])
    message = answer.get("description") or repr(raw[:200])
    moved = (answer.get("parameters") or {}).get("migrate_to_chat_id")
    if moved:
        message += f" (new chat id: {moved})"
    raise SendError(f"telegram {status}: {message}", retry=status in RETRYABLE)


RETRYABLE = {408, 429, 500, 502, 503, 504}


def _post_telegram(chat_id: str, text: str) -> None:
    try:
        send_telegram(chat_id, text)
    except SendError as e:
        log.warning("admin alert not delivered: %s", e)
    except Exception:       # an alert must never break its caller, whatever happens
        log.exception("admin alert failed")


def _send_email(address: str, subject: str, text: str) -> None:
    from . import dispatch      # late: dispatch imports the models
    dispatch.email(address, subject, text, kind="admin_alert")


def _take(target: str) -> bool:
    """Rate limit per target (call with _lock held)."""
    now = time.monotonic()
    kind = target.split(".", 1)[0]
    recent = _recent.setdefault(target, [])
    recent[:] = [t for t in recent if now - t < 60]
    if len(recent) >= RATE_PER_MINUTE.get(kind, 10):
        return False
    recent.append(now)
    return True


def alert(topic: str, text: str, key: str | None = None, wait: bool = False) -> bool:
    """Tell the admins about `topic`. `key` names the problem (default: the text) so a repeating one goes out once
    per DEDUPE_SECONDS. `wait=True` posts to Telegram in the caller (tests, management commands).
    Returns whether it went to at least one target; never raises."""
    try:
        if topic in TOPICS:
            targets = routes(topic)
        else:
            log.error("alert with an unknown topic %r: %s", topic, text)
            targets, text = ["telegram.critical"], f"(unknown topic {topic!r}) {text}"
        message = f"[{settings.SITE_DOMAIN}] {topic}: {text}"
        if not targets:
            log.info("alert %s (no route): %s", topic, text)
            return False
        now = time.monotonic()
        with _lock:
            if now - _last.get((topic, key or text), -DEDUPE_SECONDS) < DEDUPE_SECONDS:
                return False
            allowed = [t for t in targets if _take(t)]
            for t in set(targets) - set(allowed):
                log.warning("admin alert dropped (rate limit, %s): %s", t, message)
            if allowed:
                _last[(topic, key or text)] = now
                if len(_last) > 500:        # forget old keys
                    for k in [k for k, t in _last.items() if now - t >= DEDUPE_SECONDS]:
                        del _last[k]
        sent = False
        for target in allowed:
            kind, _, name = target.partition(".")
            kind = kind.lower()
            if kind == "log":
                log.warning("ADMIN ALERT: %s", message)
            elif kind == "telegram":
                chat = settings.TELEGRAM_CHANNELS.get(name.lower(), "")
                if settings.TELEGRAM_DELIVERY != "live" or not chat:
                    log.warning("ADMIN ALERT (not sent to %s: %s): %s", target,
                                "delivery is log" if chat else "no chat id", message)
                elif wait:
                    _post_telegram(chat, message)
                else:
                    threading.Thread(target=_post_telegram, args=(chat, message), daemon=True,
                                     name="admin-alert").start()
            elif kind == "email" and name:
                try:
                    _send_email(name, f"[{settings.SITE_DOMAIN}] {topic}", text)
                except Exception:
                    log.exception("admin alert email to %s failed", name)
                    continue
            else:
                log.warning("admin alert: unknown target %r for %s", target, topic)
                continue
            sent = True
        return sent
    except Exception:
        log.exception("admin alert failed")
        return False
