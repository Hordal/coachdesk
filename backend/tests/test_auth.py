"""초대 계정, 세션, CSRF, 권한(상세 설계 21)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.auth import cli
from app.auth.service import AuthError, AuthService
from app.settings import Settings
from app.storage.db import Database

from .test_manual_import import add_video, payload

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def make_settings(tmp_path, **kw) -> Settings:
    return Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data", auth_mode="session", **kw)


@pytest.fixture()
def env(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    db = Database(settings.db_path)
    admin_id = app.state.auth.create_user(email="admin@example.com", password=PASSWORD, display_name="관리자", role="admin", consented=True)
    return app, settings, db, admin_id


def login(app, email="admin@example.com", password=PASSWORD) -> TestClient:
    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client


def invite_and_join(app, admin: TestClient, email: str, name="참가자") -> TestClient:
    token = admin.post("/api/v1/admin/invites", json={"email": email}).json()["accept_path"].rsplit("/", 1)[1]
    r = TestClient(app).post(f"/api/v1/auth/invites/{token}/accept", json={"display_name": name, "password": PASSWORD, "consent": True})
    assert r.status_code == 201, r.text
    return login(app, email)


# --- 인증이 없으면 아무것도 열리지 않는다 -------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/me"), ("GET", "/api/v1/runs"), ("GET", "/api/v1/runs/fixture-counter"), ("GET", "/api/v1/videos"),
        ("POST", "/api/v1/uploads"), ("GET", "/api/v1/jobs/x"), ("GET", "/api/v1/videos/v/playback"), ("DELETE", "/api/v1/videos/v"),
        ("POST", "/api/v1/runs/fixture-counter/corrections"), ("GET", "/api/v1/admin/users"), ("POST", "/api/v1/admin/invites"),
    ],
)
def test_protected_routes_need_a_session(env, method, path):
    app, *_ = env
    r = TestClient(app).request(method, path)
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"


def test_health_and_signed_media_do_not_need_a_session(env):
    app, *_ = env
    assert TestClient(app).get("/api/v1/health").status_code == 200
    assert TestClient(app).get("/api/v1/media/v1?exp=1&sig=bad").status_code == 403  # 서명이 인증을 대신하고 틀리면 막는다


# --- 로그인 ---------------------------------------------------------------------------

def test_login_sets_a_safe_cookie_and_me_works(env):
    app, *_ = env
    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"email": "Admin@Example.com ", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["csrf_token"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    me = client.get("/api/v1/me").json()
    assert me["email"] == "admin@example.com" and me["auth_mode"] == "session"


def test_secure_flag_follows_the_setting(tmp_path):
    app = create_app(make_settings(tmp_path, cookie_secure=True))
    app.state.auth.create_user(email="a@example.com", password=PASSWORD, display_name="a", role="admin")
    r = TestClient(app).post("/api/v1/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_wrong_password_and_unknown_email_look_the_same(env):
    app, *_ = env
    a = TestClient(app).post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong password!"})
    b = TestClient(app).post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong password!"})
    assert a.status_code == b.status_code == 401
    assert a.json()["code"] == b.json()["code"] == "invalid_credentials" and a.json()["title"] == b.json()["title"]


def test_repeated_failures_lock_out_even_the_right_password(env):
    app, *_ = env
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "nope nope nope"}).status_code == 401
    locked = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert locked.status_code == 429 and locked.json()["code"] == "too_many_attempts"


def test_lockout_expires_with_time_and_success_clears_the_counter(tmp_path):
    settings = make_settings(tmp_path)
    clock = Clock()
    auth = AuthService(Database(settings.db_path), settings, clock)
    auth.create_user(email="a@example.com", password=PASSWORD, display_name="a")
    for _ in range(5):
        with pytest.raises(AuthError):
            auth.login("a@example.com", "nope nope nope")
    with pytest.raises(AuthError) as locked:
        auth.login("a@example.com", PASSWORD)
    assert locked.value.code == "too_many_attempts"
    clock.now += settings.login_window_seconds + 1
    token, _ = auth.login("a@example.com", PASSWORD)
    assert auth.session_user(token) is not None


def test_session_expires_and_logout_revokes_it_on_the_server(tmp_path):
    settings = make_settings(tmp_path)
    clock = Clock()
    auth = AuthService(Database(settings.db_path), settings, clock)
    auth.create_user(email="a@example.com", password=PASSWORD, display_name="a")
    token, _ = auth.login("a@example.com", PASSWORD)
    assert auth.session_user(token) is not None
    clock.now += settings.session_ttl_seconds + 1
    assert auth.session_user(token) is None  # 만료

    token2, _ = auth.login("a@example.com", PASSWORD)
    auth.logout(token2)
    assert auth.session_user(token2) is None  # 폐기


def test_logout_makes_a_copied_cookie_useless(env):
    app, *_ = env
    client = login(app)
    stolen = client.cookies.get("session")
    assert client.post("/api/v1/auth/logout").status_code == 204
    other = TestClient(app, cookies={"session": stolen})
    assert other.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me").status_code == 401


# --- CSRF -----------------------------------------------------------------------------

def test_changes_need_the_csrf_token_but_reads_do_not(env):
    app, *_ = env
    client = login(app)
    client.headers.pop("X-CSRF-Token")
    assert client.get("/api/v1/runs").status_code == 200
    missing = client.post("/api/v1/admin/invites", json={"email": "x@example.com"})
    assert missing.status_code == 403 and missing.json()["code"] == "csrf_failed"
    wrong = client.post("/api/v1/admin/invites", json={"email": "x@example.com"}, headers={"X-CSRF-Token": "guess"})
    assert wrong.status_code == 403
    token = client.get("/api/v1/me").json()["csrf_token"]
    assert client.post("/api/v1/admin/invites", json={"email": "x@example.com"}, headers={"X-CSRF-Token": token}).status_code == 201


def test_csrf_token_of_another_session_is_rejected(env):
    app, *_ = env
    a, b = login(app), login(app)
    b.headers["X-CSRF-Token"] = a.headers["X-CSRF-Token"]
    assert b.post("/api/v1/admin/invites", json={"email": "x@example.com"}).status_code == 403


# --- 초대 -----------------------------------------------------------------------------

def test_invite_flow_creates_a_participant_who_is_not_admin(env):
    app, _, _, _ = env
    admin = login(app)
    user = invite_and_join(app, admin, "p@example.com")
    me = user.get("/api/v1/me").json()
    assert me["role"] == "participant" and me["display_name"] == "참가자"
    assert user.get("/api/v1/admin/users").status_code == 403
    assert user.post("/api/v1/admin/invites", json={"email": "z@example.com"}).status_code == 403


def test_invite_is_single_use_and_requires_consent_and_a_decent_password(env):
    app, *_ = env
    admin = login(app)
    token = admin.post("/api/v1/admin/invites", json={"email": "p@example.com"}).json()["accept_path"].rsplit("/", 1)[1]
    anon = TestClient(app)
    base = f"/api/v1/auth/invites/{token}"
    assert anon.get(base).json() == {"email": "p@example.com", "role": "participant"}
    assert anon.post(f"{base}/accept", json={"display_name": "p", "password": PASSWORD, "consent": False}).json()["code"] == "consent_required"
    assert anon.post(f"{base}/accept", json={"display_name": "p", "password": "short", "consent": True}).json()["code"] == "weak_password"
    assert anon.post(f"{base}/accept", json={"display_name": "p", "password": PASSWORD, "consent": True}).status_code == 201
    again = anon.post(f"{base}/accept", json={"display_name": "p", "password": PASSWORD, "consent": True})
    assert again.status_code == 404 and again.json()["code"] == "invite_invalid"  # 두 번 쓸 수 없다
    assert anon.get(base).status_code == 404


def test_unknown_expired_and_revoked_invites_are_indistinguishable(tmp_path):
    settings = make_settings(tmp_path)
    clock = Clock()
    auth = AuthService(Database(settings.db_path), settings, clock)
    admin = auth.create_user(email="a@example.com", password=PASSWORD, display_name="a", role="admin")
    _, expiring, _ = auth.create_invite(admin, "e@example.com", ttl_hours=1)
    revoked_id, revoked, _ = auth.create_invite(admin, "r@example.com")
    auth.revoke_invite(admin, revoked_id)
    clock.now += 2 * 3600
    for token in (expiring, revoked, "never-issued"):
        with pytest.raises(AuthError) as exc:
            auth.invite_info(token)
        assert (exc.value.status, exc.value.code) == (404, "invite_invalid")


def test_cannot_invite_an_existing_email_and_listing_never_shows_tokens(env):
    app, *_ = env
    admin = login(app)
    assert admin.post("/api/v1/admin/invites", json={"email": "ADMIN@example.com"}).json()["code"] == "email_taken"
    created = admin.post("/api/v1/admin/invites", json={"email": "p@example.com"}).json()
    listed = admin.get("/api/v1/admin/invites").json()
    assert listed[0]["state"] == "pending" and "token" not in str(listed) and created["accept_path"].split("/")[-1] not in str(listed)
    assert admin.delete(f"/api/v1/admin/invites/{created['invite_id']}").status_code == 204
    assert admin.get("/api/v1/admin/invites").json()[0]["state"] == "revoked"


# --- 차단 -----------------------------------------------------------------------------

def test_blocking_ends_open_sessions_and_stops_new_logins(env):
    app, *_ = env
    admin = login(app)
    user = invite_and_join(app, admin, "p@example.com")
    user_id = user.get("/api/v1/me").json()["user_id"]

    assert admin.post(f"/api/v1/admin/users/{user_id}/block").status_code == 204
    assert user.get("/api/v1/me").status_code == 401  # 열려 있던 세션이 바로 끊긴다
    blocked = TestClient(app).post("/api/v1/auth/login", json={"email": "p@example.com", "password": PASSWORD})
    assert blocked.status_code == 403 and blocked.json()["code"] == "account_blocked"

    assert admin.post(f"/api/v1/admin/users/{user_id}/unblock").status_code == 204
    assert login(app, "p@example.com").get("/api/v1/me").status_code == 200


def test_an_admin_cannot_block_themselves(env):
    app, _, _, admin_id = env
    assert login(app).post(f"/api/v1/admin/users/{admin_id}/block").json()["code"] == "cannot_block_self"


# --- 데이터 격리와 저장 ------------------------------------------------------------------

def test_two_participants_cannot_see_or_touch_each_others_data(env):
    app, _, db, _ = env
    admin = login(app)
    a = invite_and_join(app, admin, "a@example.com", "A")
    b = invite_and_join(app, admin, "b@example.com", "B")
    a_id = a.get("/api/v1/me").json()["user_id"]
    add_video(db, "vid-a", owner=a_id)
    run_id = a.post("/api/v1/videos/vid-a/imports", json=payload("vid-a")).json()["run_id"]

    assert a.get(f"/api/v1/runs/{run_id}").status_code == 200
    for r in (
        b.get(f"/api/v1/runs/{run_id}"), b.get("/api/v1/videos/vid-a/summary"), b.get("/api/v1/videos/vid-a/playback"),
        b.delete("/api/v1/videos/vid-a"), b.post(f"/api/v1/runs/{run_id}/corrections", json={"user_participant_id": "B"}),
        b.post("/api/v1/videos/vid-a/imports", json=payload("vid-a")),
    ):
        assert r.status_code == 404
    assert b.get("/api/v1/videos").json() == [] and run_id not in [x["run_id"] for x in b.get("/api/v1/runs").json()]
    assert admin.get(f"/api/v1/runs/{run_id}").status_code == 404  # 관리자도 영상·결과를 열람할 수 없다


def test_uploads_work_with_the_session_and_csrf(env):
    app, *_ = env
    client = login(app)
    created = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size_bytes": 10, "sport": "boxing"}, headers={"Idempotency-Key": "k" * 12})
    assert created.status_code == 201
    no_csrf = TestClient(app, cookies=dict(client.cookies)).put(f"/api/v1/uploads/{created.json()['upload_id']}/parts/0", content=b"x" * 10, headers={"X-Part-Sha256": "0" * 64})
    assert no_csrf.status_code == 403


def test_secrets_are_not_stored_in_plain_text(env):
    app, _, db, _ = env
    admin = login(app)
    token = admin.post("/api/v1/admin/invites", json={"email": "p@example.com"}).json()["accept_path"].rsplit("/", 1)[1]
    cookie = admin.cookies.get("session")
    with db.tx() as conn:
        dump = " ".join(str(tuple(r)) for t in ("users", "sessions", "invites", "audit_log") for r in conn.execute(f"SELECT * FROM {t}"))
    assert PASSWORD not in dump and token not in dump and cookie not in dump
    assert "$argon2" in dump  # 검증된 해시 라이브러리의 형식


def test_audit_log_records_events_without_secrets(env):
    app, _, db, _ = env
    admin = login(app)
    TestClient(app).post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong wrong wrong"})
    admin.post("/api/v1/admin/invites", json={"email": "p@example.com"})
    with db.tx() as conn:
        events = [r["event"] for r in conn.execute("SELECT event FROM audit_log ORDER BY id")]
        details = " ".join(r["detail_json"] or "" for r in conn.execute("SELECT detail_json FROM audit_log"))
    assert {"login", "login_failed", "invite_created"} <= set(events)
    assert "p@example.com" not in details and "wrong" not in details


# --- dev 모드와 명령줄 ----------------------------------------------------------------------

def test_dev_mode_keeps_the_fixed_account_without_login(tmp_path):
    app = create_app(Settings(media_dir=tmp_path / "m", media_secret="s", data_dir=tmp_path / "data"))
    client = TestClient(app)
    me = client.get("/api/v1/me").json()
    assert me["auth_mode"] == "dev" and me["user_id"] == "dev-user" and me["csrf_token"] is None
    assert client.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "x"}).status_code == 404


def test_cli_ignores_a_bom_that_windows_powershell_puts_before_piped_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bom-data"))
    monkeypatch.setattr("sys.stdin", io.StringIO("﻿" + PASSWORD + "\r\n"))
    assert cli.main(["create-admin", "--email", "root@example.com", "--password-stdin"]) == 0
    settings = Settings(data_dir=tmp_path / "bom-data", media_dir=tmp_path / "m", auth_mode="session")
    assert AuthService(Database(settings.db_path), settings).login("root@example.com", PASSWORD)


def test_cli_creates_an_admin_from_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "cli-data"))
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main(["create-admin", "--email", "root@example.com", "--password-stdin"]) == 0
    settings = Settings(data_dir=tmp_path / "cli-data", media_dir=tmp_path / "m", auth_mode="session")
    token, user = AuthService(Database(settings.db_path), settings).login("root@example.com", PASSWORD)
    assert user.role == "admin" and token

    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main(["create-admin", "--email", "root@example.com", "--password-stdin"]) == 1  # 이미 있는 이메일
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(["create-admin", "--email", "other@example.com", "--password-stdin"]) == 1
