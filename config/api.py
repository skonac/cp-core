"""The JSON API for cp-web: one NinjaAPI object; each component adds its router here.

Every response uses cp-web's envelope {success, code, message, data, requestId} (common/api.py).
Reference for the frontend: docs/api.md. Interactive: /api/docs, machine-readable: /api/openapi.json.
"""
from typing import Literal

from django.db import connection
from pydantic import Field

from accounts.api import auth_router, contact_router, kvkk_router, me_router
from common.api import Out, envelope, make_api, respond
from config import __version__
from events.api import events_router, public_router
from events.organizer_api import organizer_router
from events.uploader_api import uploader_router
from shop.api import billing_router, shop_router

api = make_api(title="cp-core API", version=__version__,
               description="Cleverphotos core API for cp-web. Session cookie + X-CSRFToken. See docs/api.md.")

api.add_router("/auth", auth_router)
api.add_router("/me", me_router)
api.add_router("/kvkk", kvkk_router)
api.add_router("/contact", contact_router)
api.add_router("/public", public_router)
api.add_router("/events", events_router)
api.add_router("/organizer", organizer_router)
api.add_router("/uploader", uploader_router)
api.add_router("/shop", shop_router)
api.add_router("/me/billing", billing_router)


class HealthOut(Out):
    version: str
    database: str
    pgvector: str | None


@api.get("/health", auth=None, tags=["system"], response=envelope("HealthResponse", HealthOut, "ok"))
def health(request):
    """Liveness + database check."""
    with connection.cursor() as cur:
        cur.execute("SELECT current_database(), (SELECT extversion FROM pg_extension WHERE extname = 'vector')")
        database, pgvector = cur.fetchone()
    return respond(request, "ok", {"version": __version__, "database": database, "pgvector": pgvector})


class OperationModeOut(Out):
    mode: Literal["normal", "restricted", "lockdown"] = Field(
        description="normal: everything works · restricted: payments are off (starting a purchase answers 503 "
                    "payments_disabled) · lockdown: only staff can use the site (everything else answers 503 "
                    "site_locked). Staff are never blocked.", examples=["normal"])


@api.get("/operation-mode", auth=None, tags=["system"],
         response=envelope("OperationModeResponse", OperationModeOut, "operation_mode_retrieved"))
def operation_mode(request):
    """The site's operation mode, set by staff during an incident. Open in every mode and without sign-in:
    read it at app start (and when an API call answers 503 site_locked or payments_disabled) and show the
    state: restricted -> disable the buy buttons with a notice; lockdown -> a "temporarily closed" screen."""
    from common.mode import current
    return respond(request, "operation_mode_retrieved", {"mode": current()})
