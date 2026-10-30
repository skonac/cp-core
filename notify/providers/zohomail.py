"""Zoho Mail (EU data center): email through the Zoho Mail REST API, sent as one mailbox (or its aliases).
design/notifications.md "Email (Zoho Mail)".

    POST https://accounts.zoho.eu/oauth/v2/token                      refresh token -> access token (1 h)
    POST https://mail.zoho.eu/api/accounts/{accountId}/messages        send one message
    GET  https://mail.zoho.eu/api/accounts                             the account id and from-addresses (email_test)

Auth: OAuth. The refresh token came from a one-time browser consent (scopes ZohoMail.messages.CREATE,
ZohoMail.accounts.READ) and does not expire unless revoked. The access token is kept in memory (per
process), renewed shortly before it expires and once more on a 401. A rejected refresh token is final:
redo the consent (notifications.md) and put the new token in /etc/cp-core/env.
"""
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from . import SendError

log = logging.getLogger("cp.notify")
TOKEN_URL = "https://accounts.zoho.eu/oauth/v2/token"
API_URL = "https://mail.zoho.eu/api/"
TIMEOUT = 10        # seconds, connect + answer
EARLY = 300         # renew the access token this many seconds before it expires
RETRYABLE = {408, 429, 500, 502, 503, 504}

_lock = threading.Lock()
_token = {"value": "", "expires": 0.0}


def _http(req: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:        # non-2xx: the body still says why
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise SendError(f"zohomail unreachable: {e}", retry=True)


def _json(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _new_token() -> str:
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": settings.ZOHOMAIL_REFRESH_TOKEN,
                                   "client_id": settings.ZOHOMAIL_CLIENT_ID,
                                   "client_secret": settings.ZOHOMAIL_CLIENT_SECRET}).encode()
    status, raw = _http(urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                               headers={"User-Agent": "cp-core"}))
    answer = _json(raw)
    if status == 200 and answer.get("access_token"):
        _token["value"] = answer["access_token"]
        _token["expires"] = time.monotonic() + int(answer.get("expires_in", 3600))
        return _token["value"]
    if status in RETRYABLE or status >= 500:
        raise SendError(f"zohomail token: HTTP {status}", retry=True)
    # Zoho answers 200 with {"error": "invalid_code"} for a revoked/unknown refresh token
    error = answer.get("error") or f"HTTP {status}: {raw[:200]!r}"
    if error == "Access Denied":              # too many token requests in a short time
        raise SendError("zohomail token: rate limited", retry=True)
    log.error("zohomail: refresh token rejected (%s); redo the consent (design/notifications.md)", error)
    raise SendError(f"zohomail token rejected: {error} (redo the OAuth consent)")


def _access_token(renew: bool = False) -> str:
    with _lock:
        if renew or not _token["value"] or time.monotonic() > _token["expires"] - EARLY:
            return _new_token()
        return _token["value"]


def call(method: str, path: str, payload: dict | None = None) -> dict:
    """One API call -> the answer's "data". A 401 renews the access token once and tries again."""
    body = json.dumps(payload).encode() if payload is not None else None
    for renew in (False, True):
        req = urllib.request.Request(API_URL + path, data=body, method=method, headers={
            "Authorization": f"Zoho-oauthtoken {_access_token(renew)}", "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": "cp-core"})
        status, raw = _http(req)
        if status != 401:
            break
    answer = _json(raw)
    if status == 200:
        return answer.get("data", {})
    message = (answer.get("data") or {}).get("moreInfo") or answer.get("status", {}).get("description") \
        or repr(raw[:200])
    raise SendError(f"zohomail {status}: {message}", retry=status in RETRYABLE or status >= 500)


def email(address: str, subject: str, text: str) -> str:
    """Send one message -> Zoho's messageId. A text starting with "<" goes as HTML, anything else as plain text."""
    data = call("POST", f"accounts/{settings.ZOHOMAIL_ACCOUNT_ID}/messages", {
        "fromAddress": settings.ZOHOMAIL_FROM,
        "toAddress": address,
        "subject": subject,
        "content": text,
        "mailFormat": "html" if text.lstrip().startswith("<") else "plaintext",
        "encoding": "UTF-8",
    })
    return str(data.get("messageId", ""))


def accounts() -> list[dict]:
    """The accounts the token can see, with their ids and from-addresses (email_test --accounts)."""
    found = call("GET", "accounts")
    return found if isinstance(found, list) else [found]
