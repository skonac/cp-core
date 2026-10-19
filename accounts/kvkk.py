"""KVKK consent documents and the consent record (design/core.md, "Consent").

Documents are PDFs in cp-core/kvkk/, one pair per version: kvkk_tr_001.pdf + kvkk_en_001.pdf.
settings.KVKK_VERSION names the current pair. A published file is never edited: a change is a
new version, and changing the setting voids every user's consent (User.is_consent).
"""
import hashlib
import re
from functools import lru_cache

from django.conf import settings
from django.db import transaction

from .models import ConsentLog, User

FILENAME_RE = re.compile(r"^kvkk_(tr|en)_(\d{3})\.pdf$")


def filename(locale: str, version: str | None = None) -> str:
    return f"kvkk_{locale}_{version or settings.KVKK_VERSION}.pdf"


def url(locale: str, version: str | None = None) -> str:
    return f"/api/kvkk/{filename(locale, version)}"


def documents() -> dict:
    """What /api/me shows: the current version and its files, one per language."""
    return {"version": settings.KVKK_VERSION, "urls": {loc: url(loc) for loc in settings.KVKK_LOCALES}}


@lru_cache(maxsize=32)
def sha256(name: str) -> str:
    # Cached per file name: published files never change.
    return hashlib.sha256((settings.KVKK_DIR / name).read_bytes()).hexdigest()


def record_consent(user: User, locale: str, ip: str | None, user_agent: str) -> bool:
    """Mark consent to the current version and log it. Returns False (and writes nothing) when
    the user already has consent to the current version."""
    if user.is_consent:
        return False
    version = settings.KVKK_VERSION
    with transaction.atomic():
        user.kvkk_version = version
        user.save(update_fields=["kvkk_version"])
        ConsentLog.objects.create(user=user, phone=user.phone, full_name=user.full_name, version=version,
                                  locale=locale, sha256=sha256(filename(locale, version)),
                                  ip=ip or None, user_agent=(user_agent or "")[:500])
    return True
