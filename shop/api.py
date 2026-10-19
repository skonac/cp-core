"""Shop API (/api/shop/…): the products on sale, buying one, the purchase history.
For organizer accounts (credits are spent on photo uploads). Shapes and codes: docs/api.md §3.7.
One product per purchase, no cart. Payment: shop/payments.py (simulated until iyzico is connected)."""
import hashlib

from ninja import Router
from ninja.security import django_auth

from accounts.models import User
from common.api import ApiError, CamelSchema, errors, respond

from . import schemas as S
from .models import Product, Purchase
from .payments import PaymentError, buy

shop_router = Router(tags=["shop"], auth=django_auth)


def _organizer(request) -> User:
    if not (request.user.is_organizer or request.user.is_staff):
        raise ApiError(403, "organizer_access_required", "Bu işlem için organizatör hesabı gerekli.")
    return request.user


def _balance(user) -> int:
    return User.objects.values_list("credit_balance", flat=True).get(pk=user.pk)


def _on_sale() -> list[Product]:
    return list(Product.objects.filter(is_active=True))


def _pricing_version(products) -> str:
    text = ";".join(f"{p.pk}:{p.credits}:{p.price}:{p.currency}" for p in products)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def purchase_json(p: Purchase) -> dict:
    return {"id": str(p.pk), "productId": str(p.product_id), "productName": p.product_name,
            "creditAmount": p.credits, "amount": float(p.amount), "currency": p.currency, "status": p.status,
            "purchasedAt": p.created_at.isoformat(), "paidAt": p.paid_at.isoformat() if p.paid_at else None}


@shop_router.get("/products", response={200: S.CatalogResponse, **errors(401, 403)})
def products(request):
    """The products on sale (managed in the admin), the balance and the price version to buy with.

    Errors: 403 organizer_access_required."""
    user = _organizer(request)
    on_sale = _on_sale()
    top_unit = max((float(p.price) / p.credits for p in on_sale if p.credits), default=0)
    out = []
    for p in on_sale:
        unit = float(p.price) / p.credits if p.credits else 0
        out.append({"id": str(p.pk), "name": p.name, "title": p.title or None, "description": p.description or None,
                    "creditAmount": p.credits, "price": float(p.price), "currency": p.currency,
                    "unitPrice": round(unit, 4),
                    "discountPercent": round((1 - unit / top_unit) * 100) if top_unit else 0,
                    "featured": p.is_featured})
    return respond(request, "products_retrieved", {
        "creditBalance": _balance(user), "pricingVersion": _pricing_version(on_sale), "products": out})


@shop_router.get("/purchases", response={200: S.PurchaseListResponse, **errors(401, 403)})
def purchases(request):
    """This account's purchases, newest first (max 100).

    Errors: 403 organizer_access_required."""
    user = _organizer(request)
    rows = Purchase.objects.filter(user=user).order_by("-created_at")
    return respond(request, "purchases_retrieved",
                   {"purchases": [purchase_json(p) for p in rows[:100]], "total": rows.count()})


class PurchaseIn(CamelSchema):
    product_id: str
    pricing_version: str


@shop_router.post("/purchases", response={201: S.PurchaseResponse, **errors(401, 403, 404, 409, 422, 501)})
def create_purchase(request, data: PurchaseIn):
    """Buy one product: checks it is on sale and the price version, takes the payment, adds the
    credits. Development servers simulate the payment: it always succeeds at once. With the real
    payment provider (iyzico, not connected yet) this step will hand back a payment page instead;
    the credits then arrive after the payment.

    Errors: 403 organizer_access_required · 404 not_found (product not on sale) ·
    409 pricing_changed (reload the products) · 501 payment_not_available."""
    user = _organizer(request)
    on_sale = _on_sale()
    product = next((p for p in on_sale if str(p.pk) == data.product_id), None)
    if product is None:
        raise ApiError(404, "not_found", "Ürün bulunamadı.")
    if data.pricing_version != _pricing_version(on_sale):
        raise ApiError(409, "pricing_changed", "Fiyatlar güncellendi; ürünleri yeniden yükle.")
    try:
        purchase = buy(user, product, request.META.get("REMOTE_ADDR"))
    except PaymentError as e:
        raise ApiError(501, e.code, e.message) from None
    return respond(request, "purchase_completed",
                   {"purchase": purchase_json(purchase), "creditBalance": _balance(user)}, status=201)
