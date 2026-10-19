from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        checks.register(check_kvkk_files)


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
            path = settings.KVKK_DIR / filename(loc)
            if not path.is_file():
                errors.append(checks.Error(f"KVKK document missing: {path}", id="cp.E002"))
    return errors
