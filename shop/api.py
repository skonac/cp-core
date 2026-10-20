"""Shop API (/api/shop/…): the products on sale (public), buying one and the purchase history
(organizer accounts: credits are spent on photo uploads). Shapes and codes: docs/api.md §3.7.
One product per purchase, no cart. Payment: shop/payments.py (simulated until iyzico is connected)."""
import hashlib
from typing import Literal

from ninja import Router
from ninja.security import django_auth
from pydantic import Field

from accounts.models import User
from common.api import ApiError, CamelSchema, errors, respond

from . import schemas as S
from .billing import billing_errors
from .models import BillingProfile, Product, Purchase
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
    return {"id": str(p.pk), "productId": str(p.product_id),
            "productName": {"tr": p.product_name_tr, "en": p.product_name_en},
            "creditAmount": p.credits, "amount": float(p.amount), "currency": p.currency, "status": p.status,
            "purchasedAt": p.created_at.isoformat(), "paidAt": p.paid_at.isoformat() if p.paid_at else None}


@shop_router.get("/products", auth=None, response={200: S.CatalogResponse})
def products(request):
    """The products on sale (managed in the admin) and the price version to buy with. Public: also for
    the landing/pricing pages. `creditBalance` only with a session (null otherwise)."""
    on_sale = _on_sale()
    top_unit = max((float(p.price) / p.credits for p in on_sale if p.credits), default=0)
    out = []
    for p in on_sale:
        unit = float(p.price) / p.credits if p.credits else 0
        out.append({"id": str(p.pk), "name": {"tr": p.name_tr, "en": p.name_en},
                    "title": {"tr": p.title_tr, "en": p.title_en},
                    "description": {"tr": p.description_tr, "en": p.description_en},
                    "creditAmount": p.credits, "price": float(p.price), "currency": p.currency,
                    "unitPrice": round(unit, 4),
                    "discountPercent": round((1 - unit / top_unit) * 100) if top_unit else 0,
                    "featured": p.is_featured})
    balance = _balance(request.user) if request.user.is_authenticated else None
    return respond(request, "products_retrieved", {
        "creditBalance": balance, "pricingVersion": _pricing_version(on_sale), "products": out})


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

    Needs a complete billing profile (`PUT /api/me/billing`); it is copied into the purchase.

    Errors: 403 organizer_access_required · 404 not_found (product not on sale) ·
    409 pricing_changed (reload the products) · 422 billing_profile_required · 501 payment_not_available."""
    user = _organizer(request)
    on_sale = _on_sale()
    product = next((p for p in on_sale if str(p.pk) == data.product_id), None)
    if product is None:
        raise ApiError(404, "not_found", "Ürün bulunamadı.")
    if data.pricing_version != _pricing_version(on_sale):
        raise ApiError(409, "pricing_changed", "Fiyatlar güncellendi; ürünleri yeniden yükle.")
    profile = _profile(user)
    if profile is None or not profile.is_complete:
        raise ApiError(422, "billing_profile_required", "Önce fatura bilgilerini doldur.")
    try:
        purchase = buy(user, product, request.META.get("REMOTE_ADDR"), billing=billing_json(profile)["billing"])
    except PaymentError as e:
        raise ApiError(501, e.code, e.message) from None
    return respond(request, "purchase_completed",
                   {"purchase": purchase_json(purchase), "creditBalance": _balance(user)}, status=201)


# ---------------------------------------------------------------------------- billing profile

billing_router = Router(tags=["shop"], auth=django_auth)   # mounted at /api/me/billing

BILLING_FIELDS = {   # API name -> model field
    "type": "type", "firstName": "first_name", "lastName": "last_name", "identityNumber": "identity_number",
    "companyName": "company_name", "taxOffice": "tax_office", "taxNumber": "tax_number", "email": "email",
    "address": "address", "district": "district", "city": "city", "postalCode": "postal_code", "country": "country",
}


def billing_json(profile) -> dict:
    if profile is None:
        return {"billing": None, "isComplete": False}
    return {"billing": {api: getattr(profile, field) or "" for api, field in BILLING_FIELDS.items()},
            "isComplete": profile.is_complete}


def _profile(user):
    return BillingProfile.objects.filter(user=user).first()


@billing_router.get("", response={200: S.BillingResponse, **errors(401)})
def get_billing(request):
    """The account's billing profile (who pays), or null before the first save."""
    return respond(request, "billing_retrieved", billing_json(_profile(request.user)))


class BillingIn(CamelSchema):
    type: Literal["individual", "company"] = "individual"
    first_name: str = Field("", max_length=80)
    last_name: str = Field("", max_length=80)
    identity_number: str = Field("", max_length=11)
    company_name: str = Field("", max_length=200)
    tax_office: str = Field("", max_length=80)
    tax_number: str = Field("", max_length=10)
    email: str = Field("", max_length=254)
    address: str = Field("", max_length=300)
    district: str = Field("", max_length=80)
    city: str = Field("", max_length=80)
    postal_code: str = Field("", max_length=10)
    country: str = Field("Türkiye", max_length=60)


@billing_router.put("", response={200: S.BillingSavedResponse, **errors(401, 422)})
def put_billing(request, data: BillingIn):
    """Save the whole billing profile. Individuals: name, TC kimlik no, email, address, district, city.
    Companies: name of the contact person, company name (unvan), tax office, VKN, email, address,
    district, city (TC kimlik no optional). Checked with the official TC kimlik / VKN checksums.

    Errors: 422 validation_error with `errors` per field (e.g. {"identityNumber": ["invalid TC kimlik no"]})."""
    values = {field: getattr(data, field).strip() for field in BILLING_FIELDS.values()}
    profile = _profile(request.user) or BillingProfile(user=request.user)
    for field, value in values.items():
        setattr(profile, field, value)
    problems = billing_errors(profile)
    if problems:
        api_names = {field: api for api, field in BILLING_FIELDS.items()}
        raise ApiError(422, "validation_error", "Fatura bilgileri eksik veya hatalı.",
                       errors={api_names[f]: [m] for f, m in problems.items()})
    profile.save()
    return respond(request, "billing_saved", billing_json(profile))
