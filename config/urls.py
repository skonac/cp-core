"""Root URL table: the table of contents. Each component plugs in its own routes."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from events.participant_views import home

from .api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/faceworker/", include("worker.urls")),   # before api/: plain views, worker's own JSON
    path("api/", api.urls),                             # JSON API for cp-web; docs at /api/docs
    # Django pages (fallback UI). Login is phone + password until SMS sign-in exists.
    path("", home, name="home"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("o/", include("events.urls")),                 # organizer pages
    path("", include("events.participant_urls")),       # participant pages: /e/, /me/, /consent/
]
