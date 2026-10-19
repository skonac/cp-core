"""CreditPackage (what we sell), Purchase (one buy attempt, with the payment provider's ids),
CreditTransaction: purchases and admin adjustments. The balance itself is User.credit_balance;
photo uploads are not logged here (every photo row cost 1 credit). design/core.md."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class CreditPackage(models.Model):
    """A credit package for sale ("1000cr": 1000 credits for a price). Edited in the admin only.
    Change a price by editing the row: the store's pricingVersion (a hash of the active packages)
    changes with it, so a purchase started at the old price is refused."""
    name = models.CharField(max_length=40)                          # "2000cr": short product name / code
    title = models.CharField(max_length=80, blank=True)             # "Mini paket": shown as the heading
    description = models.CharField(max_length=200, blank=True)      # subtitle: "Küçük düğünler için"
    credits = models.PositiveIntegerField()                         # credits the package adds
    price = models.DecimalField(max_digits=10, decimal_places=2)    # regular price, what the organizer pays
    currency = models.CharField(max_length=3, default="TRY")
    is_active = models.BooleanField(default=True)                   # False: hidden from the store
    is_featured = models.BooleanField(default=False)                # highlighted in the store
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sort_order", "credits"]

    def __str__(self):
        return f"{self.name} ({self.credits} cr, {self.price} {self.currency})"


class Purchase(models.Model):
    """One purchase attempt of one package. The package's name, credits and price are copied at
    purchase time (the package may change later). When the payment succeeds: status paid, the
    credits are added through a CreditTransaction (ref = "purchase:<id>"), in one DB transaction.
    provider "simulated" (non-production only) succeeds at once; "iyzico" later: conversation_id is
    what we send to iyzico, token / payment_id what it answers, provider_response its last raw answer."""
    class Status(models.TextChoices):
        PENDING = "pending"    # sent to the payment provider, no result yet
        PAID = "paid"          # payment confirmed, credits added
        FAILED = "failed"      # declined, cancelled or errored; no credits

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases")
    package = models.ForeignKey(CreditPackage, on_delete=models.PROTECT, related_name="purchases")
    package_name = models.CharField(max_length=40)                  # copies at purchase time
    credits = models.PositiveIntegerField()
    list_price = models.DecimalField(max_digits=10, decimal_places=2)   # the package's regular price
    amount = models.DecimalField(max_digits=10, decimal_places=2)       # charged (= list_price until discounts exist)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=16)                      # "simulated" | "iyzico"
    conversation_id = models.CharField(max_length=64, unique=True)  # our id for this attempt, sent to the provider
    provider_token = models.CharField(max_length=200, blank=True)   # iyzico checkout form token
    provider_payment_id = models.CharField(max_length=64, blank=True)   # iyzico paymentId once paid
    provider_response = models.JSONField(null=True, blank=True)     # last raw answer from the provider
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"], name="purchase_user")]

    def __str__(self):
        return f"{self.user} {self.package_name} {self.status}"


class CreditTransaction(models.Model):
    class Kind(models.TextChoices):
        PURCHASE = "purchase"
        ADJUSTMENT = "adjustment"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credit_transactions")
    delta = models.IntegerField()                                   # +2000 for a package, -10 for a correction
    kind = models.CharField(max_length=12, choices=Kind.choices)
    ref = models.CharField(max_length=100, blank=True)              # payment reference
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")                     # admin who made an adjustment
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user} {self.delta:+d} {self.kind}"
