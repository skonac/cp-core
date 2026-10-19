"""Shop: Product (what we sell) and Purchase (one buy of one product, with the payment provider's ids).
Paying for a product adds credits through credits.CreditTransaction (shop/payments.py). design/core.md."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Product(models.Model):
    """A product for sale, edited in the admin only. Today every product grants credits ("2000cr":
    2000 credits for a price); another kind of product would add a `kind` column. Disable
    (is_active) instead of deleting: purchases point at their product. Changing a price changes the
    catalog's pricingVersion, so a purchase started at the old price is refused."""
    # Shown texts in both languages the site has (tr, en); the API returns both.
    name_tr = models.CharField(max_length=40)                       # "2000cr": short product name
    name_en = models.CharField(max_length=40)
    title_tr = models.CharField(max_length=80, blank=True)          # "Mini paket": heading
    title_en = models.CharField(max_length=80, blank=True)          # "Small pack"
    description_tr = models.CharField(max_length=200, blank=True)   # subtitle: "Küçük düğünler için"
    description_en = models.CharField(max_length=200, blank=True)
    credits = models.PositiveIntegerField()                         # credits the product adds to the account
    price = models.DecimalField(max_digits=10, decimal_places=2)    # regular price
    currency = models.CharField(max_length=3, default="TRY")
    is_active = models.BooleanField(default=True)                   # False: not on sale
    is_featured = models.BooleanField(default=False)                # highlighted in the shop
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sort_order", "credits"]

    def __str__(self):
        return f"{self.name_tr} ({self.credits} cr, {self.price} {self.currency})"


class Purchase(models.Model):
    """One purchase of one product. The product's name, credits and price are copied at purchase
    time (the product may change later). When the payment succeeds: status paid, and the credits are
    added through a CreditTransaction (ref = "purchase:<id>"), in one DB transaction.
    provider "simulated" (non-production only) succeeds at once; "iyzico" later: conversation_id is
    what we send to iyzico, token / payment_id what it answers, provider_response its last raw answer."""
    class Status(models.TextChoices):
        PENDING = "pending"    # sent to the payment provider, no result yet
        PAID = "paid"          # payment confirmed, credits added
        FAILED = "failed"      # declined, cancelled or errored; no credits

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchases")
    product_name_tr = models.CharField(max_length=40)               # copies at purchase time
    product_name_en = models.CharField(max_length=40)
    credits = models.PositiveIntegerField()
    list_price = models.DecimalField(max_digits=10, decimal_places=2)   # the product's regular price
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
        indexes = [models.Index(fields=["user", "created_at"], name="shop_purchase_user")]

    def __str__(self):
        return f"{self.user} {self.product_name_tr} {self.status}"
