"""팀 연구 포즈 가져오기(상세 설계 27의 9절, 14의 PoseObservation)."""

from __future__ import annotations

import copy
import shutil

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import CORE_JOINTS, MEDIAPIPE_LANDMARKS
from app.media import binaries, probe
from app.settings import Settings
from app.storage.db import Database

from .helpers import make_video
from .test_manual_import import add_video

pytestmark = pytest.mark.skipif(not binaries.available(), reason="FFmpeg를 찾을 수 없음")

VIDEO = "vid1"


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("pose") / "clip.mp4"
    make_video(path, extra=["-preset", "veryfast", "-crf", "30", "-movflags", "+faststart"])
    return path


@pytest.fixture()
def env(tmp_path, clip):
    settings = Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data")
    app = create_app(settings)
    db = Database(settings.db_path)
    add_video(db, VIDEO, duration=3_000_000)
    settings.playback_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip, settings.playback_dir / f"{VIDEO}.mp4")
    table = [f.pts_us for f in probe.probe_video(settings.playback_dir / f"{VIDEO}.mp4").frames]
    return TestClient(app), settings, db, table


def landmarks(visibility=0.9) -> dict:
    """팀 연구 포즈의 한 포즈: 33점. 좌표는 분석 프레임(1280×720)."""
    return {
        name: {"x_norm": 0.5, "y_norm": 0.01 * i, "z_norm": 0.1, "x_px": 640.0, "y_px": 7.2 * i, "visibility": visibility, "presence": 0.99,
               "off_image": False, "finite": True, "state": "VALID", "index": i}
        for i, name in enumerate(MEDIAPIPE_LANDMARKS)
    }


def make_payload(table: list[int]) -> dict:
    rows = []
    for i in (0, 10, 11):
        rows.append({
            "schema_version": "phase1c_v1", "clip_id": "01", "frame_index": i, "timestamp_us": table[i], "mode": "IMAGE", "source": "candidate_crop",
            "attempt": "OBSERVED", "status": "FEATURE_ELIGIBLE", "participant_id": "candidate:0", "role": "UNRESOLVED", "track_id": 0,
            "tracklet_ref": "track:0:t0", "pose_index": 0, "raw_landmark_count": 33, "landmarks": landmarks(),
        })
    rows.append({"clip_id": "01", "frame_index": 12, "timestamp_us": table[12], "mode": "IMAGE", "source": "full_frame", "status": "NO_POSE",
                 "participant_id": None, "role": "UNRESOLVED", "landmarks": None})
    quality = [{"frame_index": 10, "timestamp_us": table[10], "arm": "left", "participant_id": "candidate:0", "role": "UNRESOLVED",
                "feature_ready": True, "statuses": [], "reasons": []},
               {"frame_index": 12, "timestamp_us": table[12], "participant_id": None, "feature_ready": False,
                "statuses": ["NO_GEOMETRIC_MATCH", "LOW_WRIST_VISIBILITY"], "reasons": ["NO_GEOMETRIC_MATCH"]}]
    windows = [{"schema_version": "phase1c_v1", "status": "CANDIDATES_ONLY", "role_policy": "UNRESOLVED", "frame_start": 10, "frame_end": 12,
                "pts_start": table[10], "pts_end": table[12], "participants": [{"participant_id": "candidate:0", "track_id": 0}], "rejected_tracks": []}]
    return {"source": "team-research", "poses": rows, "quality": quality, "windows": windows}


def post(client, body, video_id=VIDEO):
    return client.post(f"/api/v1/videos/{video_id}/poses", json=body)


def errors_of(client, body) -> list[str]:
    r = post(client, body)
    assert r.status_code == 422 and r.json()["code"] == "invalid_poses", r.text
    return r.json()["errors"]


def test_import_keeps_all_33_points_and_does_not_assign_anyone(env):
    client, settings, _, table = env
    r = post(client, make_payload(table))
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["row_count"], body["frame_count"], body["no_landmark_rows"], body["quality_row_count"], body["window_count"]) == (4, 4, 1, 2, 1)
    assert body["status_counts"] == {"FEATURE_ELIGIBLE": 3, "NO_POSE": 1}
    assert body["participant_ids"] == ["candidate:0"] and body["clip_ids"] == ["01"]
    assert body["feature_ready_rows"] == 3 + 1  # 1C 상태 3줄 + 품질 줄 1개
    assert (body["first_us"], body["last_us"]) == (table[0], table[12])
    # 선수를 정하지 않는다: 실행·참가자가 만들어지지 않는다
    assert client.get(f"/api/v1/videos/{VIDEO}/poses").json()["set_id"] == body["set_id"]
    assert not any(run["video_id"] == VIDEO for run in client.get("/api/v1/runs").json())
    # 저장한 파일에는 33점과 visibility·presence·off_image가 그대로 있다
    stored = settings.poses_dir / VIDEO / f"{body['set_id']}.json"
    text = stored.read_text(encoding="utf-8")
    assert text.count('"left_foot_index"') == 3 and '"presence"' in text and '"off_image"' in text


def test_same_content_returns_the_existing_set(env):
    client, _, _, table = env
    first = post(client, make_payload(table)).json()
    again = post(client, make_payload(table))
    assert again.status_code == 200 and again.json()["set_id"] == first["set_id"]


def test_poses_must_match_the_video_frame_times(env):
    client, _, _, table = env
    body = make_payload(table)
    body["poses"][0]["timestamp_us"] += 5_000  # 프레임 시각과 다름
    assert any("프레임 0" in e or "frame_index 0" in e for e in errors_of(client, body))
    body = make_payload(table)
    body["poses"][1]["frame_index"] = 10_000  # 영상 밖
    assert any("영상의 프레임 수" in e for e in errors_of(client, body))
    body = make_payload(table)
    body["quality"][0]["timestamp_us"] += 5_000
    assert any(e.startswith("quality[0]") for e in errors_of(client, body))
    body = make_payload(table)
    body["windows"][0]["frame_end"] = 10_000
    assert any(e.startswith("windows[0]") for e in errors_of(client, body))


def test_rejects_unknown_landmarks_missing_core_joints_and_out_of_range_values(env):
    client, _, _, table = env
    body = make_payload(table)
    body["poses"][0]["landmarks"]["left_tail"] = copy.deepcopy(body["poses"][0]["landmarks"]["nose"])
    assert any("알 수 없는 랜드마크" in e for e in errors_of(client, body))
    body = make_payload(table)
    del body["poses"][0]["landmarks"][CORE_JOINTS[3]]
    assert any("핵심 관절이 없음" in e for e in errors_of(client, body))
    body = make_payload(table)
    body["poses"][0]["landmarks"]["nose"]["visibility"] = 1.5
    assert any("nose.visibility" in e for e in errors_of(client, body))


def test_only_one_clip_per_import(env):
    client, _, _, table = env
    body = make_payload(table)
    body["poses"][1]["clip_id"] = "02"
    assert any("한 영상" in e for e in errors_of(client, body))


def test_not_finite_points_are_kept_and_not_checked(env):
    client, _, _, table = env
    body = make_payload(table)
    body["poses"][0]["landmarks"]["nose"].update({"x_px": None, "y_px": None, "finite": False})
    assert post(client, body).status_code == 201  # 값이 없는 점은 0으로 채우지 않고 그대로 둔다


def test_unknown_video_and_missing_poses(env):
    client, _, _, table = env
    assert post(client, make_payload(table), video_id="nope").status_code == 404
    r = client.get(f"/api/v1/videos/{VIDEO}/poses")
    assert r.status_code == 404 and r.json()["code"] == "poses_not_found"


def test_deleting_the_video_removes_the_poses(env):
    client, settings, db, table = env
    set_id = post(client, make_payload(table)).json()["set_id"]
    assert (settings.poses_dir / VIDEO / f"{set_id}.json").exists()
    assert client.delete(f"/api/v1/videos/{VIDEO}").status_code in (200, 202, 204)
    assert not (settings.poses_dir / VIDEO).exists()
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM pose_sets WHERE video_id = ?", (VIDEO,)).fetchone()["n"] == 0
