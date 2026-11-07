"""Root URL table: the table of contents. Each component plugs in its own routes."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from events.participant_views import home

from shop.views import iyzico_callback, iyzico_webhook

from printing.views import asset as print_asset

from accounts import views as account_views

from .api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/faceworker/", include("worker.urls")),   # before api/: plain views, worker's own JSON
    path("api/shop/iyzico/callback", iyzico_callback),  # before api/: iyzico's return (browser redirect)
    path("api/shop/iyzico/webhook", iyzico_webhook),    # before api/: iyzico's server notification (JSON)
    path("api/notify/", include("notify.urls")),        # before api/: SMS providers' delivery reports
    path("api/print/assets/<path:path>", print_asset),   # before api/: print theme files (fonts, images)
    path("api/", api.urls),                             # JSON API for cp-web; docs at /api/docs
    # Django pages (fallback UI and staff pages), all under /dj/: the rest of the site belongs to
    # cp-web (React), so nginx routes only /dj/ here. Login: phone or email + password, or an email link/code (accounts/views.py).
    path("dj/", include([
        path("", home, name="home"),
        path("login/", auth_views.LoginView.as_view(), name="login"),
        path("logout/", auth_views.LogoutView.as_view(), name="logout"),
        path("auth/email/", account_views.email_start, name="email_start"),
        path("auth/email/code/<str:challenge_id>/", account_views.email_code, name="email_code"),
        path("auth/email/<str:token>/", account_views.email_link, name="email_link"),
        path("auth/password/", account_views.password, name="password"),
        path("o/", include("events.urls")),             # organizer pages
        path("staff/", include("staff.urls")),          # staff data browser (read-only)
        path("", include("events.participant_urls")),   # participant pages: e/, me/, consent/
    ])),
    path("", RedirectView.as_view(url="/dj/")),         # only reachable through the SSH tunnel
]
