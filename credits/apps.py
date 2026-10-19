from django.apps import AppConfig
from django.core import checks


class CreditsConfig(AppConfig):
    name = "credits"

    def ready(self):
        checks.register(check_payment_provider)


def check_payment_provider(app_configs=None, **kwargs):
    """Simulated payments hand out free credits: never with DEBUG off (= production)."""
    from django.conf import settings
    if settings.PAYMENT_PROVIDER not in ("simulated", "iyzico"):
        return [checks.Error(f"CP_PAYMENT_PROVIDER={settings.PAYMENT_PROVIDER!r}: simulated or iyzico", id="cp.E004")]
    if settings.PAYMENT_PROVIDER == "simulated" and not settings.IS_DEV:
        return [checks.Error("CP_PAYMENT_PROVIDER=simulated while CP_DEBUG is off: purchases would be free",
                             id="cp.E005")]
    return []
