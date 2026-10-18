"""Worker: monitoring only (last poll, version). Authentication is the shared token. design/core.md."""
from django.db import models
from django.utils import timezone


class Worker(models.Model):
    worker_id = models.CharField(max_length=64, unique=True)   # X-Worker-Id
    version = models.CharField(max_length=100, blank=True)     # from User-Agent
    ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.worker_id
