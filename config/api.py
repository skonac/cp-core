"""The JSON API: one NinjaAPI object; each component adds its router here.

Every response uses cp-web's envelope: {success, code, message, data, requestId}
(see common/api.py). Interactive reference: /api/docs.
"""
from django.db import connection

from common.api import ok, make_api
from config import __version__

api = make_api(title="cp-core API", version=__version__)


@api.get("/health", auth=None, tags=["system"])
def health(request):
    """Liveness + database check, for monitoring and for the first deploy test."""
    with connection.cursor() as cur:
        cur.execute("SELECT current_database(), (SELECT extversion FROM pg_extension WHERE extname = 'vector')")
        database, pgvector = cur.fetchone()
    return ok(request, "ok", {"version": __version__, "database": database, "pgvector": pgvector})
