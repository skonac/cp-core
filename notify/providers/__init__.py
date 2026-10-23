"""Providers and routing. A provider is a plain function that sends one message and returns the
provider's reference (message/order id), or raises SendError. Routing is a few ifs, here.

    sms:   (phone E.164, text)            -> ref
    email: (address, subject, text)       -> ref
    push:  (device, title, text, url)     -> ref

Add a provider: write its function (own module), register it in the dict, route to it.
"""
from django.conf import settings

from . import log


class SendError(Exception):
    """retry: worth trying again later (timeout, provider down, 5xx). gone: the address/device no longer
    exists (drop the push device). Neither: final (bad number, no balance, rejected text)."""

    def __init__(self, message: str, retry: bool = False, gone: bool = False):
        super().__init__(message)
        self.retry, self.gone = retry, gone


SMS = {"log": log.sms}
EMAIL = {"log": log.email}
PUSH = {"log": log.push}


def sms_route(phone: str, purpose: str) -> list[str]:
    """Providers to try, in order (the next one only if the previous failed). purpose: "otp" or a kind."""
    if settings.SMS_DELIVERY == "log":
        return ["log"]
    if phone.startswith("+90"):
        return []            # iletimerkezi: next step
    return []                # outside Türkiye: Twilio, later


def email_route(address: str, purpose: str) -> list[str]:
    return ["log"]           # SendGrid, later


def push_route(device) -> list[str]:
    return ["log"]           # webpush (device.kind), next steps


def _first_that_works(names: list[str], table: dict, *args) -> tuple[str, str]:
    if not names:
        raise SendError("no provider for this address")
    error = None
    for name in names:
        try:
            return name, table[name](*args)
        except SendError as e:
            error = e
    raise error


def send_sms(phone: str, text: str, purpose: str) -> tuple[str, str]:
    """-> (provider, ref)"""
    return _first_that_works(sms_route(phone, purpose), SMS, phone, text)


def send_email(address: str, subject: str, text: str, purpose: str) -> tuple[str, str]:
    return _first_that_works(email_route(address, purpose), EMAIL, address, subject, text)


def send_push(device, title: str, text: str, url: str) -> tuple[str, str]:
    return _first_that_works(push_route(device), PUSH, device, title, text, url)
