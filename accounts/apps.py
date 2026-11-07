from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        checks.register(check_kvkk_files)
        checks.register(check_master_pass)
        checks.register(check_turnstile)


def check_turnstile(app_configs=None, **kwargs):
    """Guest access in production with Cloudflare's always-pass test key: works, but protects nothing."""
    from django.conf import settings
    if settings.GUEST_ACCESS and not settings.IS_DEV and settings.TURNSTILE_SECRET == settings.TURNSTILE_TEST_SECRET:
        return [checks.Warning("CP_GUEST_ACCESS is on with Cloudflare's test Turnstile key; set CP_TURNSTILE_SITE_KEY "
                               "and CP_TURNSTILE_SECRET", id="cp.W020")]
    if (settings.TURNSTILE_SECRET == settings.TURNSTILE_TEST_SECRET) != (settings.TURNSTILE_SITE_KEY == settings.TURNSTILE_TEST_SITE_KEY):
        return [checks.Error("Set both CP_TURNSTILE_SITE_KEY and CP_TURNSTILE_SECRET (or neither)", id="cp.E021")]
    return []


def check_master_pass(app_configs=None, **kwargs):
    """CP_MASTER_PASS opens every account: never with DEBUG off (= production)."""
    from django.conf import settings
    if settings.MASTER_PASS and not settings.IS_DEV:
        return [checks.Error("CP_MASTER_PASS is set while CP_DEBUG is off; remove it in production",
                             id="cp.E003")]
    return []


def check_kvkk_files(app_configs=None, **kwargs):
    """manage.py check (run by deploy.sh) fails when the current KVKK documents are missing."""
    from django.conf import settings

    from .kvkk import filename
    errors = []
    if not settings.KVKK_VERSION.isdigit() or len(settings.KVKK_VERSION) != 3:
        errors.append(checks.Error(f"CP_KVKK_VERSION={settings.KVKK_VERSION!r}: expected 3 digits, e.g. 001",
                                   id="cp.E001"))
    else:
        for loc in settings.KVKK_LOCALES:
            path = settings.LEGAL_DIR / filename(loc)
            if not path.is_file():
                errors.append(checks.Error(f"KVKK document missing: {path}", id="cp.E002"))
    return errors
