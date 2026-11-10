from django.conf import settings
from django.db import models


class OperationMode(models.Model):
    """One row per change of the site's operation mode (common/mode.py). The current mode is the
    newest row; no rows = normal. Rows are never edited or deleted: they are the history."""

    class Mode(models.TextChoices):
        NORMAL = "normal", "Normal"
        RESTRICTED = "restricted", "Restricted"
        LOCKDOWN = "lockdown", "Lockdown"

    mode = models.CharField(max_length=12, choices=Mode.choices)
    reason = models.TextField(blank=True)   # internal (staff page, admin alert); never shown to users
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")   # null: changed with the management command
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.mode} at {self.changed_at:%Y-%m-%d %H:%M}"
