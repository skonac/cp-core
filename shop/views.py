"""iyzico calls us in two ways (design/core.md, "Shop"):

- iyzico_callback: the return. After paying (or giving up) on iyzico's page, the browser comes back here with
a token (form POST). We ask iyzico for the result (shop/payments.finish_iyzico), settle the purchase
and send the browser on to the shop page (settings.SHOP_RETURN_URL). Not a JSON endpoint: the browser
follows the redirect. No CSRF token: the POST comes from iyzico's page; the token is checked with iyzico.
- iyzico_webhook: iyzico's server notifies us of a payment (JSON), in case the browser never returns.
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .payments import finish_iyzico

log = logging.getLogger("cp.payments")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def iyzico_callback(request):
    token = (request.POST.get("token") or request.GET.get("token") or "").strip()
    purchase = finish_iyzico(token)
    if purchase is None:
        log.warning("iyzico callback with unknown token %r", token[:40])
        return HttpResponseRedirect(settings.SHOP_RETURN_URL.format(id="", status="unknown"))
    return HttpResponseRedirect(settings.SHOP_RETURN_URL.format(id=purchase.pk, status=purchase.status))


def _signature_ok(body: dict, given: str) -> bool:
    """X-IYZ-SIGNATURE-V3: hex HMAC-SHA256 (key = secret key) of secretKey + iyziEventType +
    iyziPaymentId + token + paymentConversationId + status (iyzico docs, "Webhook")."""
    secret = settings.IYZICO_SECRET_KEY
    message = secret + "".join(str(body.get(k, "")) for k in
                               ("iyziEventType", "iyziPaymentId", "token", "paymentConversationId", "status"))
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given.strip().lower())


@csrf_exempt
@require_http_methods(["POST"])
def iyzico_webhook(request):
    """iyzico's merchant notification ("İşyeri bildirimleri" URL in iyzico's panel): server to server,
    JSON, about 10-15 s after a payment; repeated about every 15 min until we answer 2xx. It covers a
    buyer who closes the tab before the browser return. The body is never trusted: we take the token
    and ask iyzico for the result (finish_iyzico), like the browser return; credits are added once,
    whichever arrives first. The signature (X-IYZ-SIGNATURE-V3, enabled by iyzico on request) is
    checked when present.
    200: handled, or not ours (logged; no retries). 400: not JSON. 401: wrong signature.
    503: iyzico could not be asked right now; the purchase stays pending and iyzico retries."""
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"status": "error", "reason": "invalid_json"}, status=400)
    signature = request.headers.get("X-IYZ-SIGNATURE-V3", "")
    if signature and not _signature_ok(body, signature):
        log.warning("iyzico webhook: wrong signature for token %r", str(body.get("token", ""))[:40])
        return JsonResponse({"status": "error", "reason": "invalid_signature"}, status=401)
    token = str(body.get("token") or "").strip()
    log.info("iyzico webhook: %s %s token %r conversation %r", body.get("iyziEventType"), body.get("status"),
             token[:40], body.get("paymentConversationId"))
    purchase = finish_iyzico(token)
    if purchase is None:
        log.warning("iyzico webhook with unknown token %r", token[:40])
        return JsonResponse({"status": "ignored"})
    if purchase.status == purchase.Status.PENDING and purchase.error_code == "payment_provider_unreachable":
        return JsonResponse({"status": "retry"}, status=503)
    return JsonResponse({"status": purchase.status})
