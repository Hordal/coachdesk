"""영상 삭제와 보관 기간 만료(상세 설계 15)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import User, current_user
from app.api.main import create_app
from app.jobs.store import JobStore
from app.services.deletion import DeletionService
from app.settings import Settings
from app.storage.db import Database
from app.storage.videos import VideoRepo

from .test_manual_import import add_video, payload

DAY = timedelta(days=1)


@pytest.fixture()
def env(tmp_path):
    settings = Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data")
    app = create_app(settings)
    db = Database(settings.db_path)
    return app, TestClient(app), db, settings


def make_files(settings: Settings, video_id: str) -> list:
    originals = settings.originals_dir / video_id
    originals.mkdir(parents=True)
    (originals / "source.mp4").write_bytes(b"original")
    settings.playback_dir.mkdir(parents=True, exist_ok=True)
    playback = settings.playback_dir / f"{video_id}.mp4"
    playback.write_bytes(b"playback")
    tmp = settings.playback_dir / f"{video_id}.mp4.tmp"
    tmp.write_bytes(b"partial")
    return [originals, playback, tmp]


def ready_video_with_run(env, video_id="vid1"):
    app, client, db, settings = env
    add_video(db, video_id)
    files = make_files(settings, video_id)
    run_id = client.post(f"/api/v1/videos/{video_id}/imports", json=payload(video_id)).json()["run_id"]
    return run_id, files


def count(db: Database, table: str, video_id="vid1") -> int:
    with db.tx() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (video_id,)).fetchone()[0]


# --- 파일만 삭제 --------------------------------------------------------------------

def test_deleting_only_the_media_keeps_results_and_marks_evidence(env):
    _, client, db, _ = env
    run_id, files = ready_video_with_run(env)
    assert client.get(f"/api/v1/runs/{run_id}/evidence").json()[0]["playable"] is True  # 삭제 전에는 재생할 수 있다

    r = client.delete("/api/v1/videos/vid1/media")
    assert r.status_code == 200 and r.json()["media_state"] == "deleted"
    assert not any(f.exists() for f in files)

    evidence = client.get(f"/api/v1/runs/{run_id}/evidence").json()
    assert all(e["playable"] is False and e["reason_code"] == "evidence_deleted" and e["evidence"]["availability"] == "deleted" for e in evidence)
    assert client.get("/api/v1/videos/vid1/playback").json()["reason_code"] == "evidence_deleted"
    assert client.get(f"/api/v1/runs/{run_id}/findings").status_code == 200  # 요약과 수치는 남는다
    assert client.get(f"/api/v1/runs/{run_id}/findings").json()[0]["text"]
    assert client.delete("/api/v1/videos/vid1/media").status_code == 200  # 다시 요청해도 같은 결과


def test_media_cannot_be_deleted_while_processing(env):
    _, client, db, settings = env
    add_video(db, "vid1")
    make_files(settings, "vid1")
    jobs = JobStore(db)
    with db.tx(immediate=True) as conn:
        jobs.create(conn, owner_id="dev-user", kind="ingest", video_id="vid1", stages=[], max_attempts=3)
    r = client.delete("/api/v1/videos/vid1/media")
    assert r.status_code == 409 and r.json()["code"] == "video_busy"
    assert (settings.originals_dir / "vid1").exists()


# --- 영상과 결과 전체 삭제 ------------------------------------------------------------

def test_delete_all_removes_files_rows_and_everything_derived(env):
    _, client, db, _ = env
    run_id, files = ready_video_with_run(env)
    base = f"/api/v1/runs/{run_id}"
    client.post(f"{base}/reports", json={"category": "other", "text": "확인 필요"})
    client.post(f"{base}/context-notes", json={"text": "의도적으로 물러남"})
    corrected = client.post(f"{base}/corrections", json={"user_participant_id": "B"}).json()["run_id"]

    r = client.delete("/api/v1/videos/vid1")
    assert r.status_code == 202 and r.json() == {"video_id": "vid1", "status": "deleted"}
    assert not any(f.exists() for f in files)
    for table in ("videos", "runs", "jobs", "reports", "context_notes", "corrections", "uploads"):
        with db.tx() as conn:
            col = "video_id"
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = 'vid1'").fetchone()[0] == 0, table

    for path in ("/api/v1/videos/vid1/summary", f"/api/v1/runs/{run_id}", f"/api/v1/runs/{corrected}", "/api/v1/videos/vid1/playback"):
        assert client.get(path).status_code == 404
    assert client.get("/api/v1/videos").json() == []
    assert run_id not in [r["run_id"] for r in client.get("/api/v1/runs").json()]
    assert client.delete("/api/v1/videos/vid1").status_code == 404


def test_delete_blocks_access_immediately_and_finishes_after_the_running_job_stops(env):
    _, client, db, settings = env
    run_id, files = ready_video_with_run(env)
    jobs = JobStore(db, lease_seconds=60)
    with db.tx(immediate=True) as conn:
        jobs.create(conn, owner_id="dev-user", kind="ingest", video_id="vid1", stages=[{"name": "a", "label": "A", "weight": 1.0, "status": "pending", "progress": 0.0, "message": None, "reason_code": None}], max_attempts=3)
    claimed = jobs.claim_next()  # 워커가 처리 중

    r = client.delete("/api/v1/videos/vid1")
    assert r.status_code == 202 and r.json()["status"] == "deleting"  # 완료 전에는 완료라고 하지 않는다
    assert client.get("/api/v1/videos/vid1/summary").status_code == 404  # 하지만 조회는 바로 막힌다
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 404
    assert all(f.exists() for f in files)  # 작업이 쓰는 파일은 아직 건드리지 않는다
    assert jobs.cancel_requested(claimed.job_id) is True  # 작업에는 취소를 요청했다

    jobs.complete(claimed.job_id, claimed.token, status="cancelled")
    service = DeletionService(db, settings, VideoRepo(db), jobs)
    assert service.sweep() == {"purged": 1, "expired": 0}
    assert not any(f.exists() for f in files)
    assert count(db, "videos") == 0 and count(db, "runs") == 0 and count(db, "jobs") == 0


def test_a_failed_file_removal_is_retried_and_rows_are_kept_until_files_are_gone(env, monkeypatch):
    _, client, db, settings = env
    run_id, files = ready_video_with_run(env)
    service = DeletionService(db, settings, VideoRepo(db), JobStore(db))
    real = service._remove_files
    monkeypatch.setattr(service, "_remove_files", lambda video_id: False)

    video = VideoRepo(db).get("vid1")
    assert service.request_delete_all(video, "dev-user").completed is False
    assert count(db, "videos") == 1 and count(db, "runs") >= 1  # 파일이 남아 있으면 기록을 지우지 않는다
    assert client.get("/api/v1/videos/vid1/summary").status_code == 404

    monkeypatch.setattr(service, "_remove_files", real)
    assert service.sweep()["purged"] == 1
    assert count(db, "videos") == 0 and not any(f.exists() for f in files)


def test_other_users_cannot_delete(env):
    app, client, db, settings = env
    ready_video_with_run(env)
    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    other = TestClient(app)
    assert other.delete("/api/v1/videos/vid1").status_code == 404
    assert other.delete("/api/v1/videos/vid1/media").status_code == 404
    assert count(db, "videos") == 1 and (settings.originals_dir / "vid1").exists()


# --- 보관 기간 ------------------------------------------------------------------------

def set_ready_at(db: Database, video_id: str, when: datetime) -> None:
    with db.tx(immediate=True) as conn:
        conn.execute("UPDATE videos SET ready_at = ? WHERE video_id = ?", (when.isoformat(timespec="milliseconds"), video_id))


def test_expired_media_is_removed_but_results_stay(env):
    _, client, db, settings = env
    run_id, files = ready_video_with_run(env)
    add_video(db, "vid2")
    make_files(settings, "vid2")
    now = datetime.now(timezone.utc)
    set_ready_at(db, "vid1", now - 8 * DAY)
    set_ready_at(db, "vid2", now - 3 * DAY)

    service = DeletionService(db, settings, VideoRepo(db), JobStore(db))
    assert service.sweep(now)["expired"] == 1
    assert not any(f.exists() for f in files) and (settings.originals_dir / "vid2").exists()

    summary = client.get("/api/v1/videos/vid1/summary").json()
    assert summary["media_state"] == "expired" and summary["media_expires_at"] is None
    evidence = client.get(f"/api/v1/runs/{run_id}/evidence").json()
    assert all(e["reason_code"] == "evidence_expired" for e in evidence)
    assert client.get(f"/api/v1/runs/{run_id}/findings").json()[0]["text"]
    assert service.sweep(now)["expired"] == 0  # 한 번만 처리한다


def test_summary_shows_when_media_will_be_removed(env):
    _, client, db, _ = env
    ready_video_with_run(env)
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    set_ready_at(db, "vid1", when)
    assert client.get("/api/v1/videos/vid1/summary").json()["media_expires_at"] == "2026-10-08T00:00:00.000+00:00"


def test_retention_can_be_disabled_and_busy_videos_are_skipped(tmp_path):
    settings = Settings(media_dir=tmp_path / "m", media_secret="s", data_dir=tmp_path / "data", retention_days=0)
    app = create_app(settings)
    db = Database(settings.db_path)
    add_video(db, "vid1")
    make_files(settings, "vid1")
    set_ready_at(db, "vid1", datetime.now(timezone.utc) - 400 * DAY)
    service = DeletionService(db, settings, VideoRepo(db), JobStore(db))
    assert service.sweep() == {"purged": 0, "expired": 0}
    assert TestClient(app).get("/api/v1/videos/vid1/summary").json()["media_expires_at"] is None

    settings.retention_days = 7
    jobs = JobStore(db)
    with db.tx(immediate=True) as conn:
        jobs.create(conn, owner_id="dev-user", kind="ingest", video_id="vid1", stages=[], max_attempts=3)
    assert service.sweep()["expired"] == 0  # 처리 중이면 건드리지 않는다
    assert (settings.originals_dir / "vid1").exists()
