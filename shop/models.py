"""Shop: Product (what we sell), Purchase (one buy of one product, with the payment provider's ids),
BillingProfile (who pays: what the payment provider and the invoice need), PromoCode (discount codes).
Paying for a product adds credits through credits.CreditTransaction (shop/payments.py). design/core.md."""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
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
    amount = models.DecimalField(max_digits=10, decimal_places=2)       # charged: list_price after the discount
    currency = models.CharField(max_length=3)
    # The discount in amount (copies at purchase time): which one, its percent (null for a fixed-amount
    # promo), and the promo code as entered (lowercase; not a FK, codes may be deleted later).
    discount_source = models.CharField(max_length=5, choices=[("none", "none"), ("user", "user"), ("promo", "promo")],
                                       default="none")
    discount_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    promo_code = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=32)                      # settings.PAYMENT_PROVIDER: simulated | iyzico-sandbox | iyzico-production
    conversation_id = models.CharField(max_length=64, unique=True)  # our id for this attempt, sent to the provider
    provider_token = models.CharField(max_length=200, blank=True)   # iyzico checkout form token
    provider_payment_id = models.CharField(max_length=64, blank=True)   # iyzico paymentId once paid
    provider_response = models.JSONField(null=True, blank=True)     # last raw answer from the provider
    billing = models.JSONField(null=True, blank=True)               # the billing profile at purchase time
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Sales documents (shop/documents.py): what the buyer accepted before paying, written once when the
    # purchase is created, never changed. docs_html = ÖBF-TR, MSS-TR, ÖBF-EN, MSS-EN in one page.
    docs_version = models.CharField(max_length=3, blank=True)       # e.g. "001"; empty on older purchases
    docs_html = models.TextField(blank=True)
    docs_sha256 = models.CharField(max_length=64, blank=True)       # of docs_html (UTF-8)
    docs_accepted_at = models.DateTimeField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"], name="shop_purchase_user")]

    @property
    def number(self) -> str:
        """The order number on the documents."""
        return f"CP-{self.pk:06d}"

    def __str__(self):
        return f"{self.user} {self.product_name_tr} {self.status}"


class BillingProfile(models.Model):
    """Who pays: an individual (TC kimlik no) or a company (unvan, vergi dairesi, VKN). Asked at the
    first purchase, editable in the profile. The payment provider needs a buyer name, identity number,
    email, phone (the account's) and an address; an invoice needs the company data. Checks:
    shop/billing.py."""
    class Type(models.TextChoices):
        INDIVIDUAL = "individual"
        COMPANY = "company"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="billing")
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.INDIVIDUAL)
    first_name = models.CharField(max_length=80)            # the buyer; for a company, the contact person
    last_name = models.CharField(max_length=80)
    identity_number = models.CharField(max_length=11, blank=True)   # TC kimlik no: required for individuals
    company_name = models.CharField(max_length=200, blank=True)   # unvan
    tax_office = models.CharField(max_length=80, blank=True)      # vergi dairesi
    tax_number = models.CharField(max_length=10, blank=True)      # VKN, 10 digits
    email = models.EmailField()
    address = models.CharField(max_length=300)
    district = models.CharField(max_length=80)              # ilçe
    city = models.CharField(max_length=80)                  # il
    postal_code = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=60, default="Türkiye")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_complete(self) -> bool:
        from .billing import billing_errors
        return not billing_errors(self)

    def __str__(self):
        return f"{self.user} {self.company_name or (self.first_name + ' ' + self.last_name)}"


class PromoCode(models.Model):
    """A discount code, defined in the admin. Signed-in organizers apply one to their session
    (POST /api/shop/promo); while now is between the dates it replaces the user's own discount on
    the products it covers. Stored and matched lowercase, shown uppercase. design/core.md."""
    class Type(models.TextChoices):
        PERCENT = "percent"    # value = whole percent, 1-100
        FIXED = "fixed"        # value = an amount off the price, in the product's currency (TRY)

    code = models.CharField(max_length=16, primary_key=True)
    type = models.CharField(max_length=7, choices=Type.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    # Product ids it covers; empty = every product. Plain ids, not a relation: a removed product just dangles.
    product_ids = ArrayField(models.PositiveIntegerField(), blank=True, default=list,
                             help_text="Product ids, comma-separated. Empty: every product.")
    note = models.CharField(max_length=200, blank=True)              # for us: what the code is for
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-valid_until"]
        constraints = [
            models.CheckConstraint(condition=models.Q(code=Lower("code")), name="promocode_lowercase"),
            models.CheckConstraint(condition=models.Q(valid_until__gt=models.F("valid_from")), name="promocode_dates"),
            models.CheckConstraint(condition=models.Q(value__gt=0), name="promocode_value_positive"),
            models.CheckConstraint(condition=~models.Q(type="percent") | models.Q(value__lte=100),
                                   name="promocode_percent_max"),
        ]

    def __str__(self):
        return self.code.upper()

    def clean(self):
        self.code = (self.code or "").strip().lower()
        errors = {}
        if self.value is not None:
            if self.value <= 0:
                errors["value"] = "Must be more than 0."
            elif self.type == self.Type.PERCENT and (self.value > 100 or self.value != self.value.to_integral_value()):
                errors["value"] = "A percent is a whole number from 1 to 100."
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            errors["valid_until"] = "Must be after valid from."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().lower()
        super().save(*args, **kwargs)

    def is_valid_at(self, when) -> bool:
        return self.valid_from <= when < self.valid_until

    def covers(self, product_id: int) -> bool:
        return not self.product_ids or product_id in self.product_ids
