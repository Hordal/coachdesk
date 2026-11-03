import csv
import io
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.deps import User, current_user
from app.settings import Settings
from app.storage.db import Database
from .test_manual_import import add_video
from .test_team_events import TEAM_LINE


@pytest.fixture
def env(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", enable_fixtures=False, media_secret="unit-test"))
    return app, TestClient(app), Database(app.state.settings.db_path)


def action(client, video_id="vid1"):
    response = client.post(f"/api/v1/videos/{video_id}/actions", json={"events": [TEAM_LINE]})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_operational_workspace_starts_empty_without_demo_records(env):
    _, client, _ = env
    assert client.get("/api/v1/runs").json() == []
    body = client.get("/api/v1/workspace/sessions").json()
    assert body["items"] == [] and body["counts"] == {"total":0,"processing":0,"failed":0,"pending":0,"reviewed":0}


def test_records_are_paginated_searchable_and_linked_to_roster(env):
    _, client, db = env
    for i in range(4):
        add_video(db, f"v{i}", duration=17_000_000)
    athlete = client.post("/api/v1/workspace/athletes", json={"name":"코치 시험 선수","discipline":"boxing"}).json()
    payload = {"title":"%_ 훈련 목표","athlete_id":athlete["athlete_id"],"opponent":"상대 별칭","session_date":"2026-10-22","notes":"전진 후 가드 확인","expected_revision":0}
    result = client.put("/api/v1/workspace/sessions/v0",json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["revision"] == 1 and result.json()["athlete_name"] == "코치 시험 선수"
    page = client.get("/api/v1/workspace/sessions?page_size=2").json()
    assert page["total"] == 4 and len(page["items"]) == 2
    assert len(client.get("/api/v1/workspace/sessions?q=%25_").json()["items"]) == 1
    assert client.get("/api/v1/workspace/sessions",params={"athlete_id":athlete["athlete_id"]}).json()["total"] == 1
    assert client.put("/api/v1/workspace/sessions/v0",json=payload).status_code == 409


def test_other_account_cannot_read_or_modify_records_or_attach_foreign_athletes(env):
    app, client, db = env
    add_video(db, duration=17_000_000)
    run_id = action(client)
    athlete = client.post("/api/v1/workspace/athletes",json={"name":"격리 선수"}).json()
    app.dependency_overrides[current_user] = lambda: User(user_id="other",display_name="다른 코치")
    for suffix in ["sessions/vid1",f"runs/{run_id}/review",f"runs/{run_id}/report",f"runs/{run_id}/report.csv"]:
        assert client.get("/api/v1/workspace/"+suffix).status_code == 404
    assert client.get("/api/v1/workspace/athletes").json() == []
    assert client.get("/api/v1/workspace/sessions").json()["total"] == 0
    add_video(db,"own",owner="other")
    response = client.put("/api/v1/workspace/sessions/own",json={"title":"내 기록","athlete_id":athlete["athlete_id"],"expected_revision":0})
    assert response.status_code == 422


def test_review_and_final_export_keep_candidates_unverified_and_record_actor(env):
    _, client, db = env
    add_video(db,duration=17_000_000)
    run_id = action(client)
    base = f"/api/v1/workspace/runs/{run_id}"
    assert client.get(base+"/report.csv").status_code == 409
    assert client.put(base+"/review",json={"state":"reviewed","note":"","expected_revision":0}).status_code == 422
    response = client.put(base+"/review",json={"state":"reviewed","note":"=HYPERLINK(unsafe)","expected_revision":0})
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert client.put(base+"/review",json={"state":"in_review","note":"덮어쓰기","expected_revision":0}).status_code == 409
    report = client.get(base+"/report").json()
    assert report["expert_validated"] is False and report["events"][0]["verified"] is False
    assert report["events"][0]["status"] == "CANDIDATE"
    exported = client.get(base+"/report.csv")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert rows[1][5].startswith("'=HYPERLINK")
    assert rows[1][-2:] == ["False","UNKNOWN"]
    activities = client.get("/api/v1/workspace/activity").json()
    assert {row["action"] for row in activities} == {"review.reviewed","report.exported"}


def test_new_analysis_invalidates_old_review_and_export(env):
    _, client, db = env
    add_video(db,duration=17_000_000)
    old = action(client)
    client.put(f"/api/v1/workspace/runs/{old}/review",json={"state":"reviewed","note":"검토 완료","expected_revision":0})
    new = action(client)
    assert new != old
    assert client.get(f"/api/v1/workspace/runs/{old}/report.csv").status_code == 409
    assert client.put(f"/api/v1/workspace/runs/{old}/review",json={"state":"reviewed","note":"오래된 결과","expected_revision":1}).status_code == 409
    assert client.get("/api/v1/workspace/sessions?status=pending").json()["total"] == 1


def test_review_history_preserves_previous_notes_and_deletion_removes_them(env):
    _, client, db = env
    add_video(db,duration=17_000_000)
    run_id = action(client)
    base = f"/api/v1/workspace/runs/{run_id}"
    assert client.put(base+"/review",json={"state":"in_review","note":"첫 의견","expected_revision":0}).status_code == 200
    assert client.put(base+"/review",json={"state":"reviewed","note":"최종 의견","expected_revision":1}).status_code == 200
    history = client.get(base+"/review-history").json()
    assert [(row["revision"],row["note"]) for row in history] == [(2,"최종 의견"),(1,"첫 의견")]
    assert client.delete("/api/v1/videos/vid1").status_code == 202
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM coach_review_history").fetchone()[0] == 0


def test_reports_render_findings_as_text_using_server_computed_values(env):
    from .test_manual_import import payload
    _, client, db = env
    add_video(db)
    run_id = client.post("/api/v1/videos/vid1/imports",json=payload()).json()["run_id"]
    report = client.get(f"/api/v1/workspace/runs/{run_id}/report").json()
    assert isinstance(report["findings"][0]["text"],str)
    assert "4회" in report["findings"][0]["text"]
    assert report["findings"][1]["text"] is None


def test_failed_video_can_be_retried_once_and_missing_original_is_explained(env,tmp_path):
    _, client, db = env
    add_video(db,status="failed")
    assert client.post("/api/v1/workspace/sessions/vid1/retry").status_code == 409
    original = tmp_path / "original.mp4"
    original.write_bytes(b"test-only")
    with db.tx(immediate=True) as conn:
        conn.execute("UPDATE videos SET source_path=? WHERE video_id='vid1'",(str(original),))
    response = client.post("/api/v1/workspace/sessions/vid1/retry")
    assert response.status_code == 201
    assert client.post("/api/v1/workspace/sessions/vid1/retry").status_code == 409
    assert client.get("/api/v1/workspace/sessions/vid1").json()["status"] == "processing"


def test_workspace_is_protected_by_session_and_csrf(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "session",auth_mode="session",enable_fixtures=False))
    client = TestClient(app)
    assert client.get("/api/v1/workspace/sessions").status_code == 401
    app.state.auth.create_user(email="unit@example.com",password="temporary-only-123",display_name="코치",role="participant",consented=True)
    login = client.post("/api/v1/auth/login",json={"email":"unit@example.com","password":"temporary-only-123"})
    assert login.status_code == 200
    assert client.post("/api/v1/workspace/athletes",json={"name":"회원"}).status_code == 403
    assert client.post("/api/v1/workspace/athletes",json={"name":"회원"},headers={"X-CSRF-Token":login.json()["csrf_token"]}).status_code == 201


def test_worker_presence_detects_stale_workers_and_api_is_not_cached(env):
    _, client, db = env
    with db.tx(immediate=True) as conn:
        conn.execute("INSERT INTO worker_presence VALUES('alive',?)",(time.time(),))
        conn.execute("INSERT INTO worker_presence VALUES('stale',?)",(time.time()-60,))
    response = client.get("/api/v1/workspace/status")
    assert response.json()["active_workers"] == 1
    assert response.headers["cache-control"] == "no-store"


def test_concurrent_review_saves_accept_one_revision_without_losing_the_other(env):
    from concurrent.futures import ThreadPoolExecutor
    app, client, db = env
    add_video(db,duration=17_000_000)
    run_id = action(client)
    def save(index):
        with TestClient(app) as own_client:
            return own_client.put(f"/api/v1/workspace/runs/{run_id}/review",json={"state":"in_review","note":f"검토 {index}","expected_revision":0}).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(save,range(6)))
    assert statuses.count(200) == 1 and statuses.count(409) == 5
    assert len(client.get(f"/api/v1/workspace/runs/{run_id}/review-history").json()) == 1


def test_thousand_records_remain_paginated_and_isolated(env):
    from app.storage.db import now_rfc3339
    _, client, db = env
    with db.tx(immediate=True) as conn:
        conn.executemany("INSERT INTO videos(video_id,owner_id,filename,sport,source_path,size_bytes,sha256,duration_us,width,height,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         [(f"bulk{i}","dev-user",f"sparring-{i}.mp4","boxing","unused",1,"h",17_000_000,320,180,"ready",now_rfc3339()) for i in range(1000)])
    body = client.get("/api/v1/workspace/sessions?page=4&page_size=25").json()
    assert body["total"] == 1000 and len(body["items"]) == 25
    assert body["counts"]["total"] == 1000


def test_production_profile_refuses_unsafe_auth_and_missing_stable_secret(tmp_path,monkeypatch):
    with pytest.raises(ValueError):
        create_app(Settings(data_dir=tmp_path / "bad",app_profile="production"))
    monkeypatch.delenv("MEDIA_SECRET",raising=False)
    settings = Settings(data_dir=tmp_path / "prod",app_profile="production",auth_mode="session",cookie_secure=True,enable_fixtures=False)
    with pytest.raises(ValueError):
        create_app(settings)
    monkeypatch.setenv("MEDIA_SECRET","s"*64)
    assert create_app(settings).state.settings.enable_fixtures is False
