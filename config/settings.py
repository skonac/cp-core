"""cp-core settings. Read once at startup.

Machine-specific values (secrets, database, paths) come from CP_* environment variables,
optionally loaded from a .env file (dev) or the file CP_ENV_FILE points to (production).
Product constants (sizes, limits, thresholds) live at the bottom of this file; see design/core.md.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal KEY=value reader. Existing environment variables are not overwritten."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file(Path(os.environ.get("CP_ENV_FILE", BASE_DIR / ".env")))


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing setting {name} (environment or .env file)")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# --- Django core ---------------------------------------------------------------

SECRET_KEY = env("CP_SECRET_KEY")
DEBUG = env_bool("CP_DEBUG")
IS_DEV = DEBUG   # CP_DEBUG as configured; the test runner turns DEBUG off, this stays (dev-only guards use it)
ALLOWED_HOSTS = [h.strip() for h in env("CP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",   # Postgres-only fields and indexes (ArrayField, partial indexes)
    # Our components (design/core.md, "Data model")
    "common",     # API envelope, request ids: no models
    "accounts",   # User, OtpChallenge
    "events",     # Event, EventMember
    "media",      # Image (also the worker queue), Face, DownloadLog
    "credits",    # CreditTransaction
    "worker",     # face worker API, Worker (monitoring)
    "shop",       # Product, Purchase, payments (simulated until iyzico)
    "staff",      # staff data browser pages (/dj/staff/): no models
]

AUTH_USER_MODEL = "accounts.User"   # must exist before the first migrate

MIDDLEWARE = [
    "common.middleware.RequestIdMiddleware",   # first: every response gets X-Request-ID
    "common.middleware.RealIpMiddleware",      # client IP from nginx (rate limits, logs)
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Behind nginx: it terminates HTTPS and passes the scheme and the client IP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")   # nginx: proxy_set_header X-Forwarded-Proto $scheme
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("CP_CSRF_TRUSTED_ORIGINS",
                        "https://cleverphotos.org,https://www.cleverphotos.org").split(",") if o.strip()]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("CP_DB_NAME", "cpcore"),
        "USER": env("CP_DB_USER", ""),
        "PASSWORD": env("CP_DB_PASSWORD", ""),
        "HOST": env("CP_DB_HOST", ""),
        "PORT": env("CP_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,   # reuse connections for a minute instead of one per request
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True   # timestamps stored in UTC (timestamptz)

STATIC_URL = "static/"

# Django pages: where login sends people (URL names from config/urls.py)
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",   # phone + the account's own password
    "accounts.backends.MasterPassBackend",         # phone + CP_MASTER_PASS (non-production)
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"          # "/" sends organizers and participants to their own pages
LOGOUT_REDIRECT_URL = "login"
STATIC_ROOT = BASE_DIR / "static_root"   # collectstatic target, served by nginx later

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# --- Files ----------------------------------------------------------------------

MEDIA_ROOT = Path(env("CP_MEDIA_ROOT", str(BASE_DIR / "media_root")))
USE_X_ACCEL = env_bool("CP_X_ACCEL")         # production: nginx serves files (X-Accel-Redirect)
X_ACCEL_PREFIX = "/protected-media/"         # nginx internal location aliased to MEDIA_ROOT
UPLOAD_MAX_BYTES = 50 * 1024 * 1024          # per file

# --- Product settings (design/core.md, "Settings") --------------------------------

MASTER_SIDE = 4000          # px, long edge; downloads and prints
MASTER_QUALITY = 90
DISPLAY_SIDE = 1600         # browsing, and the image the face worker receives
DISPLAY_QUALITY = 85
THUMB_SIDE = 400
THUMB_QUALITY = 80

CREDIT_FLOOR_DEFAULT = -50  # a user's balance may go down to this; per-user override: User.credit_floor
MATCH_THRESHOLD = 0.363     # SFace cosine similarity; tune on a real event
DOWNLOAD_CAP_PER_PHOTO_PER_DAY = 3
DOWNLOAD_NAME = "cleverphotos_{code}_{seq:05d}.jpg"
ZIP_MAX_FILES = int(env("CP_ZIP_MAX_FILES", "50"))   # photos per ZIP download request
ZIP_NAME = "cleverphotos_{code}_{view}.zip"

EVENT_CODE_LENGTH = 5
EVENT_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"   # 31 symbols: no 0/1, no i/l/o

OTP_TTL = 120               # seconds
OTP_MAX_PER_15MIN = 3       # codes per phone
OTP_MAX_ATTEMPTS = 5        # wrong codes per challenge
OTP_RESEND_AFTER = 30       # seconds before another code may be requested
OTP_DELIVERY = env("CP_OTP_DELIVERY", "log")   # "log": code goes to the log + OtpChallenge.dev_code, no SMS
EVENT_LOOKUP_PER_MIN = 30   # public event lookups per IP
# Non-production only: this string is accepted as the code of any OTP challenge and as the password
# of any active account (accounts.backends.MasterPassBackend). Empty = off. `manage.py check` (and so
# deploy.sh and runserver) refuses to run with it set while DEBUG is off.
MASTER_PASS = env("CP_MASTER_PASS", "")
# Credit purchases (credits/payments.py): "simulated" = every purchase succeeds at once, no money
# (non-production only; `manage.py check` refuses it with DEBUG off); "iyzico" once connected.
PAYMENT_PROVIDER = env("CP_PAYMENT_PROVIDER", "simulated")

# KVKK consent document (design/core.md, "Consent"). The pair kvkk/kvkk_tr_<v>.pdf + kvkk_en_<v>.pdf;
# a user has consent when User.kvkk_version equals this. Changing it = everyone consents again.
KVKK_VERSION = env("CP_KVKK_VERSION", "001")
KVKK_DIR = BASE_DIR / "kvkk"
KVKK_LOCALES = ("tr", "en")

WORKER_TOKEN = env("CP_WORKER_TOKEN", "")
WORKER_LEASE = 120          # seconds a claimed job belongs to its worker
WORKER_MAX_ATTEMPTS = 3
WORKER_POLL_MAX = 60        # longest long-poll wait we honour
