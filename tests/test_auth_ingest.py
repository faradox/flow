import hashlib
import io

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("frontmatter")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app import auth  # noqa: E402
from app.main import app  # noqa: E402
from app.settings import settings  # noqa: E402

PROXY = {"X-Proxy-Secret": "proxy-s3cret", "Remote-User": "alice"}
TOKEN = "ingest-token-for-tests"


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "proxy_secret", "proxy-s3cret")
    monkeypatch.setattr(settings, "allowed_user", "alice")
    monkeypatch.setattr(settings, "session_secret", "s" * 40)


@pytest.fixture
def ingest_token(monkeypatch):
    monkeypatch.setattr(settings, "ingest_token_sha256", hashlib.sha256(TOKEN.encode()).hexdigest())


def make_request(headers=None, cookies=None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


# --- app/auth.py ---------------------------------------------------------


def test_dev_mode_allows_everything():
    assert not auth.is_live_mode()
    assert auth.is_authenticated(make_request())
    assert auth.require_editor(make_request()) == "dev"


def test_proxy_user_needs_secret_and_allowed_user(live):
    assert auth.proxy_user(make_request(PROXY)) == "alice"
    assert auth.proxy_user(make_request({**PROXY, "X-Proxy-Secret": "nope"})) is None
    assert auth.proxy_user(make_request({**PROXY, "Remote-User": "mallory"})) is None
    assert auth.proxy_user(make_request({"Remote-User": "alice"})) is None


def test_editor_token_roundtrip_expiry_and_tamper(live):
    token = auth.create_editor_token(now=1000)
    assert auth.verify_editor_token(token, now=1001)
    assert not auth.verify_editor_token(token, now=1000 + 8 * 86400)
    expires, signature = token.split(".")
    assert not auth.verify_editor_token(f"{int(expires) + 1}.{signature}", now=1001)
    assert not auth.verify_editor_token("garbage", now=1001)


def test_cookie_shows_editor_but_does_not_allow_writes(live):
    request = make_request(cookies={auth.EDITOR_COOKIE: auth.create_editor_token()})
    assert auth.is_authenticated(request)
    with pytest.raises(Exception) as excinfo:
        auth.require_editor(request)
    assert excinfo.value.status_code == 401


def test_safe_redirect_target():
    assert auth.safe_redirect_target("/p/x/") == "/p/x/"
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil"):
        assert auth.safe_redirect_target(bad) == "/"


def test_live_config_check(monkeypatch, live):
    auth.check_live_config()
    monkeypatch.setattr(settings, "session_secret", auth.DEFAULT_SESSION_SECRET)
    with pytest.raises(RuntimeError):
        auth.check_live_config()


@pytest.fixture
def password_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "password")
    monkeypatch.setattr(settings, "admin_password", "hunter2-but-longer")
    monkeypatch.setattr(settings, "session_secret", "p" * 40)


@pytest.mark.parametrize(
    "mode, proxy_secret, admin_password, expected",
    [
        ("auto", "", "", "none"),
        ("auto", "", "pw", "password"),
        ("auto", "s", "pw", "proxy"),
        ("", "s", "", "proxy"),
        ("authelia", "s", "", "proxy"),
        ("NONE", "s", "pw", "none"),
        ("password", "s", "pw", "password"),
    ],
)
def test_auth_mode_resolution(monkeypatch, mode, proxy_secret, admin_password, expected):
    monkeypatch.setattr(settings, "auth_mode", mode)
    monkeypatch.setattr(settings, "proxy_secret", proxy_secret)
    monkeypatch.setattr(settings, "admin_password", admin_password)
    assert auth.auth_mode() == expected


def test_unknown_auth_mode_refuses_to_start(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "basic")
    with pytest.raises(RuntimeError):
        auth.check_live_config()


def test_explicit_mode_needs_its_settings(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "s" * 40)
    monkeypatch.setattr(settings, "auth_mode", "password")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        auth.check_live_config()
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    with pytest.raises(RuntimeError, match="PROXY_SECRET"):
        auth.check_live_config()


def test_password_mode_ignores_proxy_headers(monkeypatch, password_mode):
    monkeypatch.setattr(settings, "proxy_secret", "proxy-s3cret")
    monkeypatch.setattr(settings, "allowed_user", "alice")
    auth.check_live_config()
    assert auth.proxy_user(make_request(PROXY)) is None
    assert not auth.is_authenticated(make_request(PROXY))


def test_password_change_invalidates_cookie(monkeypatch, password_mode):
    token = auth.create_editor_token()
    assert auth.verify_editor_token(token)
    monkeypatch.setattr(settings, "admin_password", "a-new-password")
    assert not auth.verify_editor_token(token)


# --- editor routes -------------------------------------------------------

POST_TEXT = '---\ntitle: "Hello"\ndate: 2026-11-08T10:00:00\n---\nHello there'


def test_login_sets_cookie_from_proxy_headers(client, live):
    assert client.get("/login").status_code == 401
    response = client.get("/login", params={"next": "//evil.example"}, headers=PROXY)
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    set_cookie = response.headers["set-cookie"]
    assert f"{auth.EDITOR_COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie


def test_post_requires_proxy_headers(client, live):
    data = {"path": "2026-11-08-hello", "text": POST_TEXT}
    assert client.post("/post", data=data).status_code == 401
    cookie = {auth.EDITOR_COOKIE: auth.create_editor_token()}
    assert client.post("/post", data=data, cookies=cookie).status_code == 401
    assert client.post("/post", data=data, headers=PROXY).status_code == 200


def test_post_refuses_cross_origin(client, live):
    data = {"path": "2026-11-08-cross", "text": POST_TEXT}
    headers = {**PROXY, "Origin": "https://other.example.com"}
    assert client.post("/post", data=data, headers=headers).status_code == 403
    headers = {**PROXY, "Origin": "http://testserver"}
    assert client.post("/post", data=data, headers=headers).status_code == 200


@pytest.mark.parametrize("path", ["../escape", "sub/../../escape", ".hidden", "/abs"])
def test_post_and_delete_refuse_path_traversal(client, live, path):
    assert client.post("/post", data={"path": path, "text": POST_TEXT}, headers=PROXY).status_code == 400
    assert client.post("/delete", data={"path": path}, headers=PROXY).status_code == 400


def test_upload_strips_metadata(client, live, jpeg):
    files = {"file": ("photo.jpg", jpeg(marker="UPLOAD-MARKER"), "image/jpeg")}
    response = client.post("/upload", files=files, headers=PROXY)
    assert response.status_code == 200
    url = response.json()["files"][0]["url"]
    stored = (settings.local_upload_path / url.rsplit("/", 1)[1]).read_bytes()
    assert b"UPLOAD-MARKER" not in stored


def test_upload_rejects_non_images(client, live):
    files = {"file": ("photo.jpg", b"not an image", "image/jpeg")}
    assert client.post("/upload", files=files, headers=PROXY).status_code == 400


def test_agent_upload_needs_login(client, live):
    response = client.post("/analytics/api/agent/upload-image", json={"url": "http://127.0.0.1/"})
    assert response.status_code == 401
    assert response.json()["success"] is False


# --- app/ingest.py -------------------------------------------------------


def ingest(client, image, token=TOKEN, **data):
    return client.post(
        "/api/ingest/photo",
        files={"image": ("IMG_0001.jpg", image, "image/jpeg")},
        data=data,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


def test_ingest_disabled_without_configured_token(client, jpeg):
    response = ingest(client, jpeg())
    assert response.status_code == 503
    assert response.json() == {"error": "Ingest is not configured"}


def test_ingest_rejects_missing_or_wrong_token(client, ingest_token, jpeg):
    for token in (None, "wrong"):
        response = ingest(client, jpeg(), token=token)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_ingest_ignores_proxy_headers(client, live, ingest_token, jpeg):
    response = client.post(
        "/api/ingest/photo",
        files={"image": ("a.jpg", jpeg(), "image/jpeg")},
        headers=PROXY,
    )
    assert response.status_code == 401


def test_ingest_creates_private_post(client, live, ingest_token, jpeg):
    image = jpeg(size=(300, 200), color=(10, 120, 40), orientation=6, marker="INGEST-MARKER")
    response = ingest(
        client,
        image,
        title='My "Garden" \\ today',
        tags=["Garden", "grün Beet, garden", "bad<tag>"],
        body="First line\nsecond line\n\n<b>bold?</b>",
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    digest = hashlib.sha256(image).hexdigest()
    assert payload["result"] == "created"
    assert payload["status"] == "private"
    assert payload["path"].endswith(f"-my-garden-today-{digest[:8]}")
    assert payload["url"] == f"https://blog.example/p/{payload['path']}/"

    page = app.state.content_manager.get_page(payload["path"])
    assert page.metadata.title == "My Garden today"
    assert page.metadata.tags == ["garden", "grün-beet", "badtag"]
    assert page.metadata.status == "private"
    assert page.metadata.source_sha256 == f"sha256:{digest}"
    assert "<p>First line<br>second line</p>" in page.content
    assert "&lt;b&gt;bold?&lt;/b&gt;" in page.content
    assert page not in app.state.content_manager.get_pages_by_status("public")

    stored = settings.local_upload_path / f"{payload['path']}.webp"
    data = stored.read_bytes()
    assert b"INGEST-MARKER" not in data
    assert Image.open(io.BytesIO(data)).size == (200, 300)


def test_ingest_same_image_twice_returns_existing(client, ingest_token, jpeg):
    image = jpeg(color=(1, 2, 3))
    first = ingest(client, image, title="Once")
    second = ingest(client, image, title="Twice", status="public")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["result"] == "exists"
    assert second.json()["path"] == first.json()["path"]
    assert second.json()["status"] == "private"


def test_ingest_public_post_is_listed_and_downscaled(client, ingest_token, jpeg):
    response = ingest(client, jpeg(size=(3000, 1000), color=(4, 5, 6)), status="public")
    assert response.status_code == 201
    path = response.json()["path"]
    public = {p.path for p in app.state.content_manager.get_pages_by_status("public")}
    assert path in public
    stored = settings.local_upload_path / f"{path}.webp"
    assert Image.open(stored).size == (2400, 800)


def test_ingest_rejects_bad_input(client, ingest_token, jpeg):
    assert ingest(client, b"not an image").status_code == 415
    assert ingest(client, jpeg(color=(7, 8, 9)), status="draft").status_code == 400
    too_many = [f"t{i}" for i in range(31)]
    assert ingest(client, jpeg(color=(9, 8, 7)), tags=too_many).status_code == 400
    response = client.post(
        "/api/ingest/photo",
        data={"title": "no image"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 400


# --- password mode -------------------------------------------------------


def test_password_login_form_and_wrong_password(client, password_mode, monkeypatch):
    from app import routes

    monkeypatch.setattr(routes, "LOGIN_FAILURE_DELAY", 0)
    page = client.get("/login", params={"next": "/p/x/"})
    assert page.status_code == 200 and 'name="password"' in page.text
    assert 'value="/p/x/"' in page.text
    response = client.post("/login", data={"password": "wrong", "next": "/"})
    assert response.status_code == 401
    assert auth.EDITOR_COOKIE not in response.headers.get("set-cookie", "")


def test_password_login_cookie_allows_writes(client, password_mode):
    data = {"path": "2026-11-08-password", "text": POST_TEXT}
    assert client.post("/post", data=data).status_code == 401
    response = client.post("/login", data={"password": "hunter2-but-longer", "next": "//evil.example"})
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "Secure" not in set_cookie  # plain-HTTP test client
    cookie = {auth.EDITOR_COOKIE: client.cookies.get(auth.EDITOR_COOKIE)}
    client.cookies.clear()
    assert client.post("/post", data=data, cookies=cookie).status_code == 200
    headers = {"Origin": "https://evil.example"}
    assert client.post("/post", data=data, cookies=cookie, headers=headers).status_code == 403


def test_password_login_cookie_secure_behind_https_proxy(client, password_mode):
    response = client.post(
        "/login",
        data={"password": "hunter2-but-longer"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "Secure" in response.headers["set-cookie"]

