"""Buying a product (design/core.md, "Shop").

    buy(user, product, ip) -> Purchase     the one place where a purchase adds credits

PAYMENT_PROVIDER = "simulated" (non-production only; `manage.py check` refuses it with DEBUG off):
every purchase succeeds at once, as if the provider had answered "paid". "iyzico" is not connected
yet; its flow will be: create the Purchase (pending) -> initialize the checkout form with the
conversation_id -> the organizer pays on iyzico's page -> iyzico calls our callback with the token ->
we retrieve the result -> complete() or fail(). Credits are added only by complete().
"""
import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from credits.models import CreditTransaction

from .models import Product, Purchase

log = logging.getLogger("cp.payments")


class PaymentError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message


def _new(user, product: Product, ip: str | None, billing: dict | None) -> Purchase:
    return Purchase.objects.create(
        user=user, product=product, product_name_tr=product.name_tr, product_name_en=product.name_en,
        credits=product.credits,
        list_price=product.price, amount=product.price, currency=product.currency,
        provider=settings.PAYMENT_PROVIDER, conversation_id="cp_" + secrets.token_hex(12), ip=ip or None,
        billing=billing)


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


def buy(user, product: Product, ip: str | None = None, billing: dict | None = None) -> Purchase:
    """billing: the buyer's billing profile (API shape), kept with the purchase."""
    if settings.PAYMENT_PROVIDER == "simulated":
        purchase = _new(user, product, ip, billing)
        return complete(purchase, payment_id=f"SIM-{purchase.pk}", response={"simulated": True, "status": "success"})
    raise PaymentError("payment_not_available", "Ödeme henüz kullanılamıyor.")
