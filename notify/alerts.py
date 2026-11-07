"""Admin alerts to our Telegram group: instant "something needs a human" messages for us, not for users
(users get SMS / WhatsApp / email through dispatch.py). design/notifications.md.

    from notify.alerts import alert
    alert("Worker silent for 10 min", key="worker-silent")

    POST https://api.telegram.org/bot<token>/sendMessage     chat_id, text (plain text, no parse_mode)

alert() never raises and never waits for Telegram: the post runs on a short-lived daemon thread. Everything is
held in RAM (no table, no disk writes): the same key is sent at most once per DEDUPE_SECONDS, and at most
MAX_PER_MINUTE messages leave in any minute; a restart forgets both, which is fine.

CP_TELEGRAM_DELIVERY: "log" (default; the text goes to the log only) | "live" (needs CP_TELEGRAM_BOT_TOKEN and
CP_TELEGRAM_ADMIN_CHAT_ID). A basic Telegram group that is upgraded to a supergroup gets a new chat id: the
old one then answers "group chat was upgraded" and names the new id (shown in the error below).
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
TIMEOUT = 5             # seconds, connect + answer
DEDUPE_SECONDS = 300    # the same key is not repeated within this time
MAX_PER_MINUTE = 10     # messages out, all keys together
MAX_TEXT = 3500         # Telegram's limit is 4096 characters
RETRYABLE = {408, 429, 500, 502, 503, 504}

_lock = threading.Lock()
_last_by_key: dict[str, float] = {}
_recent: list[float] = []


def _reset() -> None:
    """Forget the dedupe and rate-limit state (tests)."""
    with _lock:
        _last_by_key.clear()
        _recent.clear()


def send(text: str) -> int:
    """Post one message to the admin group, whatever CP_TELEGRAM_DELIVERY says -> the Telegram message_id.
    Raises SendError (retry=True for network trouble, 429 and 5xx)."""
    token, chat = settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_ADMIN_CHAT_ID
    if not (token and chat):
        raise SendError("telegram: CP_TELEGRAM_BOT_TOKEN / CP_TELEGRAM_ADMIN_CHAT_ID not set")
    body = json.dumps({"chat_id": chat, "text": text[:MAX_TEXT], "disable_web_page_preview": True}).encode()
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


def _deliver(text: str) -> None:
    try:
        send(text)
    except SendError as e:
        log.warning("admin alert not delivered: %s", e)
    except Exception:       # an alert must never break its caller, whatever happens
        log.exception("admin alert failed")


def alert(text: str, key: str | None = None, wait: bool = False) -> bool:
    """Tell the admin group. `key` names the problem (default: the text itself) so a repeating one is sent once
    per DEDUPE_SECONDS. `wait=True` posts in the caller (tests, management commands). Returns whether a
    message was sent (or logged, in "log" mode); never raises."""
    try:
        text = f"[{settings.SITE_DOMAIN}] {text}"
        if settings.TELEGRAM_DELIVERY != "live":
            log.warning("ADMIN ALERT (log only): %s", text)
            return True
        now = time.monotonic()
        with _lock:
            if now - _last_by_key.get(key or text, -DEDUPE_SECONDS) < DEDUPE_SECONDS:
                return False
            _recent[:] = [t for t in _recent if now - t < 60]
            if len(_recent) >= MAX_PER_MINUTE:
                log.warning("admin alert dropped (rate limit): %s", text)
                return False
            _recent.append(now)
            _last_by_key[key or text] = now
            if len(_last_by_key) > 500:     # forget old keys
                for k in [k for k, t in _last_by_key.items() if now - t >= DEDUPE_SECONDS]:
                    del _last_by_key[k]
        if wait:
            _deliver(text)
        else:
            threading.Thread(target=_deliver, args=(text,), daemon=True, name="admin-alert").start()
        return True
    except Exception:
        log.exception("admin alert failed")
        return False
