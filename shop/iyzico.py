"""iyzico checkout form (hosted payment page), through the official iyzipay package.

    initialize(purchase, user, profile, callback_url) -> dict    iyzico's answer (token, paymentPageUrl, ...)
    retrieve(purchase, token) -> dict                            the payment result for a token

Flow (design/core.md, "Shop"): POST /api/shop/purchases creates a pending Purchase and calls
initialize(); the browser goes to paymentPageUrl; after paying, iyzico sends the browser back to
callback_url with the token; the callback view calls retrieve() and completes or fails the purchase.
iyzico's server also notifies /api/shop/iyzico/webhook (panel: "İşyeri bildirimleri URL") in case the
browser never returns; that view does the same retrieve() (shop/views.py).

Keys come from the env per provider: iyzico-sandbox -> CP_IYZICOSANDBOX_API_KEY / _SECRET_KEY / _BASE_URL,
iyzico-production -> CP_IYZICOPRODUCTION_*. The package's own connect() has no timeout and shares one
header dict between all calls; _Client below replaces it (same signing, own headers, a timeout).
"""
import http.client
import json
import logging
from decimal import Decimal

import iyzipay
from django.conf import settings

log = logging.getLogger("cp.iyzico")

TIMEOUT = 20   # seconds per call to iyzico
NO_TCKN = "11111111111"   # placeholder identityNumber for a company buyer without a TC kimlik no (to confirm with iyzico)


class IyzicoError(Exception):
    def __init__(self, code: str, message: str, response: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.response = code, message, response


class _Client:
    """Mixin over an iyzipay resource class: same request signing, a timeout, per-call headers, JSON back."""

    def connect(self, method, url, options, request_body_dict=None):
        body = json.dumps(request_body_dict)
        self.header = dict(type(self).header)            # own copy: the package mutates a class-level dict
        headers = self.get_http_header(url, options, body)
        connection = http.client.HTTPSConnection(options["base_url"], timeout=TIMEOUT)
        try:
            connection.request(method, url, body, headers)
            response = connection.getresponse()
            raw = response.read().decode("utf-8", "replace")
        except OSError as e:   # DNS, refused, timeout, TLS
            raise IyzicoError("payment_provider_unreachable", f"{type(e).__name__}: {e}") from None
        finally:
            connection.close()
        try:
            return json.loads(raw)
        except ValueError:
            raise IyzicoError("payment_provider_error", f"HTTP {response.status}, not JSON: {raw[:300]}") from None


class _Initialize(_Client, iyzipay.CheckoutFormInitialize):
    pass


class _Retrieve(_Client, iyzipay.CheckoutForm):
    pass


def options() -> dict:
    return {"api_key": settings.IYZICO_API_KEY, "secret_key": settings.IYZICO_SECRET_KEY,
            "base_url": settings.IYZICO_BASE_URL}


def _money(value) -> str:
    return str(Decimal(value).quantize(Decimal("0.01")))


def _send(resource, method: str, body: dict) -> dict:
    """One call to iyzico; a failure answer raises IyzicoError. Tests replace this function."""
    answer = getattr(resource(), method)(body, options())
    if answer.get("status") != "success":
        raise IyzicoError("payment_provider_error", answer.get("errorMessage") or "iyzico refused the request",
                          answer)
    return answer


def build_initialize_request(purchase, user, profile, callback_url: str) -> dict:
    """The checkout form request: buyer and billing address from the billing profile, one VIRTUAL item."""
    name = f"{profile.first_name} {profile.last_name}".strip()
    city, country = profile.city, profile.country or "Türkiye"
    address = f"{profile.address}, {profile.district}"
    price = _money(purchase.amount)
    return {
        "locale": "tr",
        "conversationId": purchase.conversation_id,
        "price": price,
        "paidPrice": price,
        "currency": purchase.currency,
        "basketId": f"purchase-{purchase.pk}",
        "paymentGroup": "PRODUCT",
        "callbackUrl": callback_url,
        "enabledInstallments": [1],
        "buyer": {
            "id": str(user.pk), "name": profile.first_name, "surname": profile.last_name,
            "gsmNumber": user.phone, "email": profile.email,
            "identityNumber": profile.identity_number or NO_TCKN,
            "registrationAddress": address, "city": city, "country": country,
            "zipCode": profile.postal_code or None, "ip": purchase.ip or "127.0.0.1",
        },
        "billingAddress": {
            "contactName": profile.company_name or name, "city": city, "country": country,
            "address": address, "zipCode": profile.postal_code or None,
        },
        "basketItems": [{
            "id": f"product-{purchase.product_id}", "name": purchase.product_name_tr, "category1": "Kontör",
            "itemType": "VIRTUAL", "price": price,
        }],
    }


def initialize(purchase, user, profile, callback_url: str) -> dict:
    body = build_initialize_request(purchase, user, profile, callback_url)
    answer = _send(_Initialize, "create", body)
    log.info("purchase %s: checkout form token %s", purchase.pk, answer.get("token"))
    return answer


def retrieve(purchase, token: str) -> dict:
    return _send(_Retrieve, "retrieve", {"locale": "tr", "conversationId": purchase.conversation_id, "token": token})
