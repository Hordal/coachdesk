"""재개 가능한 분할 업로드(상세 설계 12, 13, 21)."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import User, current_user
from app.api.main import create_app
from app.media import admission, binaries
from app.settings import Settings

from .helpers import create_upload, make_video, put_part, sha256, upload_all

PROBLEM = "application/problem+json"
pytestmark = pytest.mark.skipif(not binaries.available(), reason="FFmpeg를 찾을 수 없음")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    path = tmp_path_factory.mktemp("sample") / "clip.mp4"
    make_video(path, extra=["-preset", "veryfast", "-crf", "30", "-movflags", "+faststart"])
    return path.read_bytes()


def settings_for(tmp_path, **overrides) -> Settings:
    base = dict(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data", upload_part_size=2048)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def api(tmp_path):
    app = create_app(settings_for(tmp_path))
    return app, TestClient(app), tmp_path


def code(response) -> str:
    assert response.headers["content-type"].startswith(PROBLEM), response.text
    return response.json()["code"]


# --- 시작 ---------------------------------------------------------------------

def test_create_returns_part_plan(api, sample):
    _, client, _ = api
    r = create_upload(client, sample)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "receiving" and body["part_size"] == 2048
    assert body["part_count"] == -(-len(sample) // 2048) and body["received_parts"] == []


def test_same_idempotency_key_returns_the_same_upload(api, sample):
    _, client, _ = api
    first = create_upload(client, sample, key="same-key-123")
    second = create_upload(client, sample, key="same-key-123")
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["upload_id"] == second.json()["upload_id"]
    conflict = create_upload(client, sample + b"x", key="same-key-123")
    assert conflict.status_code == 409 and code(conflict) == "idempotency_conflict"


def test_idempotency_key_is_required(api, sample):
    _, client, _ = api
    r = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size_bytes": 10, "sport": "boxing"})
    assert r.status_code == 422 and code(r) == "invalid_request"


def test_admission_rejects_before_any_bytes_are_sent(tmp_path, sample):
    app = create_app(settings_for(tmp_path, max_upload_bytes=1000))
    client = TestClient(app)
    assert code(create_upload(client, sample, filename="clip.avi")) == "unsupported_extension"
    too_large = create_upload(client, sample)
    assert too_large.status_code == 413 and code(too_large) == "too_large"
    bad_sport = client.post("/api/v1/uploads", json={"filename": "a.mp4", "size_bytes": 10, "sport": "judo"}, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert bad_sport.status_code == 422
    assert client.post("/api/v1/uploads", json={"filename": "a.mp4", "size_bytes": 0, "sport": "boxing"}, headers={"Idempotency-Key": uuid.uuid4().hex}).status_code == 422


def test_insufficient_disk_space_is_reported_with_a_reason(api, sample, monkeypatch):
    _, client, _ = api

    class Usage:
        free = 100

    monkeypatch.setattr(admission.shutil, "disk_usage", lambda _p: Usage())
    r = create_upload(client, sample)
    assert r.status_code == 507 and code(r) == "insufficient_storage"


def test_path_in_filename_is_dropped(api, sample):
    _, client, _ = api
    body = create_upload(client, sample, filename="C:\\Users\\x\\..\\clip.mp4").json()
    assert body["filename"] == "clip.mp4"


# --- 조각 ---------------------------------------------------------------------

def test_part_checks(api, sample):
    _, client, _ = api
    upload = create_upload(client, sample).json()

    bad_digest = put_part(client, upload, 0, sample, digest="0" * 64)
    assert bad_digest.status_code == 422 and code(bad_digest) == "checksum_mismatch"

    no_digest = client.put(f"/api/v1/uploads/{upload['upload_id']}/parts/0", content=sample[:2048])
    assert no_digest.status_code == 422 and code(no_digest) == "checksum_required"

    wrong_size = client.put(f"/api/v1/uploads/{upload['upload_id']}/parts/0", content=sample[:100], headers={"X-Part-Sha256": sha256(sample[:100])})
    assert wrong_size.status_code == 422 and code(wrong_size) == "invalid_part_size"

    out_of_range = put_part(client, upload, upload["part_count"], sample)
    assert out_of_range.status_code == 422 and code(out_of_range) == "invalid_part"

    too_big = client.put(f"/api/v1/uploads/{upload['upload_id']}/parts/0", content=b"x" * 4096, headers={"X-Part-Sha256": "0" * 64})
    assert too_big.status_code == 413 and code(too_big) == "part_too_large"


def test_resume_reports_received_parts_and_resending_a_part_is_safe(api, sample):
    _, client, _ = api
    upload = create_upload(client, sample).json()
    assert put_part(client, upload, 0, sample).status_code == 200
    assert put_part(client, upload, 2, sample).status_code == 200
    assert put_part(client, upload, 2, sample).status_code == 200  # 다시 보내도 같은 결과

    status = client.get(f"/api/v1/uploads/{upload['upload_id']}").json()
    assert status["received_parts"] == [0, 2]  # 이어 올릴 때 이 조각은 건너뛴다
    assert status["status"] == "receiving"


# --- 완료 ---------------------------------------------------------------------

def test_complete_requires_every_part(api, sample):
    _, client, _ = api
    upload = create_upload(client, sample).json()
    put_part(client, upload, 0, sample)
    r = client.post(f"/api/v1/uploads/{upload['upload_id']}/complete")
    assert r.status_code == 409 and code(r) == "incomplete_upload"


def test_complete_registers_the_original_and_creates_a_queued_job(api, sample):
    app, client, tmp_path = api
    done = upload_all(client, sample)
    assert done["status"] == "completed" and done["video_id"] and done["job_id"]

    job = client.get(f"/api/v1/jobs/{done['job_id']}").json()
    assert job["status"] == "queued" and job["progress"] == 0
    assert [s["name"] for s in job["stages"]] == ["decode_check", "playback_copy", "frame_table", "register", "analysis"]
    assert job["stages"][-1]["status"] == "unsupported" and job["stages"][-1]["reason_code"] == "stage_not_implemented"

    original = next((tmp_path / "data" / "originals" / done["video_id"]).glob("source.mp4"))
    assert original.read_bytes() == sample
    assert not (tmp_path / "data" / "uploads" / done["upload_id"]).exists()  # 임시 조각은 정리된다

    summary = client.get(f"/api/v1/videos/{done['video_id']}/summary").json()
    assert summary["status"] == "processing" and summary["job"]["job_id"] == done["job_id"] and summary["run_id"] is None
    assert summary["filename"] == "clip.mp4" and summary["sport"] == "boxing"
    assert (summary["width"], summary["height"]) == (320, 180)


def test_complete_twice_returns_the_same_result(api, sample):
    _, client, _ = api
    first = upload_all(client, sample)
    again = client.post(f"/api/v1/uploads/{first['upload_id']}/complete")
    assert again.status_code == 200 and again.json()["video_id"] == first["video_id"] and again.json()["job_id"] == first["job_id"]
    assert len(client.get("/api/v1/videos").json()) == 1  # 영상·작업이 하나만 만들어진다


def test_closed_upload_rejects_more_parts(api, sample):
    _, client, _ = api
    done = upload_all(client, sample)
    r = put_part(client, done, 0, sample)
    assert r.status_code == 409 and code(r) == "upload_closed"


def test_not_a_video_is_rejected_by_content_not_by_extension(api):
    _, client, tmp_path = api
    garbage = os.urandom(5000)
    created = create_upload(client, garbage).json()
    for n in range(created["part_count"]):
        put_part(client, created, n, garbage)
    r = client.post(f"/api/v1/uploads/{created['upload_id']}/complete")
    assert r.status_code == 422 and code(r) == "invalid_media"
    assert client.get(f"/api/v1/uploads/{created['upload_id']}").json()["status"] == "failed"
    assert not (tmp_path / "data" / "uploads" / created["upload_id"]).exists()
    assert client.get("/api/v1/videos").json() == []  # 영상도 작업도 만들지 않는다


def test_unsupported_codec_is_rejected(api, tmp_path):
    _, client, _ = api
    path = tmp_path / "mpeg4.mp4"
    make_video(path, codec="mpeg4", seconds=1)
    data = path.read_bytes()
    created = create_upload(client, data).json()
    for n in range(created["part_count"]):
        put_part(client, created, n, data)
    r = client.post(f"/api/v1/uploads/{created['upload_id']}/complete")
    assert r.status_code == 422 and code(r) == "unsupported_codec"


def test_too_long_and_too_many_pixels_are_rejected(tmp_path, sample):
    for overrides, expected in (({"max_duration_us": 1_000_000}, "too_long"), ({"max_pixels": 1000}, "resolution_too_high")):
        client = TestClient(create_app(settings_for(tmp_path / expected, **overrides)))
        created = create_upload(client, sample).json()
        for n in range(created["part_count"]):
            put_part(client, created, n, sample)
        r = client.post(f"/api/v1/uploads/{created['upload_id']}/complete")
        assert r.status_code == 422 and code(r) == expected


def test_abort_removes_received_parts(api, sample):
    _, client, tmp_path = api
    upload = create_upload(client, sample).json()
    put_part(client, upload, 0, sample)
    assert client.delete(f"/api/v1/uploads/{upload['upload_id']}").status_code == 204
    assert client.get(f"/api/v1/uploads/{upload['upload_id']}").json()["status"] == "aborted"
    assert not (tmp_path / "data" / "uploads" / upload["upload_id"]).exists()
    assert put_part(client, upload, 1, sample).status_code == 409


# --- 접근 제어 -------------------------------------------------------------------

def test_other_users_cannot_touch_uploads_videos_or_jobs(api, sample):
    app, client, _ = api
    done = upload_all(client, sample)
    fresh = create_upload(client, sample).json()

    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    other = TestClient(app)
    checks = [
        other.get(f"/api/v1/uploads/{fresh['upload_id']}"),
        put_part(other, fresh, 0, sample),
        other.post(f"/api/v1/uploads/{fresh['upload_id']}/complete"),
        other.delete(f"/api/v1/uploads/{fresh['upload_id']}"),
        other.get(f"/api/v1/jobs/{done['job_id']}"),
        other.post(f"/api/v1/jobs/{done['job_id']}/cancel"),
        other.get(f"/api/v1/videos/{done['video_id']}/summary"),
        other.get(f"/api/v1/videos/{done['video_id']}/playback"),
    ]
    assert all(r.status_code == 404 for r in checks), [r.status_code for r in checks]
    assert other.get("/api/v1/videos").json() == []


def test_default_duration_limit_is_one_hour(tmp_path):
    from app.media.admission import Rejection, check_source
    from app.media.probe import SourceInfo

    settings = Settings(media_dir=tmp_path / "m", data_dir=tmp_path / "d")
    assert settings.max_duration_us == 3600 * 1_000_000

    def info(seconds: int) -> SourceInfo:
        return SourceInfo(container="mov,mp4,m4a,3gp,3g2,mj2", video_codec="h264", width=1920, height=1080, duration_us=seconds * 1_000_000, rotation=0, has_audio=False)

    check_source(info(3600), settings)  # 정확히 1시간은 받는다
    with pytest.raises(Rejection) as exc:
        check_source(info(3601), settings)
    assert exc.value.code == "too_long" and "60분" in (exc.value.detail or "")
