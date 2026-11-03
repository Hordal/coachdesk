"""포즈 가져오기 → 가드 상태 → 가드 복귀 집계까지의 연결(`states_from_poses`)."""

from __future__ import annotations

from .test_poses import clip, env, post  # noqa: F401
from .test_states import pose

VIDEO = "vid1"


def pose_rows(table, low_from, back):
    """candidate:0: 오른발이 앞발이라 뒷손은 왼손. 왼손은 low_from 프레임부터 back 프레임 전까지 낮다."""
    rows = []
    for i in range(back + 10):
        r = pose(i, wrist_y=130)
        r.timestamp_us = table[i]
        r.landmarks["right_ankle"].x_px = 600.0
        if low_from <= i < back:
            r.landmarks["left_wrist"].y_px = 230.0
        d = r.model_dump(mode="json", exclude_none=True)
        d.update({"clip_id": "01", "mode": "IMAGE", "source": "candidate_crop"})
        rows.append(d)
    return rows


def attack_event(table, end_idx):
    return {"event_label": "CROSS", "status": "VALID", "verified": True, "participant_id": "candidate:0", "target_participant_id": "candidate:1",
            "start_timestamp_us": table[end_idx - 3], "end_timestamp_us": table[end_idx]}


def setup(env_):
    client, settings, db, table = env_
    low_from = 8
    end_idx = 12
    back = next(i for i in range(end_idx, len(table) - 5) if table[i] - table[end_idx] > 600_000)
    assert post(client, {"source": "t", "poses": pose_rows(table, low_from, back)}).status_code == 201
    return client, table, end_idx, back


def test_poses_become_states_and_feed_the_guard_return_rule(env):
    client, table, end_idx, back = setup(env)
    body = {"annotation_source": "t", "definition_version": "phase1b_v1", "participants": [], "events": [attack_event(table, end_idx)],
            "states_from_poses": {"opponent_direction": {"candidate:0": 1}}, "derive": ["rear_guard_return"]}
    r = client.post(f"/api/v1/videos/{VIDEO}/imports", json=body)
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    stored = client.app.state.store.get_for_owner(run_id, "dev-user")
    rear = [s for s in stored.raw["state_intervals"] if s["body_part"] == "rear_hand"]
    assert [s["label"] for s in rear] == ["high_guard", "low_guard", "high_guard"]
    assert rear[1]["end_us"] == table[back]
    (assessment,) = [a for a in stored.raw["assessments"] if a["question_code"] == "rear_guard_return_delayed"]
    assert assessment["value"] == "present" and assessment["status"] == "VALID"
    assert any("가드 상태" in n for n in stored.raw["notes"])
    assert {p["participant_id"] for p in stored.raw["participants"]} >= {"candidate:0", "candidate:1"}


def test_without_opponent_direction_rear_guard_is_unknown_not_guessed(env):
    client, table, end_idx, back = setup(env)
    body = {"annotation_source": "t", "definition_version": "phase1b_v1", "participants": [], "events": [attack_event(table, end_idx)],
            "states_from_poses": {}, "derive": ["rear_guard_return"]}
    r = client.post(f"/api/v1/videos/{VIDEO}/imports", json=body)
    assert r.status_code == 201, r.text
    stored = client.app.state.store.get_for_owner(r.json()["run_id"], "dev-user")
    assert not any(s["body_part"] == "rear_hand" for s in stored.raw["state_intervals"])
    assert {s["body_part"] for s in stored.raw["state_intervals"]} == {"left_hand", "right_hand"}
    (assessment,) = [a for a in stored.raw["assessments"] if a["question_code"] == "rear_guard_return_delayed"]
    assert (assessment["value"], assessment["reason_code"]) == ("unknown", "missing_input")  # 0·지연 없음으로 바꾸지 않는다


def test_missing_poses_and_unknown_set_are_rejected(env):
    client, settings, db, table = env
    base = {"annotation_source": "t", "definition_version": "phase1b_v1", "participants": [], "states_from_poses": {}}
    r = client.post(f"/api/v1/videos/{VIDEO}/imports", json=base)
    assert r.status_code == 422 and r.json()["code"] == "invalid_import" and "포즈가 없음" in r.json()["errors"][0]
    assert post(client, {"source": "t", "poses": pose_rows(table, 8, 24)}).status_code == 201
    r = client.post(f"/api/v1/videos/{VIDEO}/imports", json={**base, "states_from_poses": {"set_id": "nope"}})
    assert r.status_code == 422


def test_clashing_state_ids_are_rejected(env):
    client, table, end_idx, back = setup(env)
    first = client.post(f"/api/v1/videos/{VIDEO}/imports", json={"annotation_source": "t", "definition_version": "x", "participants": [], "states_from_poses": {}}).json()
    stored = client.app.state.store.get_for_owner(first["run_id"], "dev-user")
    given = stored.raw["state_intervals"][0]
    body = {"annotation_source": "t", "definition_version": "x", "participants": [{"participant_id": "candidate:0", "role": "UNRESOLVED"}],
            "state_intervals": [{k: given[k] for k in ("state_id", "participant_id", "body_part", "state_category", "label", "start_us", "end_us")}],
            "states_from_poses": {}}
    r = client.post(f"/api/v1/videos/{VIDEO}/imports", json=body)
    assert r.status_code == 422 and "겹침" in r.json()["errors"][0]
