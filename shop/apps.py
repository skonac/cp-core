from django.apps import AppConfig
from django.core import checks


class ShopConfig(AppConfig):
    name = "shop"

    def ready(self):
        checks.register(check_payment_provider)


PROVIDERS = ("simulated", "iyzico-sandbox", "iyzico-production")


def check_payment_provider(app_configs=None, **kwargs):
    """simulated and iyzico-sandbox hand out credits without real money: never with DEBUG off
    (= production). An iyzico provider needs its keys."""
    from django.conf import settings
    provider = settings.PAYMENT_PROVIDER
    if provider not in PROVIDERS:
        return [checks.Error(f"CP_PAYMENT_PROVIDER={provider!r}: one of {', '.join(PROVIDERS)}", id="cp.E004")]
    if provider in ("simulated", "iyzico-sandbox") and not settings.IS_DEV:
        return [checks.Error(f"CP_PAYMENT_PROVIDER={provider} while CP_DEBUG is off: purchases would be free",
                             id="cp.E005")]
    if provider.startswith("iyzico") and not (settings.IYZICO_API_KEY and settings.IYZICO_SECRET_KEY):
        prefix = "CP_IYZICOSANDBOX" if provider == "iyzico-sandbox" else "CP_IYZICOPRODUCTION"
        return [checks.Error(f"CP_PAYMENT_PROVIDER={provider} needs {prefix}_API_KEY and {prefix}_SECRET_KEY",
                             id="cp.E007")]
    return []
