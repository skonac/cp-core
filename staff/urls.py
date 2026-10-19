from django.urls import path

from . import views

app_name = "staff"

urlpatterns = [
    path("", views.index, name="index"),
    path("events/", views.events, name="events"),
    path("events/<str:code>/", views.event, name="event"),
    path("images/<int:image_id>/", views.image, name="image"),
    path("images/<int:image_id>/<str:rendition>.jpg", views.image_file, name="image_file"),
    path("users/", views.users, name="users"),
    path("users/<int:user_id>/", views.user, name="user"),
    path("worker/", views.worker, name="worker"),
]
