from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("jobs/next", views.next_job),
    path("jobs/<int:image_id>/result", views.result),
    path("images/<int:image_id>.jpg", views.image),
]
