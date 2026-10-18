from django.urls import path

from . import views

app_name = "organizer"

urlpatterns = [
    path("", views.home, name="home"),
    path("events/new", views.event_new, name="event_new"),
    path("events/<str:code>/", views.event_detail, name="event"),
    path("events/<str:code>/upload", views.upload, name="upload"),
    path("events/<str:code>/status.json", views.status_json, name="status"),
    path("events/<str:code>/photos/<int:seq>/", views.photo_detail, name="photo"),
    path("events/<str:code>/photos/<int:seq>_<str:rendition>.jpg", views.photo_file, name="photo_file"),
]
