"""실제 영상 시험에서 확인한 형식별 동작을 작은 합성 영상으로 회귀 시험한다."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.jobs.worker import Worker
from app.media import binaries
from app.settings import Settings

from .helpers import create_upload, make_video, put_part, upload_all

pytestmark = pytest.mark.skipif(not binaries.available(), reason="FFmpeg를 찾을 수 없음")


def ffmpeg(*args: str) -> None:
    subprocess.run([binaries.find_binary("ffmpeg"), "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    path = tmp_path_factory.mktemp("fmt") / "base.mp4"
    make_video(path, seconds=4, extra=["-preset", "veryfast", "-crf", "30"])
    return path


@pytest.fixture()
def env(tmp_path):
    settings = Settings(media_dir=tmp_path / "m", media_secret="s", data_dir=tmp_path / "data", upload_part_size=64 * 1024)
    return settings, TestClient(create_app(settings))


def run(env, data: bytes, filename: str):
    settings, client = env
    done = upload_all(client, data, filename=filename)
    Worker(settings).run_until_idle()
    return client, done["video_id"], client.get(f"/api/v1/videos/{done['video_id']}/summary").json()


def test_rotated_phone_video_is_prepared_upright(env, base, tmp_path):
    rotated = tmp_path / "rot.mp4"
    ffmpeg("-display_rotation", "90", "-i", str(base), "-c", "copy", str(rotated))  # 가로로 부호화하고 회전 정보만 90도
    client, vid, summary = run(env, rotated.read_bytes(), "rot.mp4")
    assert summary["status"] == "ready"
    info = client.get(f"/api/v1/videos/{vid}").json()
    assert (info["width"], info["height"]) == (180, 320)  # 재생용 사본은 회전을 적용한 세로 영상이다
    assert client.get(f"/api/v1/videos/{vid}/frame-image", params={"time_us": 1_000_000}).status_code == 200


def test_hevc_mov_is_accepted(env, base, tmp_path):
    mov = tmp_path / "clip.mov"
    ffmpeg("-i", str(base), "-c:v", "libx265", "-preset", "ultrafast", "-crf", "30", "-tag:v", "hvc1", "-an", str(mov))
    client, vid, summary = run(env, mov.read_bytes(), "clip.mov")
    assert summary["status"] == "ready" and client.get(f"/api/v1/videos/{vid}").json()["frame_count"] == 120


def test_variable_frame_rate_uses_the_average_versus_reported_rule(env, base, tmp_path):
    vfr = tmp_path / "vfr.mp4"
    ffmpeg("-i", str(base), "-vf", "select='not(mod(n,4))+not(mod(n,6))'", "-fps_mode", "vfr", "-video_track_timescale", "90000", "-c:v", "libx264", "-preset", "ultrafast", "-an", str(vfr))
    client, vid, summary = run(env, vfr.read_bytes(), "vfr.mp4")
    assert summary["status"] == "ready" and client.get(f"/api/v1/videos/{vid}").json()["is_vfr"] is True
    assert "가변" in next(s for s in summary["job"]["stages"] if s["name"] == "frame_table")["message"]


def test_truncated_file_is_refused_or_fails_with_a_readable_reason(env, base, tmp_path):
    settings, client = env
    # 재생 정보(moov)가 파일 끝에 있는 영상이 잘리면 올리는 순간 영상이 아니라고 거절한다
    data = base.read_bytes()
    up = create_upload(client, data[: len(data) // 2]).json()
    for n in range(up["part_count"]):
        put_part(client, up, n, data[: len(data) // 2])
    assert client.post(f"/api/v1/uploads/{up['upload_id']}/complete").json()["code"] == "invalid_media"

    # 재생 정보가 앞에 있는 영상(스마트폰·내려받은 영상 대부분)이 잘리면 처리 중에 읽기 오류로 끝난다
    fast = tmp_path / "fast.mp4"
    ffmpeg("-i", str(base), "-c", "copy", "-movflags", "+faststart", str(fast))
    cut = fast.read_bytes()
    cut = cut[: len(cut) // 2]
    _, _, summary = run(env, cut, "cut.mp4")
    assert summary["status"] == "failed"
    error = summary["job"]["error"]
    assert error["code"] == "decode_error" and error["retryable"] is False and "손상" in error["title"]

@pytest.mark.parametrize("filename,code", [("clip.avi", "unsupported_extension")])
def test_wrong_extension_is_refused_before_upload(env, base, filename, code):
    _, client = env
    r = create_upload(client, base.read_bytes(), filename=filename)
    assert r.status_code == 422 and r.json()["code"] == code


def test_pixel_limit_rejects_oversized_video_after_upload(tmp_path, base):
    settings = Settings(media_dir=tmp_path / "m", media_secret="s", data_dir=tmp_path / "data", upload_part_size=64 * 1024, max_pixels=320 * 180 - 1)
    client = TestClient(create_app(settings))
    data = base.read_bytes()
    up = create_upload(client, data).json()
    for n in range(up["part_count"]):
        put_part(client, up, n, data)
    r = client.post(f"/api/v1/uploads/{up['upload_id']}/complete")
    assert r.status_code == 422 and r.json()["code"] == "resolution_too_high"


def test_quality_notice_follows_the_supported_and_recommended_specs():
    from app.media.admission import quality_note

    assert quality_note(1920, 1080, 60.0).startswith("권장 규격")
    assert "충족" in quality_note(1920, 1080, 59.94)  # 59.94fps는 60fps로 본다
    assert "권장 규격(1080p·60fps 이상) 미만" in quality_note(1920, 1080, 30.0)
    assert "권장 규격(1080p·60fps 이상) 미만" in quality_note(1280, 720, 60.0)
    assert quality_note(1280, 720, 29.97).startswith("기본 지원 규격 충족")
    low = quality_note(640, 360, 30.0)
    assert "기본 지원 규격(720p·30fps 이상) 미만" in low and "640×360" in low and "신뢰도" in low
    assert "24.0fps" in quality_note(1920, 1080, 24.0)
    assert quality_note(1080, 1920, 30.0).startswith("기본 지원 규격 충족")  # 세로 영상은 짧은 변으로 본다


def test_the_notice_is_shown_in_the_job_but_never_rejects_the_video(env, base):
    client, _, summary = run(env, base.read_bytes(), "small.mp4")  # 320×180, 30fps
    assert summary["status"] == "ready"  # 규격 미만이어도 거절하지 않는다
    message = next(s for s in summary["job"]["stages"] if s["name"] == "frame_table")["message"]
    assert "기본 지원 규격(720p·30fps 이상) 미만" in message and "320×180" in message
