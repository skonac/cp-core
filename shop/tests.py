"""Shop: the API contract (every /api/shop/ endpoint, responses checked against their schemas),
credits added once, product data copied at purchase time, production guard."""
import hashlib
import hmac
import json
import re
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from accounts.models import User
from common.testing import ApiClient
from config.api import api
from credits.models import CreditTransaction

from .apps import check_payment_provider, check_sales_documents
from .billing import tckn_valid, vkn_valid
from . import pricing
from .models import BillingProfile, Product, PromoCode, Purchase
from .documents import attach
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
                          ("5000cr", {"tr": "", "en": ""}, 1.5, 0)])        # no discount on this account
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

        # --- the two documents, read before buying, then the purchase stores them
        self.assertCode(c.get("/api/shop/obf", {"product": str(old.pk)}), 404, "not_found")
        r = c.get("/api/shop/obf", {"product": pid, "locale": "tr"})
        self.assertEqual((r.status_code, r["Content-Type"], r["X-Frame-Options"]),
                         (200, "text/html; charset=utf-8", "SAMEORIGIN"))         # cp-web embeds it
        self.assertContains(r, "Ön Bilgilendirme Formu")
        self.assertContains(r, "Foto Ltd.")
        self.assertContains(r, "4.000,00 TL")
        r = c.get("/api/shop/mss", {"product": pid, "locale": "en"})
        self.assertContains(r, "Distance Sales Agreement")
        self.assertContains(r, "TRY 4,000.00")
        self.assertEqual(cat["documentsVersion"], "001")
        buy = {"productId": pid, "pricingVersion": version}
        self.assertCode(c.post_json("/api/shop/purchases", buy), 409, "documents_changed")
        buy["documentsVersion"] = cat["documentsVersion"]
        self.assertCode(c.post_json("/api/shop/purchases", {**buy, "acceptedObf": True}), 422, "documents_not_accepted")
        buy.update(acceptedObf=True, acceptedMss=True)
        self.assertFalse(Purchase.objects.exists())

        r = c.post_json("/api/shop/purchases", buy)
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
        url = r.json()["data"]["purchase"]["documentsUrl"]
        self.assertEqual(url, f"/api/shop/purchases/{data['purchase']['id']}/documents")
        page = c.get(url)
        self.assertEqual((page.status_code, page["X-Frame-Options"]), (200, "SAMEORIGIN"))
        stored = Purchase.objects.get()
        self.assertEqual(page.content.decode(), stored.docs_html)
        self.assertEqual((stored.docs_version, stored.user_agent, stored.ip), ("001", "TestBrowser/1.0", "127.0.0.1"))
        self.assertCode(c.get("/api/shop/purchases/99999/documents"), 404, "not_found")
        self.assertEqual(r.json()["data"]["purchase"]["status"], "paid")
        self.assertCode(c.get("/api/shop/purchases/99999"), 404, "not_found")

        # --- iyzico (sandbox), the call to iyzico faked: start -> payment page URL, pending
        fake = FakeIyzico()
        with self.settings(PAYMENT_PROVIDER="iyzico-sandbox"), mock.patch("shop.iyzico._send", fake):
            r = c.post_json("/api/shop/purchases", buy)
            self.assertCode(r, 201, "purchase_started")
            data = r.json()["data"]
            self.assertEqual((data["purchase"]["status"], data["paymentPageUrl"]), ("pending", fake.PAGE))
            body = fake.calls[0][1]
            self.assertEqual((body["price"], body["paidPrice"], body["basketItems"][0]["itemType"],
                              body["buyer"]["gsmNumber"], body["billingAddress"]["contactName"]),
                             ("4000.00", "4000.00", "VIRTUAL", "+905550000302", "Foto Ltd."))
            self.assertTrue(body["callbackUrl"].endswith("/api/shop/iyzico/callback"))
            fake.fail_next = True
            self.assertCode(c.post_json("/api/shop/purchases", buy), 502, "payment_provider_error")

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

    def _hook(self, body, **headers):
        return self.client.post("/api/shop/iyzico/webhook", json.dumps(body) if isinstance(body, dict) else body,
                                content_type="application/json", **headers)

    def _event(self, purchase, status="SUCCESS"):
        return {"iyziEventType": "CHECKOUT_FORM_AUTH", "iyziEventTime": 1792500000000, "iyziPaymentId": 28000001,
                "iyziReferenceCode": "ref-1", "merchantId": 1, "paymentConversationId": purchase.conversation_id,
                "token": purchase.provider_token, "status": status}

    def test_webhook_completes_once(self):
        """The browser never came back: iyzico's notification settles the purchase, once."""
        fake = FakeIyzico()
        purchase = self._start(fake)
        with mock.patch("shop.iyzico._send", fake):
            r = self._hook(self._event(purchase))
            self.assertEqual((r.status_code, r.json()), (200, {"status": "paid"}))
            self.assertEqual(self._hook(self._event(purchase)).status_code, 200)            # a retry: nothing changes
            self.client.post("/api/shop/iyzico/callback", {"token": purchase.provider_token})   # the browser, late
        self.assertEqual((Purchase.objects.get(pk=purchase.pk).status, self._balance()), ("paid", 1988))
        self.assertEqual(len(fake.calls), 2)        # initialize + one retrieve: later arrivals ask nobody
        self.assertEqual(CreditTransaction.objects.count(), 1)

    def test_webhook_body_not_trusted(self):
        """A notification saying SUCCESS for a declined payment: iyzico's own answer decides."""
        fake = FakeIyzico(payment_status="FAILURE")
        purchase = self._start(fake)
        with mock.patch("shop.iyzico._send", fake):
            r = self._hook(self._event(purchase, "SUCCESS"))
        self.assertEqual((r.status_code, r.json()["status"], self._balance()), (200, "failed", -12))

    def test_webhook_unknown_bad_and_unreachable(self):
        self.assertEqual(self._hook({"token": "nope"}).json(), {"status": "ignored"})     # 200: no retries
        self.assertEqual(self._hook("not json").status_code, 400)
        self.assertEqual(self.client.get("/api/shop/iyzico/webhook").status_code, 405)
        fake = FakeIyzico(unreachable=True)
        purchase = self._start(fake)
        with mock.patch("shop.iyzico._send", fake):
            self.assertEqual(self._hook(self._event(purchase)).status_code, 503)       # iyzico retries later
        self.assertEqual(Purchase.objects.get(pk=purchase.pk).status, "pending")

    @override_settings(IYZICO_SECRET_KEY="sk-test")
    def test_webhook_signature(self):
        fake = FakeIyzico()
        purchase = self._start(fake)
        event = self._event(purchase)
        message = "sk-test" + "CHECKOUT_FORM_AUTH" + "28000001" + purchase.provider_token + purchase.conversation_id + "SUCCESS"
        good = hmac.new(b"sk-test", message.encode(), hashlib.sha256).hexdigest()
        with mock.patch("shop.iyzico._send", fake):
            self.assertEqual(self._hook(event, HTTP_X_IYZ_SIGNATURE_V3="0" * 64).status_code, 401)
            self.assertEqual(Purchase.objects.get(pk=purchase.pk).status, "pending")
            self.assertEqual(self._hook(event, HTTP_X_IYZ_SIGNATURE_V3=good).status_code, 200)
        self.assertEqual(Purchase.objects.get(pk=purchase.pk).status, "paid")

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


class DocumentsTests(TestCase):
    """The stored ÖBF + MSS: filled from the purchase's copies, four texts, hash, never changed."""
    def setUp(self):
        self.user = User.objects.create_user("+905550000401", is_organizer=True)
        self.product = Product.objects.create(name_tr="2000cr", name_en="2000cr", title_tr="Mini paket",
                                              title_en="Small pack", credits=2000, price=Decimal("12345.5"))
        self.profile = BillingProfile.objects.create(
            user=self.user, first_name="Ece", last_name="<b>Yılmaz</b>", identity_number="10000000146",
            email="e@x.co", address="Cad. 1", district="Çankaya", city="Ankara")

    @override_settings(PAYMENT_PROVIDER="simulated")
    def test_stored_page(self):
        from .api import billing_json
        purchase = start(self.user, self.product, "10.0.0.1", billing_json(self.profile)["billing"], self.profile, "",
                         "Mozilla/5.0 (iPhone)")[0]
        html = purchase.docs_html
        heads = [h for h in ("Ön Bilgilendirme Formu", "Mesafeli Satış Sözleşmesi", "Preliminary Information Form",
                             "Distance Sales Agreement")]
        self.assertEqual([html.index("<h1>" + h) for h in heads], sorted(html.index("<h1>" + h) for h in heads))
        self.assertEqual(html.count('<section class="doc">'), 4)
        for text in (purchase.number, "CP-%06d" % purchase.pk, "12.345,50 TL", "TRY 12,345.50", "2.000",
                     "Mini paket", "Small pack", "10000000146", "Cad. 1, Çankaya / Ankara, Türkiye",
                     "+905550000401", "&lt;b&gt;Yılmaz&lt;/b&gt;", "Sabiha Pınar Gesli"):
            self.assertIn(text, html)
        self.assertNotIn("{{", html)
        self.assertEqual(purchase.docs_sha256, hashlib.sha256(html.encode()).hexdigest())
        self.assertEqual((purchase.user_agent, purchase.docs_version), ("Mozilla/5.0 (iPhone)", "001"))
        self.assertIsNotNone(purchase.docs_accepted_at)
        self.product.title_tr = "Yeni ad"                    # later product changes don't touch the stored page
        self.product.save()
        self.assertEqual(Purchase.objects.get(pk=purchase.pk).docs_html, html)

    @override_settings(PAYMENT_PROVIDER="simulated")
    def test_no_purchase_without_documents(self):
        with mock.patch("shop.payments.documents.attach", side_effect=RuntimeError("render failed")):
            with self.assertRaises(RuntimeError):
                start(self.user, self.product, None, {}, self.profile, "")
        self.assertFalse(Purchase.objects.exists())

    def test_staff_page(self):
        with self.settings(PAYMENT_PROVIDER="simulated"):
            purchase = start(self.user, self.product, None, {}, self.profile, "")[0]
        staff = User.objects.create_user("+905550000499", is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(f"/dj/staff/purchases/{purchase.pk}/documents/")
        self.assertEqual(r.content.decode(), Purchase.objects.get(pk=purchase.pk).docs_html)
        self.assertContains(self.client.get(f"/dj/staff/users/{self.user.pk}/"), f"/dj/staff/purchases/{purchase.pk}/documents/")
        staff.is_superuser = True
        staff.save()
        self.assertContains(self.client.get(f"/admin/shop/purchase/{purchase.pk}/change/"),
                            f"/dj/staff/purchases/{purchase.pk}/documents/")

    def test_check(self):
        self.assertEqual(check_sales_documents(), [])
        with self.settings(SALES_DOCS_VERSION="1"):
            self.assertEqual([e.id for e in check_sales_documents()], ["cp.E008"])
        with self.settings(SALES_DOCS_VERSION="999"):
            self.assertEqual([e.id for e in check_sales_documents()], ["cp.E009"] * 5)


class PromoCodeModelTests(TestCase):
    """Admin-side rules of PromoCode and User.discount_percent (the pricing itself: later steps)."""

    def _promo(self, **kw):
        now = timezone.now()
        data = {"code": "XMAS2027", "type": "percent", "value": Decimal("15"),
                "valid_from": now - timedelta(days=1), "valid_until": now + timedelta(days=1)} | kw
        return PromoCode(**data)

    def test_lowercase_and_validity(self):
        promo = self._promo(code=" XMAS2027 ", product_ids=[2, 3])
        promo.full_clean()
        promo.save()
        self.assertEqual((promo.pk, str(promo)), ("xmas2027", "XMAS2027"))
        now = timezone.now()
        self.assertTrue(promo.is_valid_at(now))
        self.assertFalse(promo.is_valid_at(promo.valid_until))
        self.assertFalse(promo.is_valid_at(promo.valid_from - timedelta(seconds=1)))
        self.assertEqual((promo.covers(2), promo.covers(4)), (True, False))
        self.assertTrue(self._promo(code="all").covers(4))      # empty list: every product

    def test_clean_rejects(self):
        now = timezone.now()
        for kw, field in [({"value": Decimal("0")}, "value"), ({"value": Decimal("101")}, "value"),
                          ({"value": Decimal("12.5")}, "value"),
                          ({"valid_until": now - timedelta(days=2)}, "valid_until"),
                          ({"code": "A" * 17}, "code")]:
            with self.assertRaises(ValidationError) as e:
                self._promo(**kw).full_clean()
            self.assertIn(field, e.exception.message_dict, kw)
        self._promo(type="fixed", value=Decimal("250.50")).full_clean()   # fixed: any amount > 0

    def test_db_constraints(self):
        now = timezone.now()
        for kw in [{"value": Decimal("150")}, {"valid_until": now - timedelta(days=2)}, {"value": Decimal("-1")}]:
            with self.subTest(kw), self.assertRaises(IntegrityError), transaction.atomic():
                PromoCode.objects.bulk_create([self._promo(**kw)])
        with self.assertRaises(IntegrityError), transaction.atomic():     # save() lowercases; raw rows can't skip it
            PromoCode.objects.bulk_create([self._promo(code="Upper")])

    def test_user_discount(self):
        user = User.objects.create_user("+905550000601")
        self.assertEqual(user.discount_percent, 0)
        user.discount_percent = 101
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_admin(self):
        staff = User.objects.create_user("+905550000602", is_staff=True)
        self.client.force_login(staff)
        r = self.client.post("/admin/shop/promocode/add/", {
            "code": "Bahar26", "type": "fixed", "value": "100", "product_ids": "1,2",
            "valid_from_0": "2026-03-01", "valid_from_1": "00:00:00",
            "valid_until_0": "2026-04-01", "valid_until_1": "00:00:00", "note": ""})
        self.assertEqual(r.status_code, 302, r.content.decode()[:2000])
        promo = PromoCode.objects.get()
        self.assertEqual((promo.code, promo.product_ids), ("bahar26", [1, 2]))
        self.assertContains(self.client.get("/admin/shop/promocode/"), "BAHAR26")
        self.assertEqual(self.client.get("/admin/shop/promocode/bahar26/change/").status_code, 200)
        user = User.objects.create_user("+905550000603")
        self.assertContains(self.client.get(f"/admin/accounts/user/{user.pk}/change/"), "discount_percent")


@override_settings(PAYMENT_PROVIDER="simulated")
class PricingTests(TestCase):
    """shop/pricing.py and where it is used: the catalog, the ÖBF/MSS preview, purchase creation."""

    def setUp(self):
        self.p1 = Product.objects.create(name_tr="2000cr", name_en="2000cr", credits=2000, price=3600)
        self.p2 = Product.objects.create(name_tr="5000cr", name_en="5000cr", credits=5000, price=Decimal("1999"))
        self.org = User.objects.create_user("+905550000701", is_organizer=True, discount_percent=20)
        BillingProfile.objects.create(user=self.org, first_name="Ece", last_name="Yılmaz",
                                      identity_number="10000000146", email="e@x.co", address="Cad. 1",
                                      district="Çankaya", city="Ankara")
        now = timezone.now()
        self.promo = PromoCode.objects.create(code="Xmas2027", type="percent", value=50, product_ids=[self.p1.pk],
                                              valid_from=now - timedelta(days=1), valid_until=now + timedelta(days=1))

    def _use(self, user, code="xmas2027"):
        self.client.force_login(user)
        session = self.client.session
        session[pricing.SESSION_KEY] = code
        session.save()

    def _catalog(self):
        data = self.client.get("/api/shop/products").json()["data"]
        return data, {p["id"]: (p["price"], p["finalPrice"], p["discountPercent"]) for p in data["products"]}

    def test_price_for(self):
        P = PromoCode
        fixed = P(code="f", type="fixed", value=Decimal("100"), valid_from=timezone.now(), valid_until=timezone.now())
        big = P(code="g", type="fixed", value=Decimal("5000"), valid_from=timezone.now(), valid_until=timezone.now())
        cases = [
            (self.p1, self.org, None, ("3600", "2880.00", "user", 20, 20, "")),
            (self.p2, self.org, None, ("1999", "1599.00", "user", 20, 20, "")),       # 1599.20 → whole TL down
            (self.p1, self.org, self.promo, ("3600", "1800.00", "promo", 50, 50, "xmas2027")),
            (self.p2, self.org, self.promo, ("1999", "1599.00", "user", 20, 20, "")),  # not covered: own discount
            (self.p1, self.org, fixed, ("3600", "3500.00", "promo", None, 3, "f")),
            (self.p1, self.org, big, ("3600", "0.00", "promo", None, 100, "g")),       # never below 0
            (self.p2, User(discount_percent=0), None, ("1999", "1999", "none", None, 0, "")),
        ]
        for product, user, promo, want in cases:
            pr = pricing.price_for(product, user, promo)
            self.assertEqual((str(pr.list_price), str(pr.final), pr.source, pr.percent, pr.shown_percent,
                              pr.promo_code), want, (product, promo))

    def test_catalog(self):
        data, prices = self._catalog()                                      # signed out: list prices
        self.assertEqual((prices[str(self.p1.pk)], data["appliedPromo"]), ((3600.0, 3600.0, 0), None))
        anon_version = data["pricingVersion"]
        self.client.force_login(self.org)                                   # own discount everywhere
        data, prices = self._catalog()
        self.assertEqual(prices, {str(self.p1.pk): (3600.0, 2880.0, 20), str(self.p2.pk): (1999.0, 1599.0, 20)})
        self.assertIsNone(data["appliedPromo"])
        self.assertEqual(data["products"][0]["unitPrice"], 1.44)
        own_version = data["pricingVersion"]
        self._use(self.org)                                                 # promo on p1, own discount on p2
        data, prices = self._catalog()
        self.assertEqual(prices, {str(self.p1.pk): (3600.0, 1800.0, 50), str(self.p2.pk): (1999.0, 1599.0, 20)})
        self.assertEqual(data["appliedPromo"], "XMAS2027")
        self.assertEqual(len({anon_version, own_version, data["pricingVersion"]}), 3)
        PromoCode.objects.filter(pk="xmas2027").update(valid_until=timezone.now() - timedelta(seconds=1))
        data, prices = self._catalog()                                      # expired: back to the own discount
        self.assertEqual((prices[str(self.p1.pk)], data["appliedPromo"], data["pricingVersion"]),
                         ((3600.0, 2880.0, 20), None, own_version))

    def test_promo_only_for_organizers(self):
        guest = User.objects.create_user("+905550000702", is_participant=True, discount_percent=10)
        self._use(guest)
        data, prices = self._catalog()
        self.assertEqual((prices[str(self.p1.pk)], data["appliedPromo"]), ((3600.0, 3240.0, 10), None))

    def test_preview_and_purchase(self):
        self._use(self.org)
        mss = self.client.get(f"/api/shop/mss?product={self.p1.pk}").content.decode()
        self.assertIn("1.800,00", mss)
        self.assertNotIn("3.600,00", mss)
        version = self._catalog()[0]["pricingVersion"]
        body = {"productId": str(self.p1.pk), "pricingVersion": version, "documentsVersion": settings.SALES_DOCS_VERSION,
                "acceptedObf": True, "acceptedMss": True}
        r = self.client.post("/api/shop/purchases", body, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        purchase = Purchase.objects.get()
        self.assertEqual((purchase.list_price, purchase.amount, purchase.discount_source, purchase.discount_percent,
                          purchase.promo_code), (Decimal("3600"), Decimal("1800"), "promo", 50, "xmas2027"))
        self.assertIn("1.800,00", purchase.docs_html)
        self.org.discount_percent = 30                                      # a shown price changed: refused
        self.org.save()
        body["productId"] = str(self.p2.pk)
        r = self.client.post("/api/shop/purchases", body, content_type="application/json")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "pricing_changed"))
        body["pricingVersion"] = self._catalog()[0]["pricingVersion"]
        self.assertEqual(self.client.post("/api/shop/purchases", body, content_type="application/json").status_code, 201)
        latest = Purchase.objects.latest("pk")
        self.assertEqual((latest.amount, latest.discount_source, latest.discount_percent, latest.promo_code),
                         (Decimal("1399"), "user", 30, ""))
