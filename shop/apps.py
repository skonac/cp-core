from django.apps import AppConfig
from django.core import checks


class ShopConfig(AppConfig):
    name = "shop"

    def ready(self):
        checks.register(check_payment_provider)
        checks.register(check_sales_documents)
        checks.register(check_global_discount)


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


def check_global_discount(app_configs=None, **kwargs):
    """CP_DISCOUNT_VALUE: a whole percent 0-100 (a typo fails at deploy, not on the shop page)."""
    from .pricing import global_percent
    try:
        global_percent(strict=True)
    except ValueError as e:
        return [checks.Error(str(e), id="cp.E010")]
    return []


def check_sales_documents(app_configs=None, **kwargs):
    """manage.py check (run by deploy.sh) fails when the current sales document templates are missing."""
    from django.conf import settings

    from .documents import missing_templates
    version = settings.SALES_DOCS_VERSION
    if not version.isdigit() or len(version) != 3:
        return [checks.Error(f"CP_SALES_DOCS_VERSION={version!r}: expected 3 digits, e.g. 001", id="cp.E008")]
    return [checks.Error(f"Sales document template missing: {path}", id="cp.E009") for path in missing_templates(version)]
