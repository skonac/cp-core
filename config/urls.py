"""Root URL table: the table of contents. Each component plugs in its own routes."""
from django.contrib import admin
from django.urls import include, path

from .api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/faceworker/", include("worker.urls")),   # before api/: plain views, worker's own JSON
    path("api/", api.urls),                             # JSON API for cp-web; docs at /api/docs
]
