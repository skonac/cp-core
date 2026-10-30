"""Zoho CPaaS (formerly ZeptoMail), EU: transactional email from any address on our verified domain
(DKIM + CNAME). The main email route; Zoho Mail (zohomail.py) is the fallback. design/notifications.md.

    POST https://cpaas.zoho.eu/v1.1/email     one message
    Authorization: Zoho-enczapikey <key>      a fixed key from the CPaaS panel (no OAuth); valid until deleted

Answer: 201/200 with {"data": [{"code": "EM_104", ...}], "request_id": ...}; errors as
{"error": {"code": ..., "message": ..., "details": [...]}}. Transactional mail only (no marketing).
"""
import json
import logging
import socket
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings

from . import SendError

log = logging.getLogger("cp.notify")
URL = "https://cpaas.zoho.eu/v1.1/email"
TIMEOUT = 10        # seconds, connect + answer
RETRYABLE = {408, 429, 500, 502, 503, 504}


def _address(text: str) -> dict:
    """'Clever Photos <noreply@x.org>' -> {"address": "noreply@x.org", "name": "Clever Photos"}"""
    name, address = parseaddr(text)
    return {"address": address, "name": name} if name else {"address": address}


def email(address: str, subject: str, text: str) -> str:
    """Send one message -> the request_id. A text starting with "<" goes as HTML, anything else as plain text."""
    payload = {"from": _address(settings.CPAAS_FROM),
               "to": [{"email_address": _address(address)}],
               "subject": subject,
               "htmlbody" if text.lstrip().startswith("<") else "textbody": text}
    if settings.EMAIL_REPLY_TO:
        payload["reply_to"] = [_address(settings.EMAIL_REPLY_TO)]
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Zoho-enczapikey {settings.CPAAS_API_KEY}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "cp-core"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:        # non-2xx: the body still says why
        status, raw = e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise SendError(f"cpaas unreachable: {e}", retry=True)
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = {}
    if status in (200, 201) and "error" not in answer:
        return str(answer.get("request_id", ""))
    error = answer.get("error") or {}
    details = "; ".join(d.get("message", "") for d in error.get("details", []) if isinstance(d, dict))
    message = " - ".join(x for x in (error.get("code"), error.get("message"), details) if x) or repr(raw[:200])
    raise SendError(f"cpaas {status}: {message}", retry=status in RETRYABLE)
