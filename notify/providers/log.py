"""The "log" provider: nothing leaves the server; the message goes to the log (dev, tests)."""
import itertools
import logging

log = logging.getLogger("cp.notify")
_ids = itertools.count(1)


def sms(phone: str, text: str) -> str:
    log.warning("SMS to %s (not sent, log provider): %s", phone, text.replace("\n", " | "))
    return f"log-{next(_ids)}"


def email(address: str, subject: str, text: str) -> str:
    log.warning("email to %s (not sent, log provider): %s | %s", address, subject, text[:200].replace("\n", " | "))
    return f"log-{next(_ids)}"


def push(device, title: str, text: str, url: str) -> str:
    log.warning("push to %s (not sent, log provider): %s | %s | %s", device, title, text, url)
    return f"log-{next(_ids)}"
