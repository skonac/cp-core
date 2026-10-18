from django.urls import path

from . import participant_views as v

app_name = "participant"

urlpatterns = [
    path("e/<str:code>/", v.event_page, name="event"),
    path("e/<str:code>/all/", v.event_page, {"view": "all"}, name="event_all"),
    path("e/<str:code>/photos/<int:seq>/", v.photo_page, name="photo"),
    path("e/<str:code>/photos/<int:seq>/download", v.photo_download, name="download"),
    path("e/<str:code>/photos/<int:seq>_<str:rendition>.jpg", v.photo_file, name="photo_file"),
    path("me/", v.my_events, name="my_events"),
    path("me/face/", v.my_face, name="my_face"),
    path("me/face/<int:image_id>_t.jpg", v.selfie_thumb, name="selfie_thumb"),
    path("consent/", v.consent, name="consent"),
]
