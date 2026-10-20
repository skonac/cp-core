"""Talk to iyzico from the server, without the frontend.

    cp-core-manage iyzico_test                                   # ping: are the URL and keys right?
    cp-core-manage iyzico_test --buy +905550000002 --product 1   # start a real (sandbox) purchase, print the payment page URL

The --buy purchase is a normal purchase: open the printed URL in a browser, pay with a sandbox test card,
iyzico sends the browser back to https://<site>/api/shop/iyzico/callback, and the purchase becomes
paid (credits added) or failed. The account needs the organizer role and a complete billing profile.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from shop import iyzico
from shop.api import CALLBACK_PATH, billing_json
from shop.models import BillingProfile, Product
from shop.payments import PaymentError, start


class _Ping(iyzico._Client, iyzico.iyzipay.ApiTest):
    pass


class Command(BaseCommand):
    help = "Check the connection to iyzico; optionally start a purchase and print its payment page URL."

    def add_arguments(self, parser):
        parser.add_argument("--buy", metavar="PHONE", help="account that buys")
        parser.add_argument("--product", type=int, help="product id (admin: Shop > Products)")
        parser.add_argument("--site", default="https://cleverphotos.org", help="our public URL, for iyzico's return")

    def handle(self, *args, **opts):
        provider = settings.PAYMENT_PROVIDER
        if not provider.startswith("iyzico"):
            raise CommandError(f"CP_PAYMENT_PROVIDER is {provider!r}; set iyzico-sandbox in /etc/cp-core/env first")
        self.stdout.write(f"provider {provider}, {settings.IYZICO_BASE_URL}, key {settings.IYZICO_API_KEY[:12]}…")
        try:
            answer = _Ping().retrieve(iyzico.options())
        except iyzico.IyzicoError as e:
            raise CommandError(f"ping failed: {e}")
        self.stdout.write(f"ping: {answer}")
        if not opts["buy"]:
            return
        user = User.objects.filter(phone=opts["buy"]).first()
        product = Product.objects.filter(pk=opts["product"] or 0, is_active=True).first()
        profile = BillingProfile.objects.filter(user=user).first() if user else None
        if user is None or product is None:
            raise CommandError("unknown account or product (needs --buy PHONE and --product ID of a product on sale)")
        if profile is None or not profile.is_complete:
            raise CommandError("the account has no complete billing profile (PUT /api/me/billing, or the admin)")
        try:
            purchase, page = start(user, product, None, billing_json(profile)["billing"], profile,
                                   opts["site"].rstrip("/") + CALLBACK_PATH)
        except PaymentError as e:
            raise CommandError(f"initialize failed: {e.code}: {e.message}")
        self.stdout.write(self.style.SUCCESS(f"purchase {purchase.pk} pending, token {purchase.provider_token}"))
        self.stdout.write(f"open in a browser:\n{page}")
