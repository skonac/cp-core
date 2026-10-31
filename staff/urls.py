from django.urls import path

from printing import views as print_views

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
    path("messages/", views.messages_page, name="messages"),
    path("purchases/<int:purchase_id>/documents/", views.purchase_documents, name="purchase_documents"),
    path("print/", print_views.page, name="print"),
    path("print/<str:theme_id>/<str:fmt>.html", print_views.html, name="print_html"),
    path("print/<str:theme_id>/<str:fmt>.pdf", print_views.pdf, name="print_pdf"),
]
