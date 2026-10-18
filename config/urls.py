"""Root URL table: the table of contents. Each component plugs in its own routes."""
from django.contrib import admin
from django.urls import path

from .api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),   # JSON API; interactive docs at /api/docs
]
