from django.urls import path

from . import views

urlpatterns = [
    path("iletimerkezi/report", views.iletimerkezi_report),
]
