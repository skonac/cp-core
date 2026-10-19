"""Purchases: credits added once, package data copied, production guard."""
from django.test import TestCase, override_settings

from accounts.models import User

from .apps import check_payment_provider
from .models import CreditPackage, CreditTransaction, Purchase
from .payments import buy, complete


class PurchaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("+905550000201", is_organizer=True, credit_balance=-12)
        self.package = CreditPackage.objects.create(name="2000cr", title="Mini paket", credits=2000, price=4000)

    def test_simulated_buy_adds_credits_once(self):
        purchase = buy(self.user, self.package, "127.0.0.1")
        self.assertEqual((purchase.status, purchase.provider, purchase.provider_payment_id),
                         ("paid", "simulated", f"SIM-{purchase.pk}"))
        self.assertEqual(User.objects.get(pk=self.user.pk).credit_balance, 1988)
        complete(purchase, "again")                              # a repeated callback changes nothing
        self.assertEqual(User.objects.get(pk=self.user.pk).credit_balance, 1988)
        self.assertEqual(CreditTransaction.objects.get().ref, f"purchase:{purchase.pk}")

    def test_package_copied_at_purchase_time(self):
        purchase = buy(self.user, self.package)
        self.package.price, self.package.credits = 9999, 1
        self.package.save()
        purchase = Purchase.objects.get(pk=purchase.pk)
        self.assertEqual((purchase.credits, purchase.list_price, purchase.amount), (2000, 4000, 4000))

    @override_settings(PAYMENT_PROVIDER="simulated", IS_DEV=False)
    def test_check_refuses_simulated_in_production(self):
        self.assertEqual([e.id for e in check_payment_provider()], ["cp.E005"])
