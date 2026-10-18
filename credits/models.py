"""CreditTransaction: purchases and admin adjustments. The balance itself is User.credit_balance;
photo uploads are not logged here (every photo row cost 1 credit). design/core.md."""
from django.conf import settings
from django.db import models
from django.utils import timezone


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
