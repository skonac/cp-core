"""Buying a product (design/core.md, "Shop").

    start(user, product, ip, billing, profile, callback_url, user_agent, price) -> (Purchase, page URL)
    finish_iyzico(token) -> Purchase       after iyzico's return
    complete(purchase, ...)                the one place where a purchase adds credits

PAYMENT_PROVIDER = "simulated" (non-production only): every purchase succeeds at once. "iyzico-sandbox" /
"iyzico-production": create the Purchase (pending) -> initialize iyzico's checkout form (shop/iyzico.py)
-> the organizer pays on iyzico's page -> iyzico sends the browser to our callback with the token ->
finish_iyzico() asks iyzico for the result -> complete() or fail(). Credits are added only by complete().
"""
import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from credits.models import CreditTransaction

from . import documents, iyzico, pricing
from .models import Product, Purchase

log = logging.getLogger("cp.payments")


class PaymentError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message


def _new(user, product: Product, ip: str | None, billing: dict | None, user_agent: str = "",
         price: "pricing.Price | None" = None) -> Purchase:
    """The pending purchase with the sales documents the buyer accepted (shop/documents.py), in one
    transaction: no purchase without its documents. price: shop/pricing.py (default: the user's own
    discount, no promo)."""
    price = price or pricing.price_for(product, user, None)
    with transaction.atomic():
        purchase = Purchase.objects.create(
            user=user, product=product, product_name_tr=product.name_tr, product_name_en=product.name_en,
            credits=product.credits,
            list_price=price.list_price, amount=price.final, currency=product.currency,
            discount_source=price.source, discount_percent=price.percent, promo_code=price.promo_code,
            provider=settings.PAYMENT_PROVIDER, conversation_id="cp_" + secrets.token_hex(12), ip=ip or None,
            billing=billing)
        documents.attach(purchase, user_agent)
    return purchase


def complete(purchase: Purchase, payment_id: str, response: dict | None = None) -> Purchase:
    """Payment confirmed: mark paid and add the credits, once (a second call changes nothing)."""
    with transaction.atomic():
        purchase = Purchase.objects.select_for_update().get(pk=purchase.pk)
        if purchase.status == Purchase.Status.PAID:
            return purchase
        purchase.status = Purchase.Status.PAID
        purchase.provider_payment_id = payment_id
        purchase.provider_response = response
        purchase.paid_at = timezone.now()
        purchase.save()
        CreditTransaction.objects.create(user_id=purchase.user_id, delta=purchase.credits,
                                         kind=CreditTransaction.Kind.PURCHASE, ref=f"purchase:{purchase.pk}",
                                         note=f"{purchase.product_name_tr} {purchase.amount} {purchase.currency}")
        User.objects.filter(pk=purchase.user_id).update(credit_balance=F("credit_balance") + purchase.credits)
    log.info("purchase %s paid: +%s credits for user %s", purchase.pk, purchase.credits, purchase.user_id)
    return purchase


def fail(purchase: Purchase, code: str, message: str = "", response: dict | None = None) -> Purchase:
    Purchase.objects.filter(pk=purchase.pk, status=Purchase.Status.PENDING).update(
        status=Purchase.Status.FAILED, error_code=code, error_message=message[:500], provider_response=response)
    purchase.refresh_from_db()
    return purchase


def start(user, product: Product, ip: str | None, billing: dict, profile, callback_url: str,
          user_agent: str = "", price: "pricing.Price | None" = None) -> tuple[Purchase, str | None]:
    """Begin a purchase. -> (purchase, payment page URL or None).
    simulated: paid at once, no page. iyzico-*: pending, the browser goes to iyzico's page; the
    callback (finish_iyzico) completes or fails it. Raises PaymentError when iyzico refuses or can't be reached."""
    provider = settings.PAYMENT_PROVIDER
    purchase = _new(user, product, ip, billing, user_agent, price)
    if provider == "simulated":
        return complete(purchase, payment_id=f"SIM-{purchase.pk}", response={"simulated": True, "status": "success"}), None
    try:
        answer = iyzico.initialize(purchase, user, profile, callback_url)
    except iyzico.IyzicoError as e:
        log.warning("purchase %s: iyzico initialize failed: %s", purchase.pk, e)
        fail(purchase, e.code, e.message, e.response)
        raise PaymentError(e.code, f"Ödeme başlatılamadı: {e.message}") from None
    Purchase.objects.filter(pk=purchase.pk).update(provider_token=answer.get("token", "")[:200],
                                                   provider_response=answer)
    purchase.refresh_from_db()
    return purchase, answer.get("paymentPageUrl")


def finish_iyzico(token: str) -> Purchase | None:
    """iyzico sent the browser back with this token: ask iyzico for the result, then paid or failed.
    Safe to call again (a finished purchase is returned as it is). If iyzico can't be asked right now
    the purchase stays pending (the payment may have gone through) and the error is noted."""
    purchase = Purchase.objects.filter(provider_token=token).first() if token else None
    if purchase is None or purchase.status != Purchase.Status.PENDING:
        return purchase
    try:
        answer = iyzico.retrieve(purchase, token)
    except iyzico.IyzicoError as e:
        log.warning("purchase %s: iyzico retrieve failed: %s", purchase.pk, e)
        Purchase.objects.filter(pk=purchase.pk).update(error_code=e.code, error_message=e.message[:500],
                                                       provider_response=e.response)
        purchase.refresh_from_db()
        return purchase
    ok = (answer.get("paymentStatus") == "SUCCESS"
          and answer.get("conversationId") == purchase.conversation_id
          and Decimal(str(answer.get("paidPrice", "0"))) == purchase.amount
          and answer.get("fraudStatus", 1) != -1)
    if ok:
        return complete(purchase, payment_id=str(answer.get("paymentId", "")), response=answer)
    log.warning("purchase %s: payment not accepted: %s %s", purchase.pk, answer.get("paymentStatus"),
                answer.get("errorMessage"))
    return fail(purchase, answer.get("errorCode") or "payment_failed",
                answer.get("errorMessage") or f"paymentStatus {answer.get('paymentStatus')}", answer)
