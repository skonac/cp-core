"""Shop: the API contract (every /api/shop/ endpoint, responses checked against their schemas),
credits added once, product data copied at purchase time, production guard."""
import re

from django.core.cache import cache
from unittest import mock

from django.test import TestCase, override_settings

from accounts.models import User
from common.testing import ApiClient
from config.api import api
from credits.models import CreditTransaction

from .apps import check_payment_provider
from .billing import tckn_valid, vkn_valid
from .models import BillingProfile, Product, Purchase
from .payments import complete, finish_iyzico, start

PERSON = {"type": "individual", "firstName": "Ece", "lastName": "Yılmaz", "identityNumber": "10000000146",
          "email": "ece@example.com", "address": "Atatürk Cad. 1", "district": "Çankaya", "city": "Ankara"}


def _norm(route: str) -> str:
    return re.sub(r"<(?:\w+:)?\w+>|\{\w+\}", "{}", route.strip("/"))


class FakeIyzico:
    """Stands in for shop.iyzico._send: records calls, answers like iyzico's sandbox."""
    PAGE = "https://sandbox-cpp.iyzipay.com?token=tok-1&lang=tr"

    def __init__(self, payment_status="SUCCESS", paid_price=None, unreachable=False):
        self.calls, self.fail_next = [], False
        self.payment_status, self.paid_price, self.unreachable = payment_status, paid_price, unreachable

    def __call__(self, resource, method, body):
        from shop.iyzico import IyzicoError
        self.calls.append((method, body))
        if self.fail_next:
            self.fail_next = False
            raise IyzicoError("payment_provider_error", "Geçersiz istek", {"status": "failure", "errorCode": "11"})
        if method == "create":
            return {"status": "success", "token": f"tok-{len(self.calls)}", "paymentPageUrl": self.PAGE,
                    "conversationId": body["conversationId"]}
        if self.unreachable:
            raise IyzicoError("payment_provider_unreachable", "TimeoutError")
        purchase = Purchase.objects.get(provider_token=body["token"])
        return {"status": "success", "paymentStatus": self.payment_status, "paymentId": "28000001",
                "conversationId": body["conversationId"], "fraudStatus": 1,
                "paidPrice": self.paid_price if self.paid_price is not None else float(purchase.amount)}


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
        self.assertCode(r, 201, "purchase_started")
        data = r.json()["data"]
        self.assertIsNone(data["paymentPageUrl"])                                    # simulated: paid at once
        self.assertEqual((data["purchase"]["status"], data["purchase"]["productName"]["tr"], data["purchase"]["creditAmount"],
                          data["purchase"]["amount"], data["creditBalance"]), ("paid", "2000cr", 2000, 4000.0, 1988))
        self.assertEqual(Purchase.objects.get().billing["companyName"], "Foto Ltd.")   # copied into the purchase
        history = c.get("/api/shop/purchases").json()["data"]
        self.assertEqual((history["total"], history["purchases"][0]["id"]), (1, data["purchase"]["id"]))
        r = c.get(f"/api/shop/purchases/{data['purchase']['id']}")
        self.assertCode(r, 200, "purchase_retrieved")
        self.assertEqual(r.json()["data"]["purchase"]["status"], "paid")
        self.assertCode(c.get("/api/shop/purchases/99999"), 404, "not_found")

        # --- iyzico (sandbox), the call to iyzico faked: start -> payment page URL, pending
        fake = FakeIyzico()
        with self.settings(PAYMENT_PROVIDER="iyzico-sandbox"), mock.patch("shop.iyzico._send", fake):
            r = c.post_json("/api/shop/purchases", {"productId": pid, "pricingVersion": version})
            self.assertCode(r, 201, "purchase_started")
            data = r.json()["data"]
            self.assertEqual((data["purchase"]["status"], data["paymentPageUrl"]), ("pending", fake.PAGE))
            body = fake.calls[0][1]
            self.assertEqual((body["price"], body["paidPrice"], body["basketItems"][0]["itemType"],
                              body["buyer"]["gsmNumber"], body["billingAddress"]["contactName"]),
                             ("4000.00", "4000.00", "VIRTUAL", "+905550000302", "Foto Ltd."))
            self.assertTrue(body["callbackUrl"].endswith("/api/shop/iyzico/callback"))
            fake.fail_next = True
            self.assertCode(c.post_json("/api/shop/purchases", {"productId": pid, "pricingVersion": version}),
                            502, "payment_provider_error")

        called = {(m, _norm(route)) for m, route, _ in c.checked}
        documented = {(m.upper(), _norm(path)) for path, ops in api.get_openapi_schema()["paths"].items() for m in ops
                      if path.startswith(("/api/shop/", "/api/me/billing"))}
        self.assertEqual(documented - called, set(), "shop endpoints never called by this test")


@override_settings(PAYMENT_PROVIDER="iyzico-sandbox")
class PurchaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("+905550000201", is_organizer=True, credit_balance=-12)
        self.product = Product.objects.create(name_tr="2000cr", name_en="2000cr", title_tr="Mini paket",
                                              credits=2000, price=4000)
        self.profile = BillingProfile.objects.create(
            user=self.user, first_name="Ece", last_name="Yılmaz", identity_number="10000000146", email="e@x.co",
            address="Cad. 1", district="Çankaya", city="Ankara")

    def _start(self, fake):
        with mock.patch("shop.iyzico._send", fake):
            return start(self.user, self.product, "127.0.0.1", {}, self.profile, "https://x/api/shop/iyzico/callback")[0]

    def _balance(self):
        return User.objects.get(pk=self.user.pk).credit_balance

    @override_settings(PAYMENT_PROVIDER="simulated")
    def test_simulated_adds_credits_once(self):
        purchase, page = start(self.user, self.product, "127.0.0.1", {}, self.profile, "")
        self.assertEqual((purchase.status, purchase.provider_payment_id, page), ("paid", f"SIM-{purchase.pk}", None))
        self.assertEqual(self._balance(), 1988)
        complete(purchase, "again")                              # a repeated callback changes nothing
        self.assertEqual(self._balance(), 1988)
        self.assertEqual(CreditTransaction.objects.get().ref, f"purchase:{purchase.pk}")

    def test_iyzico_return_success_adds_credits(self):
        fake = FakeIyzico()
        purchase = self._start(fake)
        self.assertEqual((purchase.status, purchase.provider_token), ("pending", "tok-1"))
        with mock.patch("shop.iyzico._send", fake):
            r = self.client.post("/api/shop/iyzico/callback", {"token": "tok-1"})
            self.assertRedirects(r, f"/organizer/credits?purchase={purchase.pk}&status=paid", fetch_redirect_response=False)
            self.client.post("/api/shop/iyzico/callback", {"token": "tok-1"})     # again: nothing changes
        purchase.refresh_from_db()
        self.assertEqual((purchase.status, purchase.provider_payment_id, self._balance()), ("paid", "28000001", 1988))
        self.assertEqual(len(fake.calls), 2)      # initialize + one retrieve; the second return asks nobody

    def test_iyzico_declined_or_wrong_price_fails(self):
        for fake in (FakeIyzico(payment_status="FAILURE"), FakeIyzico(paid_price=1.0)):
            purchase = self._start(fake)
            with mock.patch("shop.iyzico._send", fake):
                purchase = finish_iyzico(purchase.provider_token)
            self.assertEqual(purchase.status, "failed")
        self.assertEqual(self._balance(), -12)

    def test_iyzico_unreachable_on_return_stays_pending(self):
        fake = FakeIyzico(unreachable=True)
        purchase = self._start(fake)
        with mock.patch("shop.iyzico._send", fake):
            purchase = finish_iyzico(purchase.provider_token)
        self.assertEqual((purchase.status, purchase.error_code), ("pending", "payment_provider_unreachable"))

    def test_unknown_token(self):
        r = self.client.post("/api/shop/iyzico/callback", {"token": "nope"})
        self.assertRedirects(r, "/organizer/credits?purchase=&status=unknown", fetch_redirect_response=False)

    @override_settings(PAYMENT_PROVIDER="simulated")
    def test_product_copied_at_purchase_time(self):
        purchase = start(self.user, self.product, None, {}, self.profile, "")[0]
        self.product.price, self.product.credits = 9999, 1
        self.product.save()
        purchase = Purchase.objects.get(pk=purchase.pk)
        self.assertEqual((purchase.product_name_tr, purchase.credits, purchase.list_price, purchase.amount),
                         ("2000cr", 2000, 4000, 4000))

    def test_checks(self):
        with self.settings(PAYMENT_PROVIDER="simulated", IS_DEV=False):
            self.assertEqual([e.id for e in check_payment_provider()], ["cp.E005"])
        with self.settings(PAYMENT_PROVIDER="iyzico-sandbox", IS_DEV=False):
            self.assertEqual([e.id for e in check_payment_provider()], ["cp.E005"])
        with self.settings(PAYMENT_PROVIDER="iyzico-production", IS_DEV=False, IYZICO_API_KEY=""):
            self.assertEqual([e.id for e in check_payment_provider()], ["cp.E007"])
        with self.settings(PAYMENT_PROVIDER="iyzico"):
            self.assertEqual([e.id for e in check_payment_provider()], ["cp.E004"])


class BillingChecksTests(TestCase):
    def test_tckn(self):
        self.assertTrue(tckn_valid("10000000146"))
        for bad in ("10000000147", "00000000146", "1000000014", "1000000014a"):
            self.assertFalse(tckn_valid(bad), bad)

    def test_vkn(self):
        self.assertTrue(vkn_valid("1234567890"))
        self.assertEqual(sum(vkn_valid("123456789" + str(d)) for d in range(10)), 1)   # one check digit per prefix
        self.assertFalse(vkn_valid("123456789"))
