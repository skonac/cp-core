"""Sales documents: Ön Bilgilendirme Formu (ÖBF) and Mesafeli Satış Sözleşmesi (MSS), TR and EN
(design/core.md, "Checkout documents").

    preview(kind, locale, user, billing, product) -> HTML    one text, for reading before buying
    attach(purchase, user_agent)                            store the accepted texts on the purchase

Templates in legal/ (flat, next to the KVKK texts): {obf,mss}_{tr,en}_<version>.html are the bodies,
sales_page_<version>.html is the page around them.
settings.SALES_DOCS_VERSION names the current version; a published version is never edited (a change
is a new version). The stored copy is the four texts in one page, in the order ÖBF-TR, MSS-TR,
ÖBF-EN, MSS-EN, written once in the purchase's own transaction and never regenerated.
"""
import hashlib
from decimal import Decimal
from functools import lru_cache

from django.conf import settings
from django.template import Context, Engine
from django.utils import timezone
from django.utils.safestring import mark_safe

KINDS = ("obf", "mss")
LOCALES = ("tr", "en")
ORDER = (("obf", "tr"), ("mss", "tr"), ("obf", "en"), ("mss", "en"))   # the stored page
TITLES = {("obf", "tr"): "Ön Bilgilendirme Formu", ("mss", "tr"): "Mesafeli Satış Sözleşmesi",
          ("obf", "en"): "Preliminary Information Form", ("mss", "en"): "Distance Sales Agreement"}
EMPTY = "–"


def templates(version: str) -> list[str]:
    return [f"{kind}_{loc}_{version}.html" for kind, loc in ORDER] + [f"sales_page_{version}.html"]


@lru_cache(maxsize=1)
def _engine() -> Engine:
    return Engine(dirs=[str(settings.LEGAL_DIR)], autoescape=True)


def _render(version: str, name: str, ctx: dict) -> str:
    """name without the version: "mss_tr" -> legal/mss_tr_<version>.html."""
    return _engine().get_template(f"{name}_{version}.html").render(Context(ctx))


def _money(amount: Decimal, currency: str, locale: str) -> str:
    text = f"{Decimal(amount):,.2f}"                                   # 4,000.00
    if locale == "tr":
        text = text.replace(",", " ").replace(".", ",").replace(" ", ".")   # 4.000,00
        return f"{text} TL" if currency == "TRY" else f"{text} {currency}"
    return f"{currency} {text}"


def _count(n: int, locale: str) -> str:
    text = f"{n:,}"
    return text.replace(",", ".") if locale == "tr" else text


def _buyer(billing: dict, phone: str) -> dict:
    """From the billing profile in its API shape (shop.api.billing_json, also Purchase.billing)."""
    b = {k: (v or "").strip() for k, v in (billing or {}).items() if isinstance(v, str)}
    company = b.get("type") == "company"
    person = f"{b.get('firstName', '')} {b.get('lastName', '')}".strip()
    place = " ".join(x for x in (b.get("postalCode", ""), b.get("district", "")) if x)
    address = ", ".join(x for x in (b.get("address", ""), " / ".join(x for x in (place, b.get("city", "")) if x),
                                    b.get("country", "")) if x)
    return {"name": (b.get("companyName") if company else person) or person or EMPTY,
            "tax_id": (b.get("taxNumber") if company else b.get("identityNumber")) or EMPTY,
            "tax_office": (b.get("taxOffice") if company else "") or EMPTY,
            "address": address or EMPTY, "email": b.get("email") or EMPTY, "phone": phone or EMPTY}


def _product_name(name: str, title: str) -> str:
    return f"{name} – {title}" if title else name


def _order(locale: str, number: str, when, name: str, title: str, credits: int, amount, currency: str) -> dict:
    return {"number": number, "date": timezone.localtime(when).strftime("%d.%m.%Y %H:%M"),
            "product": _product_name(name, title), "credits": _count(credits, locale),
            "amount": _money(amount, currency, locale)}


def _page(version: str, lang: str, title: str, bodies: list[str]) -> str:
    return _render(version, "sales_page", {"lang": lang, "title": f"Clever Photos — {title}",
                                          "bodies": [mark_safe(b) for b in bodies]})


def preview(kind: str, locale: str, user, billing: dict, product) -> str:
    """One text filled for this user and product, before a purchase exists: order number "–", date now."""
    version = settings.SALES_DOCS_VERSION
    title = product.title_tr if locale == "tr" else (product.title_en or product.title_tr)
    name = product.name_tr if locale == "tr" else (product.name_en or product.name_tr)
    ctx = {"buyer": _buyer(billing, user.phone),
           "order": _order(locale, EMPTY, timezone.now(), name, title, product.credits, product.price, product.currency)}
    return _page(version, locale, TITLES[(kind, locale)], [_render(version, f"{kind}_{locale}", ctx)])


def attach(purchase, user_agent: str = "") -> None:
    """Render the four texts for this purchase (its copies of the product and billing data) and store
    them with the acceptance time and the browser. Call inside the transaction that created it."""
    version, now = settings.SALES_DOCS_VERSION, timezone.now()
    product = purchase.product
    buyer = _buyer(purchase.billing, purchase.user.phone)
    bodies = []
    for kind, loc in ORDER:
        if loc == "tr":
            name, title = purchase.product_name_tr, product.title_tr
        else:
            name, title = purchase.product_name_en or purchase.product_name_tr, product.title_en or product.title_tr
        order = _order(loc, purchase.number, now, name, title, purchase.credits, purchase.amount, purchase.currency)
        bodies.append(_render(version, f"{kind}_{loc}", {"buyer": buyer, "order": order}))
    html = _page(version, "tr", f"{TITLES[('mss', 'tr')]} {purchase.number}", bodies)
    purchase.docs_version, purchase.docs_html, purchase.docs_accepted_at = version, html, now
    purchase.docs_sha256 = hashlib.sha256(html.encode()).hexdigest()
    purchase.user_agent = (user_agent or "")[:500]
    purchase.save(update_fields=["docs_version", "docs_html", "docs_sha256", "docs_accepted_at", "user_agent"])


def missing_templates(version: str) -> list:
    return [settings.LEGAL_DIR / name for name in templates(version) if not (settings.LEGAL_DIR / name).is_file()]
