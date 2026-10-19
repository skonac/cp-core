"""The JSON API for cp-web: one NinjaAPI object; each component adds its router here.

Every response uses cp-web's envelope {success, code, message, data, requestId} (common/api.py).
Reference for the frontend: docs/api.md. Interactive: /api/docs, machine-readable: /api/openapi.json.
"""
from django.db import connection

from accounts.api import auth_router, kvkk_router, me_router
from common.api import Out, envelope, make_api, respond
from config import __version__
from events.api import events_router, public_router

api = make_api(title="cp-core API", version=__version__,
               description="Cleverphotos core API for cp-web. Session cookie + X-CSRFToken. See docs/api.md.")

api.add_router("/auth", auth_router)
api.add_router("/me", me_router)
api.add_router("/kvkk", kvkk_router)
api.add_router("/public", public_router)
api.add_router("/events", events_router)


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
