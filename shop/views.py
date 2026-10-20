"""iyzico's return: after paying (or giving up) on iyzico's page, the browser comes back here with
a token (form POST). We ask iyzico for the result (shop/payments.finish_iyzico), settle the purchase
and send the browser on to the shop page (settings.SHOP_RETURN_URL). Not a JSON endpoint: the browser
follows the redirect. No CSRF token: the POST comes from iyzico's page; the token is checked with iyzico.
"""
import logging

from django.conf import settings
from django.http import HttpResponseRedirect
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
