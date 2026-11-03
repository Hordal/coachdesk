"""팀 동작 검출 모델(phase1b_v1)의 사건 형식을 그대로 가져오는지 확인한다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.events import assign_event_ids, category_of
from app.domain.models import EventIn
from app.settings import Settings
from app.storage.db import Database, now_rfc3339

# 팀 연구 묶음의 `action_candidates.jsonl` 한 줄(클립 01, 1B)과 같은 값
TEAM_LINE = {
    "schema_version": "phase1b_v1", "event_label": "PUNCH_CANDIDATE", "status": "CANDIDATE", "verified": False, "outcome": "UNKNOWN",
    "participant_id": "candidate:0", "role": "UNRESOLVED", "track_id": 0, "arm": "left", "synthetic": False,
    "start_timestamp_us": 8299958, "end_timestamp_us": 8717042, "start_frame_index": 199, "end_frame_index": 209,
    "evidence": [{"measure": "wrist_shoulder_ratio_gain", "value": 0.7607}, {"measure": "wrist_shoulder_ratio_retract", "value": 0.3351}, {"measure": "normalization", "value": "shoulder_width"}],
    "limitations": ["HEURISTIC_NOT_A_CLASSIFIER", "NO_VERIFIED_CONTACT"], "reason_codes": ["NORMALIZED_EXTENSION_THEN_RETRACTION"],
}
# 1C에서 늘어난 필드
TEAM_LINE_1C = {
    **TEAM_LINE, "type": "PUNCH_CANDIDATE", "clip_id": "01", "mode": "IMAGE", "tracklet_ref": "track:0:t0",
    "rule_version": "phase1b_normalized_extension_v1",
    "contamination_gates": ["POSSIBLE_CROSS_PERSON_CONTAMINATION"], "evidence_path": "pose_quality_frames.jsonl",
    "input_joint_quality": "left_arm_or_right_arm VALID",
}


def test_team_line_is_accepted_as_is_and_round_trips():
    for line in (TEAM_LINE, TEAM_LINE_1C):
        event = EventIn.model_validate(line)
        dumped = event.model_dump(mode="json")
        assert {k: dumped[k] for k in line} == line  # 팀 필드는 값 그대로
        assert dumped["event_id"] is None  # 팀 형식에는 ID가 없다


def test_unknown_team_fields_are_kept():
    event = EventIn.model_validate({**TEAM_LINE, "future_field": [1, 2]})
    assert event.model_dump()["future_field"] == [1, 2]


def test_ids_are_assigned_deterministically_and_do_not_collide():
    ids = [e["event_id"] for e in assign_event_ids([TEAM_LINE, TEAM_LINE, {**TEAM_LINE, "event_id": "mine"}])]
    assert ids == ["ev_8299958_PUNCH_CANDIDATE_candidate-0", "ev_8299958_PUNCH_CANDIDATE_candidate-0_2", "mine"]
    assert assign_event_ids([TEAM_LINE])[0]["event_id"] == ids[0]


def test_category_is_derived_from_the_label():
    assert [category_of(x) for x in ("TAKEDOWN", "DIRECT_CONTACT", "PUNCH_CANDIDATE", "CROSS")] == ["ground", "contact", "action", "action"]


@pytest.fixture()
def env(tmp_path):
    settings = Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data")
    app = create_app(settings)
    db = Database(settings.db_path)
    with db.tx(immediate=True) as conn:
        conn.execute(
            "INSERT INTO videos (video_id, owner_id, filename, sport, source_path, size_bytes, sha256, duration_us, width, height, status, created_at) "
            "VALUES ('vid1', 'dev-user', 'a.mp4', 'boxing', 'x', 1, 'h', 17000000, 1920, 1080, 'ready', ?)",
            (now_rfc3339(),),
        )
    return TestClient(app)


def test_team_events_import_without_participants_or_ids(env):
    body = {"annotation_source": "team-phase1b", "definition_version": "phase1b_v1", "participants": [], "events": [TEAM_LINE, TEAM_LINE_1C | {"start_timestamp_us": 9_000_000, "end_timestamp_us": 9_300_000}]}
    r = env.post("/api/v1/videos/vid1/imports", json=body)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["counts"]["events"] == 2
    # 선수 지정 전의 후보가 UNRESOLVED 선수로 들어간다
    assert [(p["participant_id"], p["role"], p["track_ids"]) for p in run["participants"]] == [("candidate:0", "UNRESOLVED", [0])]
    event = env.get(f"/api/v1/runs/{run['run_id']}/events/ev_8299958_PUNCH_CANDIDATE_candidate-0").json()["event"]
    assert (event["event_label"], event["status"], event["verified"], event["outcome"]) == ("PUNCH_CANDIDATE", "CANDIDATE", False, "UNKNOWN")
    assert event["evidence"][0] == {"measure": "wrist_shoulder_ratio_gain", "value": 0.7607}
