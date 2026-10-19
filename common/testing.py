"""Shared test helpers: generated photos, fake face vectors, fake worker results, an API client.

No face worker in tests: a "person" is a random unit vector; a face of that person in a photo
is the same vector plus a little noise (similarity ~0.9, well above MATCH_THRESHOLD), and
random other vectors are strangers (similarity ~0). Results go through worker.queue.store_result,
the same code path as the real worker's POST.
"""
import io
import math
import random
import shutil
import tempfile
from datetime import date

from django.test import Client, override_settings
from django.urls import resolve
from PIL import Image as PILImage

from events.models import Event
from media.ingest import ingest_photo, ingest_selfie
from worker.queue import store_result

ENGINE = "test-engine/1400/0.8"


def jpeg(color=(120, 90, 60), size=(800, 600), seed: int | None = None) -> bytes:
    """A small JPEG. Different seeds give different bytes (the duplicate check uses sha256)."""
    im = PILImage.new("RGB", size, color)
    if seed is not None:
        rnd = random.Random(seed)
        for _ in range(20):
            x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
            im.putpixel((x, y), (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def person(seed: int) -> list[float]:
    rnd = random.Random(seed)
    return _unit([rnd.gauss(0, 1) for _ in range(128)])


def face_of(identity: list[float], seed: int = 0, noise: float = 0.05) -> list[float]:
    rnd = random.Random(seed)
    return _unit([x + rnd.gauss(0, noise) for x in identity])


def face(embedding: list[float]) -> dict:
    return {"bbox": [0.1, 0.1, 0.3, 0.4], "score": 0.95,
            "landmarks": [[0.15, 0.2], [0.25, 0.2], [0.2, 0.25], [0.16, 0.32], [0.24, 0.32]],
            "embedding": embedding}


def process(image, embeddings: list[list[float]], faces_found: int | None = None) -> None:
    """Store a worker result for an image, as if the worker had found these faces."""
    body = {"engine": ENGINE, "faces": [face(e) for e in embeddings]}
    if image.is_selfie:
        body["faces_found"] = len(embeddings) if faces_found is None else faces_found
    status, message = store_result(image.pk, body)
    assert status == 200, message


def make_event(owner, **fields) -> Event:
    fields = {"name": "Test event", "date": date(2026, 6, 12), "owner": owner, **fields}
    return Event.objects.create(**fields)


def add_photo(event, seed: int, embeddings: list[list[float]] | None = None):
    """Upload a photo (charges the owner 1 credit); with embeddings, also process it."""
    image = ingest_photo(event, event.owner, jpeg(seed=seed))
    if embeddings is not None:
        process(image, embeddings)
        image.refresh_from_db()
    return image


def add_selfie(user, identity: list[float] | None, slot="primary", seed: int = 1000):
    """Upload a selfie (user needs consent); with an identity, process it as one face of that person."""
    image = ingest_selfie(user, jpeg(seed=seed), slot)
    if identity is not None:
        process(image, [face_of(identity, seed=seed)])
        image.refresh_from_db()
    return image


class TempMedia:
    """Mixin for TestCase: MEDIA_ROOT in a temporary folder, removed afterwards."""

    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp(prefix="cp-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)


class ApiClient(Client):
    """Test client for the JSON API: CSRF enforced like a browser, JSON bodies, and every JSON
    response checked against the response schema its endpoint declares (the docs at /api/docs).
    A status the endpoint does not declare, or a field the docs don't list, fails the test."""

    def __init__(self, **kwargs):
        super().__init__(enforce_csrf_checks=True, HTTP_USER_AGENT="TestBrowser/1.0", **kwargs)
        self.checked: set[tuple[str, str, int]] = set()   # (method, route, status) seen

    def csrf_headers(self) -> dict:
        token = self.cookies["csrftoken"].value if "csrftoken" in self.cookies else ""
        return {"HTTP_X_CSRFTOKEN": token}

    def request(self, **req):
        response = super().request(**req)
        if req["PATH_INFO"].startswith("/api/") and not req["PATH_INFO"].startswith("/api/faceworker/"):
            self._check(req["REQUEST_METHOD"], req["PATH_INFO"], response)
        return response

    def _check(self, method: str, path: str, response):
        match = resolve(path)
        view = next(c.cell_contents for c in match.func.__closure__ if type(c.cell_contents).__name__ == "PathView")
        operation = next(op for op in view.operations if method in op.methods)
        status = response.status_code
        route = match.route
        if response.get("Content-Type", "").startswith("application/json"):
            models = operation.response_models
            assert status in models, f"{method} {path}: status {status} not declared (declared: {sorted(models)})"
            if models[status] is not None:
                models[status].model_validate({"response": response.json()})
        elif status == 204:
            assert operation.response_models.get(204, "missing") is None, f"{method} {path}: 204 not declared"
        else:   # a file
            documented = (operation.openapi_extra or {}).get("responses", {}).get(status, {}).get("content", {})
            assert response["Content-Type"] in documented, \
                f"{method} {path}: {status} {response['Content-Type']} not documented"
        self.checked.add((method, route, status))

    # JSON helpers; unsafe methods send the CSRF header like cp-web's fetch helper
    def get_json(self, path, **params):
        return self.get(path, params)

    def post_json(self, path, body=None):
        return self.post(path, body or {}, content_type="application/json", **self.csrf_headers())

    def patch_json(self, path, body):
        return self.patch(path, body, content_type="application/json", **self.csrf_headers())

    def delete_(self, path):
        return self.delete(path, **self.csrf_headers())

    def sign_in(self, phone: str, event_code: str | None = None, role: str | None = None):
        """OTP sign-in through the API (code read from the challenge row: OTP_DELIVERY=log)."""
        from accounts.models import OtpChallenge
        self.get("/api/auth/csrf")
        body = {"phoneNumber": phone}
        if event_code:
            body["eventCode"] = event_code
        if role:
            body["role"] = role
        r = self.post_json("/api/auth/otp-requests", body)
        assert r.status_code == 201, r.content
        ch = OtpChallenge.objects.get(pk=r.json()["data"]["challengeId"])
        r = self.post_json("/api/auth/otp-verifications", {"challengeId": ch.pk, "code": ch.dev_code})
        assert r.status_code == 200, r.content
        return r.json()["data"]
