"""CreditTransaction: purchases and admin adjustments. The balance itself is User.credit_balance;
photo uploads are not logged here (every photo row cost 1 credit). design/core.md."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class CreditPackage(models.Model):
    """A credit package for sale ("1000cr": 1000 credits for a price). Edited in the admin only.
    Change a price by editing the row: the store's pricingVersion (a hash of the active packages)
    changes with it, so a purchase started at the old price is refused."""
    name = models.CharField(max_length=40)                          # "1000cr"; shown to organizers
    credits = models.PositiveIntegerField()                         # credits the package adds
    price = models.DecimalField(max_digits=10, decimal_places=2)    # what the organizer pays
    currency = models.CharField(max_length=3, default="TRY")
    is_active = models.BooleanField(default=True)                   # False: hidden from the store
    is_featured = models.BooleanField(default=False)                # highlighted in the store
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sort_order", "credits"]

    def __str__(self):
        return f"{self.name} ({self.credits} cr, {self.price} {self.currency})"


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
