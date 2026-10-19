"""Response shapes of the shop API (/api/shop/…), for the docs and the contract test.
The JSON is built in shop/api.py; docs/api.md §3.7 explains the fields."""
from typing import Literal

from pydantic import Field

from common.api import Out, envelope


class ProductOut(Out):
    id: str = Field(examples=["2"])
    name: str = Field(examples=["2000cr"])
    title: str | None = Field(examples=["Mini paket"])
    description: str | None = Field(description="Subtitle.", examples=["Küçük düğünler için"])
    creditAmount: int = Field(description="Credits the product adds to the account.", examples=[2000])
    price: float = Field(examples=[3600.0])
    currency: str = Field(examples=["TRY"])
    unitPrice: float = Field(description="Price per credit.", examples=[1.8])
    discountPercent: int = Field(description="Against the product with the highest unit price.", examples=[10])
    featured: bool


class CatalogOut(Out):
    creditBalance: int = Field(description="May be negative (overdraft).", examples=[1840])
    pricingVersion: str = Field(description="Send back with a purchase; changes with any product or price change.",
                                examples=["3f9a1c0b2d4e"])
    products: list[ProductOut] = Field(description="On sale, in display order.")


class PurchaseOut(Out):
    id: str = Field(examples=["7"])
    productId: str = Field(examples=["2"])
    productName: str = Field(description="As it was at purchase time.", examples=["2000cr"])
    creditAmount: int = Field(examples=[2000])
    amount: float = Field(description="Charged.", examples=[3600.0])
    currency: str = Field(examples=["TRY"])
    status: Literal["pending", "paid", "failed"]
    purchasedAt: str = Field(description="When the purchase was started.", examples=["2026-10-19T08:30:00+00:00"])
    paidAt: str | None = Field(examples=["2026-10-19T08:30:05+00:00"])


class PurchaseListOut(Out):
    purchases: list[PurchaseOut] = Field(description="Newest first, max 100.")
    total: int


class PurchaseResultOut(Out):
    purchase: PurchaseOut
    creditBalance: int = Field(description="After the purchase.", examples=[5840])


CatalogResponse = envelope("ShopCatalogResponse", CatalogOut, "products_retrieved")
PurchaseListResponse = envelope("PurchaseListResponse", PurchaseListOut, "purchases_retrieved")
PurchaseResponse = envelope("PurchaseResponse", PurchaseResultOut, "purchase_completed")
