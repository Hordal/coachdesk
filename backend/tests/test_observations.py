"""선수 검출·추적 결과 가져오기(상세 설계 27의 9절)."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import User, current_user
from app.api.main import create_app
from app.domain.models import FrameTransform, ObservationImport
from app.services.observations import PLAYBACK_TOLERANCE_US, SOURCE_TOLERANCE_US, validate
from app.media import binaries, probe
from app.media.probe import is_variable_frame_rate, parse_time_base, pts_to_us
from app.settings import Settings
from app.storage.db import Database

from .helpers import make_video
from .test_manual_import import add_video

pytestmark = pytest.mark.skipif(not binaries.available(), reason="FFmpeg를 찾을 수 없음")

VIDEO = "vid1"


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("obs") / "clip.mp4"
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
    return app, TestClient(app), settings, db, table


def make_payload(table: list[int], *, shift: int = 0) -> dict:
    """팀 파이프라인이 파일에 쓰는 줄 형식. 선수 후보 두 명(트랙 1, 2: 2는 중간에 끊김)과 배경 인물(트랙 7)이 있다."""
    transform = FrameTransform.fit(320, 180, 0, 1280, 720).model_dump()
    stamps = [t + shift for t in table]
    tracks, detections = [], []

    def obs(track_id, bbox, confidence):
        return {"track_id": track_id, "bbox": bbox, "confidence": confidence, "class_id": 0, "class_name": "person", "tracking_state": "tracked"}

    for i, ts in enumerate(stamps):
        found = [obs(1, [100, 100, 500, 700], 0.9)]
        if not 40 <= i < 50:  # 트랙 2는 40~49 프레임에서 보이지 않는다
            found.append(obs(2, [600, 100, 1000, 700], 0.8))
        if i < 5:
            found.append(obs(7, [1200, 10, 1240, 70], 0.5))
        tracks.append({"frame_index": i, "timestamp_us": ts, "tracks": found, "timing_ms": {"tracker_update_ms": 0.35}})
        detections.append({
            "frame_index": i, "timestamp_us": ts, "width": 1280, "height": 720, "coordinate_space": "analysis_frame_xyxy_px",
            "provider": "rtdetr_hf", "null_timestamp": False,
            "detections": [{"class_id": 0, "class_name": "person", "confidence": 0.91, "bbox": [100, 100, 500, 700]}],
        })
    return {
        "source": "test",
        "metadata": {
            "source": "clip.mp4", "duration_us": 3_000_000, "width": 320, "height": 180, "coded_width": 320, "coded_height": 180,
            "fps": 30.0, "codec": "h264", "rotation_degrees": 0, "is_vfr": False, "frame_count": len(stamps),
            "time_base": "1/15360", "start_time_us": stamps[0], "frame_timestamps_us": stamps, "timestamp_basis": "source_pts",
            "analysis_width": 1280, "analysis_height": 720, "derivative_path": "C:/Users/someone/data/derived.mp4",
            "derivative_frame_count": len(stamps), "transform": transform, "warnings": [], "unknown_future_field": 1,
        },
        "tracks": tracks,
        "detections": detections,
    }


def post(client, body, video_id=VIDEO):
    return client.post(f"/api/v1/videos/{video_id}/observations", json=body)


def errors_of(client, body) -> list[str]:
    r = post(client, body)
    assert r.status_code == 422 and r.json()["code"] == "invalid_observations", r.text
    return r.json()["errors"]


# --- 정상 가져오기 ----------------------------------------------------------------------

def test_import_stores_the_tracks_as_candidates_without_choosing_anyone(env):
    _, client, settings, _, table = env
    r = post(client, make_payload(table))
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["frame_count"], body["track_count"], body["detection_count"]) == (90, 3, 90)
    assert body["assignment_state"] == "NOT_RUN" and (body["analysis_width"], body["analysis_height"]) == (1280, 720)

    tracks = {t["track_id"]: t for t in body["tracks"]}
    assert tracks[1]["frame_count"] == 90 and tracks[1]["missing_frames"] == 0
    assert tracks[2]["frame_count"] == 80 and tracks[2]["missing_frames"] == 10  # 끊긴 구간은 채우지 않고 센다
    assert tracks[2]["longest_gap_us"] == table[50] - table[39]
    assert tracks[7]["frame_count"] == 5 and tracks[7]["last_us"] == table[4]
    assert tracks[1]["median_area_ratio"] == pytest.approx(400 * 600 / (1280 * 720), abs=1e-6)
    assert tracks[7]["median_area_ratio"] < 0.01 < tracks[1]["median_area_ratio"]  # 배경 인물은 훨씬 작다(참고 값)
    assert tracks[1]["mean_score"] == pytest.approx(0.9)

    # 선수는 정해지지 않았다: 실행·참가자가 만들어지지 않는다
    assert client.get("/api/v1/videos/vid1/observations").json()["set_id"] == body["set_id"]
    assert not any(r["video_id"] == VIDEO for r in client.get("/api/v1/runs").json())


def test_local_paths_and_unknown_fields_are_not_kept(env):
    _, client, settings, db, table = env
    post(client, make_payload(table))
    with db.tx() as conn:
        stored = conn.execute("SELECT metadata_json FROM observation_sets").fetchone()["metadata_json"]
    assert "someone" not in stored and "derivative_path" not in stored and "unknown_future_field" not in stored
    saved = json.loads(next((settings.observations_dir / VIDEO).glob("*.json")).read_text(encoding="utf-8"))
    assert len(saved["tracks"]) == 90 and saved["tracks"][0]["tracks"][0]["track_id"] == 1
    # 파이프라인이 쓴 값은 고치지 않고 그대로 보존한다(모르는 필드 포함)
    assert saved["tracks"][0]["timing_ms"] == {"tracker_update_ms": 0.35} and saved["tracks"][0]["tracks"][0]["tracking_state"] == "tracked"
    assert saved["detections"][0]["detections"][0]["confidence"] == 0.91


def test_same_content_is_not_imported_twice_and_changes_create_a_new_set(env):
    _, client, _, _, table = env
    first = post(client, make_payload(table))
    again = post(client, make_payload(table))
    assert again.status_code == 200 and again.json()["set_id"] == first.json()["set_id"]
    changed = make_payload(table)
    changed["tracks"][0]["tracks"][0]["bbox"] = [101, 100, 500, 700]
    newer = post(client, changed)
    assert newer.status_code == 201 and newer.json()["set_id"] != first.json()["set_id"]
    assert client.get("/api/v1/videos/vid1/observations").json()["set_id"] == newer.json()["set_id"]


def test_detections_are_optional(env):
    _, client, _, _, table = env
    body = make_payload(table)
    del body["detections"]
    r = post(client, body)
    assert r.status_code == 201 and r.json()["detection_count"] == 0


def test_a_constant_start_offset_is_accepted(env):
    _, client, _, _, table = env
    assert post(client, make_payload(table, shift=1_000_000)).status_code == 201  # 시작 시각이 다른 것은 허용


def test_without_the_original_the_playback_copy_tolerance_applies(env):
    _, client, _, _, table = env  # 이 환경에는 재생용 사본만 있다
    ok = make_payload(table)
    for frame in (ok["tracks"], ok["detections"]):
        frame[30]["timestamp_us"] += 500  # 사본은 원본과 시각이 조금 다를 수 있다(영상 준비 단계가 1ms까지 확인)
    ok["metadata"]["frame_timestamps_us"][30] += 500
    assert post(client, ok).status_code == 201

    far = make_payload(table)
    for frame in (far["tracks"], far["detections"]):
        frame[31]["timestamp_us"] += PLAYBACK_TOLERANCE_US + 1
    far["metadata"]["frame_timestamps_us"][31] += PLAYBACK_TOLERANCE_US + 1
    assert any("프레임 31" in e for e in errors_of(client, far))


def test_with_the_original_the_frame_times_must_match_to_rounding(env, clip):
    _, client, settings, _, _ = env
    original = settings.originals_dir / VIDEO
    original.mkdir(parents=True)
    shutil.copyfile(clip, original / "source.mp4")
    table = [f.pts_us for f in probe.probe_video(original / "source.mp4").frames]
    assert post(client, make_payload(table)).status_code == 201

    drift = make_payload(table)
    for frame in (drift["tracks"], drift["detections"]):
        frame[30]["timestamp_us"] += SOURCE_TOLERANCE_US + 3
    drift["metadata"]["frame_timestamps_us"][30] += SOURCE_TOLERANCE_US + 3
    assert any("프레임 30" in e for e in errors_of(client, drift))


# --- 거절 ---------------------------------------------------------------------------

def test_frame_count_and_time_mismatches_are_rejected_with_nothing_saved(env):
    _, client, settings, db, table = env
    short = make_payload(table[:-1])
    assert any("프레임 수" in e for e in errors_of(client, short))

    no_pts = make_payload(table)
    no_pts["metadata"]["frame_timestamps_us"][3] = None
    assert any("PTS가 없는" in e for e in errors_of(client, no_pts))

    derivative = make_payload(table)
    derivative["metadata"]["derivative_frame_count"] = 89  # 분석 영상 프레임 수가 원본 PTS 수와 다르면 번호로 시각을 맞출 수 없다
    assert any("분석 영상 프레임 수" in e for e in errors_of(client, derivative))

    no_transform = make_payload(table)
    no_transform["metadata"]["transform"] = None
    assert any("transform" in e for e in errors_of(client, no_transform))

    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observation_sets").fetchone()[0] == 0
    assert not settings.observations_dir.exists() or not list(settings.observations_dir.rglob("*.json"))


def test_track_and_detection_frames_must_match_the_frame_times(env):
    _, client, _, _, table = env
    body = make_payload(table)
    body["tracks"][10]["timestamp_us"] += 1
    assert any("tracks[10]" in e for e in errors_of(client, body))

    body = make_payload(table)
    body["tracks"][12]["frame_index"] = 13  # 줄 순서와 frame_index가 어긋남
    assert any("frame_index" in e for e in errors_of(client, body))

    body = make_payload(table)
    body["tracks"] = body["tracks"][:-2]
    assert any("tracks 프레임 수" in e for e in errors_of(client, body))

    body = make_payload(table)
    body["detections"][5]["timestamp_us"] -= 1
    assert any("detections[5]" in e for e in errors_of(client, body))


def test_broken_boxes_scores_and_duplicate_ids_are_reported(env):
    _, client, _, _, table = env
    body = make_payload(table)
    body["tracks"][0]["tracks"][0]["bbox"] = [500, 100, 100, 700]  # left > right
    body["tracks"][1]["tracks"].append(copy.deepcopy(body["tracks"][1]["tracks"][0]))  # 같은 track_id가 두 번
    body["detections"][2]["detections"][0]["confidence"] = 1.5
    errors = errors_of(client, body)
    assert any("left < right" in e for e in errors)
    assert any("두 번 나옴" in e for e in errors)
    assert any("confidence는 0~1" in e for e in errors)


def test_detection_frames_must_be_in_the_analysis_frame(env):
    _, client, _, _, table = env
    body = make_payload(table)
    body["detections"][4]["width"] = 1920  # 분석 프레임(1280×720)이 아닌 크기
    assert any("분석 프레임" in e for e in errors_of(client, body))

    body = make_payload(table)
    body["detections"][0]["coordinate_space"] = "source_px"
    assert any("coordinate_space" in e for e in errors_of(client, body))


def test_real_pipeline_output_is_accepted_as_written(tmp_path):
    """팀 파이프라인의 실제 출력(RT-DETR R18 + ByteTrack, Pexels 36019283)의 앞 60프레임."""
    sample = json.loads((Path(__file__).parent / "fixtures" / "team_pipeline_sample.json").read_text(encoding="utf-8"))
    sample.pop("_note")
    payload = ObservationImport.model_validate(sample)
    stamps = payload.metadata.frame_timestamps_us
    assert validate([int(s) for s in stamps if s is not None], payload) == []  # 형식과 시각이 모두 맞는다
    assert payload.metadata.transform is not None and payload.metadata.transform.scale == pytest.approx(2 / 3)
    first = payload.tracks[5].tracks[0]
    assert first.tracking_state == "tracked" and first.class_name == "person" and len(first.bbox) == 4
    # 시각이 하나라도 어긋나면 거절한다
    shifted = [int(s) for s in stamps if s is not None]
    shifted[20] += 40
    assert any("프레임 20" in e for e in validate(shifted, payload))


# --- 접근 제어와 수명 ----------------------------------------------------------------------

def test_import_needs_a_ready_owned_video_with_its_file(env):
    app, client, settings, db, table = env
    assert client.get("/api/v1/videos/vid1/observations").json()["code"] == "observations_not_found"
    assert post(client, make_payload(table), "nope").status_code == 404
    add_video(db, "busy", status="processing")
    assert post(client, make_payload(table), "busy").json()["code"] == "video_not_ready"

    add_video(db, "nofile")
    assert post(client, make_payload(table), "nofile").json()["code"] == "video_media_missing"

    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    other = TestClient(app)
    assert post(other, make_payload(table)).status_code == 404
    assert other.get("/api/v1/videos/vid1/observations").status_code == 404


def test_deleting_the_video_removes_imported_results(env):
    _, client, settings, db, table = env
    post(client, make_payload(table))
    assert list((settings.observations_dir / VIDEO).glob("*.json"))
    assert client.delete("/api/v1/videos/vid1").json()["status"] == "deleted"
    assert not (settings.observations_dir / VIDEO).exists()
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observation_sets").fetchone()[0] == 0


def test_deleting_only_the_media_keeps_the_structured_results(env):
    _, client, settings, _, table = env
    post(client, make_payload(table))
    assert client.delete("/api/v1/videos/vid1/media").status_code == 200
    assert client.get("/api/v1/videos/vid1/observations").status_code == 200
    assert list((settings.observations_dir / VIDEO).glob("*.json"))


# --- 팀 파이프라인과 같은 시각·프레임률 계산 --------------------------------------------------------

def test_pts_to_us_rounds_half_up_like_the_ai_pipeline():
    assert pts_to_us(0, (1, 90000)) == 0
    assert pts_to_us(3003, (1, 90000)) == 33_367  # 33366.67 → 반올림
    assert pts_to_us(1, (1, 12800)) == 78  # 78.125
    assert pts_to_us(4, (1, 12800)) == 313  # 312.5는 올림
    assert pts_to_us(-4, (1, 12800)) == -313  # 음수도 0에서 먼 쪽으로


def test_time_base_parsing():
    assert parse_time_base("1/15360") == (1, 15360)
    for bad in (None, "", "15360", "1/0", "a/b", 5):
        assert parse_time_base(bad) is None


def test_variable_frame_rate_follows_the_average_versus_reported_rule():
    assert is_variable_frame_rate(30.0, 30.0, [33_333, 33_334]) is False
    assert is_variable_frame_rate(29.97, 29.9701, []) is False  # 상대 오차 0.1% 이내
    assert is_variable_frame_rate(24.0, 30.0, [33_333]) is True
    # 두 값을 모르면 실제 프레임 간격으로 판단한다
    assert is_variable_frame_rate(None, None, [33_333, 33_334, 33_333]) is False
    assert is_variable_frame_rate(None, 30.0, [33_333, 66_667]) is True


def to_standard_format(payload: dict) -> dict:
    """표준화될 파일 형식(`bbox_xyxy`, `score`, `tracking_score`, frame_index 없음)으로 바꾼다."""
    out = copy.deepcopy(payload)
    for frame in out["tracks"]:
        frame.pop("frame_index")
        for t in frame["tracks"]:
            t["bbox_xyxy"] = t.pop("bbox")
            t["tracking_score"] = t.pop("confidence")
    for frame in out["detections"]:
        frame.pop("frame_index")
        for i, d in enumerate(frame["detections"]):
            d["bbox_xyxy"] = d.pop("bbox")
            d["score"] = d.pop("confidence")
            d["detection_id"] = f"{frame['timestamp_us']}-{i}"
    return out


def test_the_standardized_field_names_are_accepted_and_give_the_same_track_summaries(env):
    _, client, _, _, table = env
    current = make_payload(table)
    first = post(client, current)
    assert first.status_code == 201
    standard = post(client, to_standard_format(current))
    assert standard.status_code == 201  # 줄 형식이 달라 별개의 가져오기로 저장된다(프레임 번호 필드가 없음)
    assert [t["frame_count"] for t in standard.json()["tracks"]] == [t["frame_count"] for t in first.json()["tracks"]]

    tracks = {t["track_id"]: t for t in standard.json()["tracks"]}
    assert tracks[1]["mean_score"] == pytest.approx(0.9) and tracks[2]["missing_frames"] == 10


def test_standardized_format_alone_is_validated_like_the_current_one(env):
    _, client, _, _, table = env
    body = to_standard_format(make_payload(table))
    body["tracks"][3]["timestamp_us"] += 1
    assert any("tracks[3]" in e for e in errors_of(client, body))
    body = to_standard_format(make_payload(table))
    body["tracks"][0]["tracks"][0]["bbox_xyxy"] = [500, 100, 100, 700]
    assert any("left < right" in e for e in errors_of(client, body))
