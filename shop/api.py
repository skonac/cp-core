"""Shop API (/api/shop/…): the products on sale (public), buying one and the purchase history
(organizer accounts: credits are spent on photo uploads). Shapes and codes: docs/api.md §3.7.
One product per purchase, no cart. Payment: shop/payments.py (simulated until iyzico is connected)."""
import logging
from typing import Literal

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from ninja import Router
from ninja.security import django_auth
from pydantic import Field

from accounts.models import User
from common.api import ApiError, CamelSchema, errors, file_doc, respond
from common.ratelimit import allow, hits

from . import documents, pricing
from . import schemas as S
from .billing import billing_errors
from .models import BillingProfile, Product, PromoCode, Purchase
from .payments import PaymentError, start

log = logging.getLogger("cp.shop")
shop_router = Router(tags=["shop"], auth=django_auth)
CALLBACK_PATH = "/api/shop/iyzico/callback"   # shop/views.py; iyzico sends the browser here after paying


def _organizer(request) -> User:
    if not (request.user.is_organizer or request.user.is_staff):
        raise ApiError(403, "organizer_access_required", "Bu işlem için organizatör hesabı gerekli.")
    return request.user


def _balance(user) -> int:
    return User.objects.values_list("credit_balance", flat=True).get(pk=user.pk)


def _on_sale() -> list[Product]:
    return list(Product.objects.filter(is_active=True))


def purchase_json(p: Purchase) -> dict:
    return {"id": str(p.pk), "productId": str(p.product_id),
            "productName": {"tr": p.product_name_tr, "en": p.product_name_en},
            "creditAmount": p.credits, "amount": float(p.amount), "currency": p.currency, "status": p.status,
            "purchasedAt": p.created_at.isoformat(), "paidAt": p.paid_at.isoformat() if p.paid_at else None,
            "documentsUrl": f"/api/shop/purchases/{p.pk}/documents" if p.docs_html else None}


@shop_router.get("/products", auth=None, response={200: S.CatalogResponse})
def products(request):
    """The products on sale (managed in the admin) and the price version to buy with. Public: also for
    the landing/pricing pages. `creditBalance` only with a session (null otherwise).
    Prices are this caller's (shop/pricing.py): `price` the list price, `finalPrice` after the one
    discount that applies (the session's promo code where it covers the product, else the account's
    own discount, or the global one: the biggest wins), `discountPercent` / `discountSource` /
    `discountLabel` that discount. Signed out: only the global discount (`globalDiscount`)."""
    return respond(request, "products_retrieved", catalog_json(request))


def _label(price) -> dict | None:
    """What to show next to a discounted price: the global discount's title, or the promo code."""
    if price.source == "global":
        return dict(settings.GLOBAL_DISCOUNT_TITLE)
    if price.source == "promo":
        return {"tr": price.promo_code.upper(), "en": price.promo_code.upper()}
    return None


def catalog_json(request) -> dict:
    on_sale = _on_sale()
    priced, promo = pricing.prices(request, on_sale)
    out = []
    for p in on_sale:
        price = priced[p.pk]
        out.append({"id": str(p.pk), "name": {"tr": p.name_tr, "en": p.name_en},
                    "title": {"tr": p.title_tr, "en": p.title_en},
                    "description": {"tr": p.description_tr, "en": p.description_en},
                    "creditAmount": p.credits, "price": float(p.price), "finalPrice": float(price.final),
                    "currency": p.currency,
                    "unitPrice": round(float(price.final) / p.credits, 4) if p.credits else 0,
                    "discountPercent": price.shown_percent,
                    "discountSource": None if price.source == "none" else price.source,
                    "discountLabel": _label(price), "featured": p.is_featured})
    balance = _balance(request.user) if request.user.is_authenticated else None
    return {"creditBalance": balance, "pricingVersion": pricing.pricing_version(on_sale, priced), "products": out,
            "appliedPromo": promo.code.upper() if promo else None,
            "globalDiscount": pricing.global_discount(),
            "documentsVersion": settings.SALES_DOCS_VERSION}


class PromoIn(CamelSchema):
    code: str


def _promo_problem(code: str, now) -> str:
    """Why a code can't be applied ("" = it can). Only logged, never told to the caller."""
    if not code or len(code) > 16:
        return "malformed"
    promo = PromoCode.objects.filter(pk=code).first()
    if promo is None:
        return "unknown"
    if now < promo.valid_from:
        return "not_started"
    if now >= promo.valid_until:
        return "expired"
    return ""


@shop_router.post("/promo", response={200: S.PromoAppliedResponse, **errors(401, 403, 422, 429)})
def apply_promo(request, data: PromoIn):
    """Apply a promo code to this session (organizers). Matched case-insensitively. A valid code (it
    exists and now is between its dates) replaces any earlier one and stays in the session until it
    is replaced or the session ends; it counts on the products it covers instead of the account's own
    discount, and stops counting by itself once it expires. Answers with the catalog as in GET
    /api/shop/products (`appliedPromo` set).

    Errors: 403 organizer_access_required · 422 invalid_promo_code (any reason; the session's code is
    cleared) · 429 rate_limit_exceeded (4 wrong codes per 4 hours)."""
    user = _organizer(request)
    limit, window = settings.PROMO_FAILS
    key = f"promo:{user.pk}"
    if hits(key, window) >= limit:
        log.info("promo code from user %s refused: rate limit", user.pk)
        raise ApiError(429, "rate_limit_exceeded", "Çok fazla deneme. Biraz sonra tekrar dene.")
    code = data.code.strip().lower()
    problem = _promo_problem(code, timezone.now())
    if problem:
        allow(key, limit, window)
        request.session.pop(pricing.SESSION_KEY, None)
        log.info("promo code %r from user %s invalid: %s", code[:40], user.pk, problem)
        raise ApiError(422, "invalid_promo_code", "Geçersiz kod.")
    request.session[pricing.SESSION_KEY] = code
    log.info("promo code %r applied for user %s", code, user.pk)
    return respond(request, "promo_applied", catalog_json(request))


HTML = "text/html; charset=utf-8"


def _html(text: str) -> HttpResponse:
    response = HttpResponse(text, content_type=HTML)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "SAMEORIGIN"     # cp-web (same domain) shows the texts in an iframe
    return response


def _document(request, kind: str, product_id: str, locale: str) -> HttpResponse:
    user = _organizer(request)
    product = next((p for p in _on_sale() if str(p.pk) == product_id), None)
    if product is None:
        raise ApiError(404, "not_found", "Ürün bulunamadı.")
    profile = _profile(user)
    if profile is None or not profile.is_complete:
        raise ApiError(422, "billing_profile_required", "Önce fatura bilgilerini doldur.")
    price = pricing.price_for(product, user, pricing.session_promo(request))
    return _html(documents.preview(kind, locale, user, billing_json(profile)["billing"], product, price.final))


@shop_router.get("/obf", response=errors(401, 403, 404, 422), openapi_extra=file_doc(HTML, "The filled form"))
def obf(request, product: str, locale: Literal["tr", "en"] = "tr"):
    """Ön Bilgilendirme Formu (preliminary information form), filled for the signed-in user's billing
    profile and this product, as an HTML page: show it before the purchase, next to its checkbox.
    Order number "–", date now. The text version: `documentsVersion` in GET /api/shop/products.

    Errors: 403 organizer_access_required · 404 not_found (product not on sale) · 422 billing_profile_required."""
    return _document(request, "obf", product, locale)


@shop_router.get("/mss", response=errors(401, 403, 404, 422), openapi_extra=file_doc(HTML, "The filled agreement"))
def mss(request, product: str, locale: Literal["tr", "en"] = "tr"):
    """Mesafeli Satış Sözleşmesi (distance sales agreement), filled like GET /api/shop/obf.

    Errors: 403 organizer_access_required · 404 not_found (product not on sale) · 422 billing_profile_required."""
    return _document(request, "mss", product, locale)


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
    documents_version: str = ""     # GET /api/shop/products documentsVersion
    accepted_obf: bool = False      # the two checkboxes
    accepted_mss: bool = False


@shop_router.post("/purchases", response={201: S.PurchaseResponse, **errors(401, 403, 404, 409, 422, 502)})
def create_purchase(request, data: PurchaseIn):
    """Buy one product: checks it is on sale and the price version, then starts the payment.
    With iyzico: the purchase is `pending` and `paymentPageUrl` is iyzico's payment page; send the
    browser there. After paying, iyzico brings the browser back to us, we settle the purchase and
    redirect to the shop page (`/organizer/credits?purchase=<id>&status=paid|failed|pending`); read
    the result with GET /api/shop/purchases/{id}. Simulated (dev servers): `paid` at once,
    `paymentPageUrl` null.

    Needs a complete billing profile (`PUT /api/me/billing`); it is copied into the purchase. The buyer
    must have ticked both checkboxes (ÖBF and MSS, GET /api/shop/obf and /mss): `acceptedObf` and
    `acceptedMss` true, `documentsVersion` as in GET /api/shop/products. The four filled texts are
    stored with the purchase (`documentsUrl`).

    Errors: 403 organizer_access_required · 404 not_found (product not on sale) ·
    409 pricing_changed (reload the products) · 422 billing_profile_required ·
    409 documents_changed (the texts changed: reload the products and show them again) ·
    422 documents_not_accepted ·
    502 payment_provider_error, payment_provider_unreachable (iyzico refused or can't be reached)."""
    user = _organizer(request)
    on_sale = _on_sale()
    product = next((p for p in on_sale if str(p.pk) == data.product_id), None)
    if product is None:
        raise ApiError(404, "not_found", "Ürün bulunamadı.")
    priced, _ = pricing.prices(request, on_sale)
    if data.pricing_version != pricing.pricing_version(on_sale, priced):
        raise ApiError(409, "pricing_changed", "Fiyatlar güncellendi; ürünleri yeniden yükle.")
    profile = _profile(user)
    if profile is None or not profile.is_complete:
        raise ApiError(422, "billing_profile_required", "Önce fatura bilgilerini doldur.")
    if data.documents_version != settings.SALES_DOCS_VERSION:
        raise ApiError(409, "documents_changed", "Sözleşme metinleri güncellendi; yeniden okuyup onayla.")
    if not (data.accepted_obf and data.accepted_mss):
        raise ApiError(422, "documents_not_accepted",
                       "Ön Bilgilendirme Formu ve Mesafeli Satış Sözleşmesi onaylanmalı.")
    try:
        purchase, page_url = start(user, product, request.META.get("REMOTE_ADDR"), billing_json(profile)["billing"],
                                   profile, request.build_absolute_uri(CALLBACK_PATH),
                                   request.META.get("HTTP_USER_AGENT", ""), priced[product.pk])
    except PaymentError as e:
        raise ApiError(502, e.code, e.message) from None
    return respond(request, "purchase_started", {"purchase": purchase_json(purchase), "paymentPageUrl": page_url,
                                                 "creditBalance": _balance(user)}, status=201)


@shop_router.get("/purchases/{purchase_id}", response={200: S.PurchaseDetailResponse, **errors(401, 403, 404)})
def get_purchase(request, purchase_id: int):
    """One of this account's purchases: the result page after iyzico's return reads `status` here
    (`pending` can still turn into `paid` or `failed`; ask again in a few seconds).

    Errors: 403 organizer_access_required · 404 not_found."""
    user = _organizer(request)
    purchase = Purchase.objects.filter(pk=purchase_id, user=user).first()
    if purchase is None:
        raise ApiError(404, "not_found", "Satın alma bulunamadı.")
    return respond(request, "purchase_retrieved", {"purchase": purchase_json(purchase), "creditBalance": _balance(user)})


@shop_router.get("/purchases/{purchase_id}/documents", response=errors(401, 403, 404),
                 openapi_extra=file_doc(HTML, "The stored page"))
def purchase_documents(request, purchase_id: int):
    """The ÖBF + MSS (TR, then EN) as the buyer accepted them for this purchase: the stored HTML page,
    never regenerated. Printable; the browser can save it as PDF.

    Errors: 403 organizer_access_required · 404 not_found (not this account's, or a purchase from
    before the documents existed)."""
    user = _organizer(request)
    purchase = Purchase.objects.filter(pk=purchase_id, user=user).exclude(docs_html="").first()
    if purchase is None:
        raise ApiError(404, "not_found", "Belge bulunamadı.")
    return _html(purchase.docs_html)


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
