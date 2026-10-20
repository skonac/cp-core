"""Shop: the API contract (every /api/shop/ endpoint, responses checked against their schemas),
credits added once, product data copied at purchase time, production guard."""
import re

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import User
from common.testing import ApiClient
from config.api import api
from credits.models import CreditTransaction

from .apps import check_payment_provider
from .billing import tckn_valid, vkn_valid
from .models import BillingProfile, Product, Purchase
from .payments import buy, complete

PERSON = {"type": "individual", "firstName": "Ece", "lastName": "Yılmaz", "identityNumber": "10000000146",
          "email": "ece@example.com", "address": "Atatürk Cad. 1", "district": "Çankaya", "city": "Ankara"}


def _norm(route: str) -> str:
    return re.sub(r"<(?:\w+:)?\w+>|\{\w+\}", "{}", route.strip("/"))


class ShopApiTests(TestCase):
    def assertCode(self, r, status, code):
        self.assertEqual((r.status_code, r.json().get("code")), (status, code), r.content[:400])

    def test_shop_flow_and_contract(self):
        cache.clear()
        c = ApiClient()
        r = c.get("/api/shop/products")                                   # public, no session
        self.assertCode(r, 200, "products_retrieved")
        self.assertIsNone(r.json()["data"]["creditBalance"])
        self.assertCode(c.get("/api/shop/purchases"), 401, "authentication_required")
        c.sign_in("+905550000301")                                        # a participant
        self.assertCode(c.get("/api/shop/purchases"), 403, "organizer_access_required")
        org = User.objects.create_user("+905550000302", is_organizer=True, credit_balance=-12)
        c.force_login(org)
        c.get("/api/auth/csrf")

        Product.objects.create(name_tr="2000cr", name_en="2000cr", title_tr="Mini paket", title_en="Small pack",
                               description_tr="Küçük etkinlikler", credits=2000, price=4000, sort_order=1)
        Product.objects.create(name_tr="5000cr", name_en="5000cr", credits=5000, price=7500, sort_order=2,
                               is_featured=True)
        old = Product.objects.create(name_tr="old", name_en="old", credits=10, price=1, is_active=False)
        r = c.get("/api/shop/products")
        self.assertCode(r, 200, "products_retrieved")
        cat = r.json()["data"]
        self.assertEqual(cat["creditBalance"], -12)
        self.assertEqual([(p["name"]["tr"], p["title"], p["unitPrice"], p["discountPercent"]) for p in cat["products"]],
                         [("2000cr", {"tr": "Mini paket", "en": "Small pack"}, 2.0, 0),
                          ("5000cr", {"tr": "", "en": ""}, 1.5, 25)])
        self.assertEqual(cat["products"][0]["description"], {"tr": "Küçük etkinlikler", "en": ""})
        self.assertCode(c.get("/api/shop/purchases"), 200, "purchases_retrieved")
        pid, version = cat["products"][0]["id"], cat["pricingVersion"]

        self.assertCode(c.post_json("/api/shop/purchases", {"productId": pid, "pricingVersion": "old"}),
                        409, "pricing_changed")
        self.assertCode(c.post_json("/api/shop/purchases", {"productId": str(old.pk), "pricingVersion": version}),
                        404, "not_found")
        self.assertCode(c.post_json("/api/shop/purchases", {"productId": pid}), 422, "validation_error")
        self.assertCode(c.post_json("/api/shop/purchases", {"productId": pid, "pricingVersion": version}),
                        422, "billing_profile_required")

        # --- billing profile: individual, then company
        r = c.get("/api/me/billing")
        self.assertCode(r, 200, "billing_retrieved")
        self.assertEqual(r.json()["data"], {"billing": None, "isComplete": False})
        r = c.put("/api/me/billing", {**PERSON, "identityNumber": "10000000147", "city": ""},
                  content_type="application/json", **c.csrf_headers())
        self.assertCode(r, 422, "validation_error")
        self.assertEqual(set(r.json()["errors"]), {"identityNumber", "city"})
        r = c.put("/api/me/billing", PERSON, content_type="application/json", **c.csrf_headers())
        self.assertCode(r, 200, "billing_saved")
        self.assertEqual((r.json()["data"]["isComplete"], r.json()["data"]["billing"]["identityNumber"]),
                         (True, "10000000146"))
        r = c.put("/api/me/billing", {**PERSON, "type": "company", "identityNumber": "", "companyName": "Foto Ltd.",
                                      "taxOffice": "Çankaya", "taxNumber": "1234567891"},
                  content_type="application/json", **c.csrf_headers())
        self.assertEqual(r.json()["errors"], {"taxNumber": ["invalid vergi kimlik no"]})
        r = c.put("/api/me/billing", {**PERSON, "type": "company", "identityNumber": "", "companyName": "Foto Ltd.",
                                      "taxOffice": "Çankaya", "taxNumber": "1234567890"},
                  content_type="application/json", **c.csrf_headers())
        self.assertCode(r, 200, "billing_saved")

        r = c.post_json("/api/shop/purchases", {"productId": pid, "pricingVersion": version})
        self.assertCode(r, 201, "purchase_completed")
        data = r.json()["data"]
        self.assertEqual((data["purchase"]["status"], data["purchase"]["productName"]["tr"], data["purchase"]["creditAmount"],
                          data["purchase"]["amount"], data["creditBalance"]), ("paid", "2000cr", 2000, 4000.0, 1988))
        self.assertEqual(Purchase.objects.get().billing["companyName"], "Foto Ltd.")   # copied into the purchase
        history = c.get("/api/shop/purchases").json()["data"]
        self.assertEqual((history["total"], history["purchases"][0]["id"]), (1, data["purchase"]["id"]))
        with self.settings(PAYMENT_PROVIDER="iyzico"):
            self.assertCode(c.post_json("/api/shop/purchases", {"productId": pid, "pricingVersion": version}),
                            501, "payment_not_available")

        called = {(m, _norm(route)) for m, route, _ in c.checked}
        documented = {(m.upper(), _norm(path)) for path, ops in api.get_openapi_schema()["paths"].items() for m in ops
                      if path.startswith(("/api/shop/", "/api/me/billing"))}
        self.assertEqual(documented - called, set(), "shop endpoints never called by this test")


class PurchaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("+905550000201", is_organizer=True, credit_balance=-12)
        self.product = Product.objects.create(name_tr="2000cr", name_en="2000cr", title_tr="Mini paket",
                                              credits=2000, price=4000)

    def test_simulated_buy_adds_credits_once(self):
        purchase = buy(self.user, self.product, "127.0.0.1")
        self.assertEqual((purchase.status, purchase.provider, purchase.provider_payment_id),
                         ("paid", "simulated", f"SIM-{purchase.pk}"))
        self.assertEqual(User.objects.get(pk=self.user.pk).credit_balance, 1988)
        complete(purchase, "again")                              # a repeated callback changes nothing
        self.assertEqual(User.objects.get(pk=self.user.pk).credit_balance, 1988)
        self.assertEqual(CreditTransaction.objects.get().ref, f"purchase:{purchase.pk}")

    def test_product_copied_at_purchase_time(self):
        purchase = buy(self.user, self.product)
        self.product.price, self.product.credits = 9999, 1
        self.product.save()
        purchase = Purchase.objects.get(pk=purchase.pk)
        self.assertEqual((purchase.product_name_tr, purchase.credits, purchase.list_price, purchase.amount),
                         ("2000cr", 2000, 4000, 4000))

    @override_settings(PAYMENT_PROVIDER="simulated", IS_DEV=False)
    def test_check_refuses_simulated_in_production(self):
        self.assertEqual([e.id for e in check_payment_provider()], ["cp.E005"])


class BillingChecksTests(TestCase):
    def test_tckn(self):
        self.assertTrue(tckn_valid("10000000146"))
        for bad in ("10000000147", "00000000146", "1000000014", "1000000014a"):
            self.assertFalse(tckn_valid(bad), bad)

    def test_vkn(self):
        self.assertTrue(vkn_valid("1234567890"))
        self.assertEqual(sum(vkn_valid("123456789" + str(d)) for d in range(10)), 1)   # one check digit per prefix
        self.assertFalse(vkn_valid("123456789"))
