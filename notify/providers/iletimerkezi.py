"""İletiMerkezi (iletimerkezi.com): SMS to Turkish numbers. API: https://www.toplusmsapi.com (JSON variant).

    POST https://api.iletimerkezi.com/v1/send-sms/json   one order: one text to one or more numbers
    POST .../get-sender/json, .../get-balance/json         approved sender names, balance (sms_test command)

Auth: the API key and the hash shown in the panel (Ayarlar -> API), sent in every body. The answer's
HTTP status is the same as its status code (200 ok, 401 credentials, 402 balance, 450 sender, ...).
Delivery reports come to our webhook (notify/views.py), set in the panel.
"""
import json
import logging
import socket
import urllib.error
import urllib.request

from django.conf import settings

from . import SendError

log = logging.getLogger("cp.notify")
BASE_URL = "https://api.iletimerkezi.com/v1/"
TIMEOUT = 8   # seconds, connect + answer
# Status codes worth another try later (server side); everything else is final for this message.
RETRYABLE = {400, 404, 429, 500, 502, 503, 504}


def local_number(phone: str) -> str:
    """+905551234567 -> 5551234567 (the format İletiMerkezi expects)."""
    if not phone.startswith("+90") or len(phone) != 13:
        raise SendError(f"not a Turkish mobile number: {phone}")
    return phone[3:]


def _auth() -> dict:
    return {"key": settings.ILETIMERKEZI_API_KEY, "hash": settings.ILETIMERKEZI_API_HASH}


def call(path: str, request: dict) -> dict:
    """POST {"request": {...}} -> the "response" object. Raises SendError unless the status is 200."""
    body = json.dumps({"request": {"authentication": _auth(), **request}}).encode()
    req = urllib.request.Request(BASE_URL + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "cp-core"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw, http_status = r.read(), r.status
    except urllib.error.HTTPError as e:      # non-200: the body still has status code and message
        raw, http_status = e.read(), e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise SendError(f"iletimerkezi unreachable: {e}", retry=True)
    try:
        response = json.loads(raw)["response"]
        code = int(response["status"]["code"])
        message = response["status"].get("message", "")
    except (ValueError, KeyError, TypeError):
        raise SendError(f"iletimerkezi: unreadable answer (HTTP {http_status}): {raw[:200]!r}",
                        retry=http_status >= 500)
    if code != 200:
        raise SendError(f"iletimerkezi {code}: {message}", retry=code in RETRYABLE)
    return response


def sms(phone: str, text: str) -> str:
    """Send one SMS -> the order id. İYS off: OTP and notices about the user's own account are not commercial."""
    response = call("send-sms/json", {"order": {
        "sender": settings.ILETIMERKEZI_SENDER,
        "sendDateTime": "",
        "iys": "0",
        "iysList": "BIREYSEL",
        "message": {"text": text, "receipents": {"number": [local_number(phone)]}},   # sic: their spelling
    }})
    return str(response["order"]["id"])


def senders() -> list[str]:
    found = call("get-sender/json", {}).get("senders", {}).get("sender", [])
    return [found] if isinstance(found, str) else list(found)


def balance() -> dict:
    return call("get-balance/json", {}).get("balance", {})
