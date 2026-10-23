"""PushDevice (where a user's pushes go) and Notification (a temporary log of what was sent). design/notifications.md."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class PushDevice(models.Model):
    """One browser or app that accepted pushes. Dropped when its push service answers "gone" (404/410)."""

    class Kind(models.TextChoices):
        WEBPUSH = "webpush"   # browsers, also iOS Home Screen web apps (Apple's service behind the Web Push protocol)
        APNS = "apns"         # a native iOS app, later

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    endpoint = models.TextField(unique=True)               # Web Push endpoint URL, or the APNs device token
    keys = models.JSONField(default=dict, blank=True)      # Web Push: {"p256dh": ..., "auth": ...}
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)   # consecutive

    def __str__(self):
        return f"{self.kind} {self.pk} of {self.user_id}"


class Notification(models.Model):
    """One push to one device, or one SMS/email to one address. A temporary log: deleted with its
    event and after NOTIFY_KEEP_DAYS. OTP codes are never stored here (accounts.OtpChallenge)."""

    class Channel(models.TextChoices):
        PUSH = "push"
        SMS = "sms"
        EMAIL = "email"

    class Status(models.TextChoices):
        PENDING = "pending"   # waiting for the sender (or for a retry)
        SENDING = "sending"   # claimed by a sender thread
        SENT = "sent"         # accepted by the provider
        FAILED = "failed"     # given up

    kind = models.CharField(max_length=32)                  # new_matches, event_end, test, ...
    channel = models.CharField(max_length=8, choices=Channel.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="notifications")
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.CASCADE,
                              related_name="notifications")
    device = models.ForeignKey(PushDevice, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="notifications")
    to = models.CharField(max_length=255)                   # phone (E.164), email address, or "device <id>"
    title = models.CharField(max_length=150, blank=True)    # push title, email subject
    text = models.TextField()
    url = models.CharField(max_length=500, blank=True)      # where a tap on the push goes
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=32, blank=True)
    provider_ref = models.CharField(max_length=100, blank=True)   # the provider's message/order id
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivery = models.CharField(max_length=12, blank=True)   # SMS delivery report: accepted, delivered, undelivered
    delivery_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"], name="notification_created"),
            models.Index(fields=["created_at"], condition=models.Q(status__in=["pending", "sending"]),
                         name="notification_unsent"),
        ]

    def __str__(self):
        return f"{self.channel} {self.kind} to {self.to} ({self.status})"
