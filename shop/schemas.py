"""Response shapes of the shop API (/api/shop/…), for the docs and the contract test.
The JSON is built in shop/api.py; docs/api.md §3.7 explains the fields."""
from typing import Literal

from pydantic import Field

from common.api import Out, envelope


class TextOut(Out):
    """A shown text in both languages; "" when not filled in."""
    tr: str = Field(examples=["Mini paket"])
    en: str = Field(examples=["Small pack"])


class ProductOut(Out):
    id: str = Field(examples=["2"])
    name: TextOut = Field(examples=[{"tr": "2000cr", "en": "2000cr"}])
    title: TextOut = Field(examples=[{"tr": "Mini paket", "en": "Small pack"}])
    description: TextOut = Field(description="Subtitle.", examples=[{"tr": "Küçük düğünler için", "en": "For small weddings"}])
    creditAmount: int = Field(description="Credits the product adds to the account.", examples=[2000])
    price: float = Field(examples=[3600.0])
    currency: str = Field(examples=["TRY"])
    unitPrice: float = Field(description="Price per credit.", examples=[1.8])
    discountPercent: int = Field(description="Against the product with the highest unit price.", examples=[10])
    featured: bool


class CatalogOut(Out):
    creditBalance: int | None = Field(description="Signed in: the balance (may be negative). Signed out: null.",
                                      examples=[1840])
    pricingVersion: str = Field(description="Send back with a purchase; changes with any product or price change.",
                                examples=["3f9a1c0b2d4e"])
    products: list[ProductOut] = Field(description="On sale, in display order.")
    documentsVersion: str = Field(description="Version of the ÖBF + MSS texts (GET /api/shop/obf, /mss); send back "
                                              "with a purchase.", examples=["001"])


class PurchaseOut(Out):
    id: str = Field(examples=["7"])
    productId: str = Field(examples=["2"])
    productName: TextOut = Field(description="As it was at purchase time.", examples=[{"tr": "2000cr", "en": "2000cr"}])
    creditAmount: int = Field(examples=[2000])
    amount: float = Field(description="Charged.", examples=[3600.0])
    currency: str = Field(examples=["TRY"])
    status: Literal["pending", "paid", "failed"]
    purchasedAt: str = Field(description="When the purchase was started.", examples=["2026-10-19T08:30:00+00:00"])
    paidAt: str | None = Field(examples=["2026-10-19T08:30:05+00:00"])
    documentsUrl: str | None = Field(description="The ÖBF + MSS the buyer accepted (HTML page); null on purchases "
                                                 "made before the documents existed.",
                                     examples=["/api/shop/purchases/7/documents"])


class PurchaseListOut(Out):
    purchases: list[PurchaseOut] = Field(description="Newest first, max 100.")
    total: int


class PurchaseStartedOut(Out):
    purchase: PurchaseOut
    paymentPageUrl: str | None = Field(description="iyzico's payment page: send the browser here. null when the "
                                                   "purchase is already paid (simulated payments).",
                                       examples=["https://sandbox-cpp.iyzipay.com?token=…&lang=tr"])
    creditBalance: int = Field(description="Now; grows when the payment succeeds.", examples=[1840])


class PurchaseDetailOut(Out):
    purchase: PurchaseOut
    creditBalance: int = Field(examples=[5840])


CatalogResponse = envelope("ShopCatalogResponse", CatalogOut, "products_retrieved")
PurchaseListResponse = envelope("PurchaseListResponse", PurchaseListOut, "purchases_retrieved")
PurchaseResponse = envelope("PurchaseResponse", PurchaseStartedOut, "purchase_started")
PurchaseDetailResponse = envelope("PurchaseDetailResponse", PurchaseDetailOut, "purchase_retrieved")


class BillingOut(Out):
    type: Literal["individual", "company"]
    firstName: str = Field(description="The buyer; for a company, the contact person.", examples=["Ece"])
    lastName: str = Field(examples=["Yılmaz"])
    identityNumber: str = Field(description="TC kimlik no; required for individuals.", examples=["10000000146"])
    companyName: str = Field(description="Unvan; companies only.", examples=[""])
    taxOffice: str = Field(description="Vergi dairesi; companies only.", examples=[""])
    taxNumber: str = Field(description="VKN, 10 digits; companies only.", examples=[""])
    email: str = Field(examples=["ece@example.com"])
    address: str = Field(examples=["Atatürk Cad. No: 1 D: 2"])
    district: str = Field(description="İlçe.", examples=["Çankaya"])
    city: str = Field(description="İl.", examples=["Ankara"])
    postalCode: str = Field(examples=["06680"])
    country: str = Field(examples=["Türkiye"])


class BillingDataOut(Out):
    billing: BillingOut | None = Field(description="null until saved once.")
    isComplete: bool = Field(description="Purchases need a complete billing profile.")


BillingResponse = envelope("BillingResponse", BillingDataOut, "billing_retrieved")
BillingSavedResponse = envelope("BillingSavedResponse", BillingDataOut, "billing_saved")
