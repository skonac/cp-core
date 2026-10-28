"""What a product costs this caller (design/core.md, "Next step: discounts and promo codes").

One discount per product, never stacked: the biggest of the ones that apply wins (lowest final price;
on a tie promo, then user, then global). Candidates: the global discount (CP_DISCOUNT_VALUE, everyone,
signed out too), the user's own discount_percent (signed in), the session's promo code when it is valid
now and covers the product (signed-in organizers and staff only). The final price is rounded down to a whole
TL and never goes below 0. Used by GET /api/shop/products, the ÖBF/MSS preview and purchase creation,
so what is shown, accepted and charged is the same number; pricing_version() over these prices makes
a purchase started before any change fail with 409 pricing_changed.
"""
import hashlib
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from django.conf import settings
from django.utils import timezone

from .models import Product, PromoCode

SESSION_KEY = "shop_promo"     # the applied code (lowercase) in the Django session


@dataclass(frozen=True)
class Price:
    list_price: Decimal
    final: Decimal
    source: str                 # "none" | "user" | "promo" | "global"
    percent: int | None         # the discount's percent; None for none and for a fixed-amount promo
    promo_code: str = ""        # lowercase, "" unless source == "promo"

    @property
    def shown_percent(self) -> int:
        """For display: the percent, or a fixed promo's rounded equivalent."""
        if self.percent is not None:
            return self.percent
        if self.source == "none" or not self.list_price:
            return 0
        return round((self.list_price - self.final) / self.list_price * 100)


def global_percent(strict: bool = False) -> int:
    """CP_DISCOUNT_VALUE as a whole percent; 0 when off. strict: raise ValueError on a bad value
    (manage.py check), otherwise a bad value counts as off."""
    raw = str(settings.GLOBAL_DISCOUNT_VALUE).strip() or "0"
    if raw.isdigit() and int(raw) <= 100:
        return int(raw)
    if strict:
        raise ValueError(f"CP_DISCOUNT_VALUE={raw!r}: a whole percent from 0 to 100 (0 = off)")
    return 0


def global_discount() -> dict | None:
    """For the API: the global discount, or None when off."""
    pct = global_percent()
    if not pct:
        return None
    return {"percent": pct, "title": dict(settings.GLOBAL_DISCOUNT_TITLE), "banner": dict(settings.GLOBAL_DISCOUNT_BANNER)}


def can_use_promo(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_organizer or user.is_staff))


def session_promo(request, now=None) -> PromoCode | None:
    """The session's promo code if this user may use one and it is valid now; otherwise None (an
    expired code stays in the session and simply stops counting)."""
    code = request.session.get(SESSION_KEY)
    if not code or not can_use_promo(request.user):
        return None
    promo = PromoCode.objects.filter(pk=code).first()
    return promo if promo and promo.is_valid_at(now or timezone.now()) else None


def _whole(amount: Decimal) -> Decimal:
    return max(Decimal(0), amount.quantize(Decimal(1), rounding=ROUND_FLOOR)).quantize(Decimal("0.01"))


def _percent_off(base: Decimal, pct: int) -> Decimal:
    return _whole(base * (100 - pct) / 100)


def price_for(product: Product, user, promo: PromoCode | None) -> Price:
    base = Decimal(product.price)
    candidates = []                     # in tie order: promo, user, global
    if promo is not None and promo.covers(product.pk):
        if promo.type == PromoCode.Type.PERCENT:
            pct = int(promo.value)
            candidates.append(Price(base, _percent_off(base, pct), "promo", pct, promo.code))
        else:
            candidates.append(Price(base, _whole(base - promo.value), "promo", None, promo.code))
    own = user.discount_percent if user is not None and user.is_authenticated else 0
    if own:
        candidates.append(Price(base, _percent_off(base, own), "user", own))
    pct = global_percent()
    if pct:
        candidates.append(Price(base, _percent_off(base, pct), "global", pct))
    best = min(candidates, key=lambda c: c.final, default=None)     # min() keeps the first of equals
    return best if best is not None and best.final < base else Price(base, base, "none", None)


def prices(request, products) -> tuple[dict[int, Price], PromoCode | None]:
    """{product id: Price} for this request's user and session, and the promo in effect (or None)."""
    promo = session_promo(request)
    return {p.pk: price_for(p, request.user, promo) for p in products}, promo


def pricing_version(products, priced: dict[int, Price]) -> str:
    text = ";".join(f"{p.pk}:{p.credits}:{p.price}:{priced[p.pk].final}:{p.currency}" for p in products)
    return hashlib.sha256(text.encode()).hexdigest()[:12]
