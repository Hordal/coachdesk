"""선수 지정(상세 설계 11, 27의 9절)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import User, current_user
from app.domain.models import FrameTransform, ObservationImport
from app.services.designation import candidate_frames
from app.services.observations import summarize_tracks, ObservationSet

from .test_observations import VIDEO, clip, env, make_payload, post  # noqa: F401  (fixtures)


@pytest.fixture()
def imported(env):
    app, client, settings, db, table = env
    set_id = post(client, make_payload(table)).json()["set_id"]
    return app, client, set_id


def put(client, set_id, user, opponent):
    return client.put(f"/api/v1/videos/{VIDEO}/designation", json={"set_id": set_id, "user_track_ids": user, "opponent_track_ids": opponent})


# --- 후보 프레임 -----------------------------------------------------------------------

def test_candidate_frames_show_separated_candidates_in_display_coordinates(imported):
    _, client, set_id = imported
    body = client.get(f"/api/v1/videos/{VIDEO}/designation-frames").json()
    assert body["set_id"] == set_id and body["candidate_state"] == "OK"
    assert (body["display_width"], body["display_height"]) == (320, 180)
    assert 1 <= len(body["frames"]) <= 5 and all(f["separated"] for f in body["frames"])
    frame = body["frames"][0]
    boxes = {b["track_id"]: b["bbox"] for b in frame["boxes"]}
    assert {1, 2} <= set(boxes)
    # 분석 좌표(1280×720)의 [100,100,500,700]이 표시 좌표(320×180)로 되돌아온다
    assert boxes[1] == [25.0, 25.0, 125.0, 175.0]
    assert [f["frame_index"] for f in body["frames"]] == sorted(f["frame_index"] for f in body["frames"])
    assert client.get(f"/api/v1/videos/{VIDEO}/designation-frames").json() == body  # 같은 입력은 같은 프레임


def test_the_real_pipeline_sample_gives_candidate_frames():
    sample = json.loads((Path(__file__).parent / "fixtures" / "team_pipeline_sample.json").read_text(encoding="utf-8"))
    sample.pop("_note")
    payload = ObservationImport.model_validate(sample)
    transform = payload.metadata.transform
    assert transform is not None
    summaries = summarize_tracks(payload.tracks, transform)
    found = ObservationSet("s", "u", "v", "h", len(payload.tracks), 0, len(summaries), "x", {}, summaries, "t")
    frames = candidate_frames(payload.tracks, found, transform)
    assert frames and all(len(f.boxes) >= 2 for f in frames)
    assert all(0 <= b.bbox_display[0] < b.bbox_display[2] <= 1920 for f in frames for b in f.boxes)


# --- 지정 --------------------------------------------------------------------------

def test_nothing_is_assigned_until_the_user_chooses(imported):
    _, client, _ = imported
    assert client.get(f"/api/v1/videos/{VIDEO}/designation").json() == {"state": "NOT_RUN", "set_id": None, "assignments": []}
    assert client.get(f"/api/v1/videos/{VIDEO}/observations").json()["assignment_state"] == "NOT_RUN"


def test_the_user_designates_user_and_opponent_tracks(imported):
    _, client, set_id = imported
    r = put(client, set_id, [2], [1])  # 번호 순서나 화면 위치와 무관하게 사용자가 고른 대로 정해진다
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ASSIGNED" and body["set_id"] == set_id
    assert {a["role"]: (a["participant_id"], a["track_ids"]) for a in body["assignments"]} == {"USER": ("A", [2]), "OPPONENT": ("B", [1])}
    assert client.get(f"/api/v1/videos/{VIDEO}/observations").json()["assignment_state"] == "ASSIGNED"


def test_a_participant_can_have_several_tracks_when_they_never_overlap_in_time(imported):
    _, client, set_id = imported
    # 트랙 7은 앞 5프레임에만 보이고 트랙 2와 같은 프레임에 함께 보인다 → 한 선수로 묶으면 모순이다(같은 선수의 트랙은 동시에 보일 수 없다)
    r = put(client, set_id, [2, 7], [1])
    assert r.status_code == 422 and r.json()["code"] == "inconsistent_selection" and "프레임 0" in r.json()["problems"][0]
    # 트랙 7은 트랙 1과도 앞 5프레임에 함께 보이므로 상대에 1, 7을 묶어도 모순이다
    assert put(client, set_id, [2], [1, 7]).status_code == 422


def test_selection_errors_are_explicit(imported):
    _, client, set_id = imported
    assert put(client, set_id, [], [1]).json()["code"] == "selection_incomplete"
    assert put(client, set_id, [1], []).json()["code"] == "selection_incomplete"
    unknown = put(client, set_id, [1], [99])
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_track" and unknown.json()["track_ids"] == [99]
    both = put(client, set_id, [1], [1])
    assert both.status_code == 422 and both.json()["code"] == "track_in_both"
    assert put(client, set_id, [1, 1], [2]).json()["code"] == "inconsistent_selection"
    assert client.get(f"/api/v1/videos/{VIDEO}/designation").json()["state"] == "NOT_RUN"  # 실패한 선택은 저장되지 않는다


def test_same_choice_is_idempotent_and_a_change_replaces_it(imported):
    app, client, set_id = imported
    first = put(client, set_id, [1], [2]).json()
    assert put(client, set_id, [1], [2]).json() == first
    with app.state.db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0] == 1
    changed = put(client, set_id, [2], [1]).json()  # 오선택을 바꾼다
    assert {a["role"]: a["track_ids"] for a in changed["assignments"]} == {"USER": [2], "OPPONENT": [1]}
    with app.state.db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0] == 2  # 이력이 남는다


def test_a_new_import_resets_the_assignment_and_stale_selections_are_refused(imported, env):
    app, client, set_id = imported
    put(client, set_id, [1], [2])
    _, _, _, _, table = env
    other = make_payload(table)
    other["tracks"][0]["tracks"][0]["bbox"] = [101, 100, 500, 700]
    new_id = post(client, other).json()["set_id"]
    assert new_id != set_id
    assert client.get(f"/api/v1/videos/{VIDEO}/designation").json()["state"] == "NOT_RUN"  # 새 결과의 트랙 번호가 같다고 가정하지 않는다
    stale = put(client, set_id, [1], [2])
    assert stale.status_code == 409 and stale.json()["code"] == "stale_observations" and stale.json()["latest_set_id"] == new_id
    assert put(client, new_id, [1], [2]).status_code == 200


def test_too_few_candidates_cannot_be_designated(env):
    _, client, _, _, table = env
    body = make_payload(table)
    for frame in body["tracks"]:
        frame["tracks"] = [t for t in frame["tracks"] if t["track_id"] == 1]
    set_id = post(client, body).json()["set_id"]
    assert client.get(f"/api/v1/videos/{VIDEO}/designation-frames").json()["candidate_state"] == "INSUFFICIENT"
    assert put(client, set_id, [1], [2]).json()["code"] == "insufficient_candidates"


def test_solo_training_accepts_one_person_and_remains_idempotent(env):
    app, client, _, _, table = env
    payload = make_payload(table)
    for frame in payload["tracks"]:
        frame["tracks"] = [t for t in frame["tracks"] if t["track_id"] == 1]
    set_id = post(client, payload).json()["set_id"]
    request = {"set_id": set_id, "user_track_ids": [1], "opponent_track_ids": [], "mode": "solo"}
    url = f"/api/v1/videos/{VIDEO}/designation"
    response = client.put(url, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["assignments"] == [{"participant_id": "A", "role": "USER", "track_ids": [1]}]
    assert client.put(url, json=request).json() == response.json()
    with app.state.db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0] == 1
    assert client.put(url, json={**request, "user_track_ids": []}).status_code == 422
    assert client.put(url, json={**request, "user_track_ids": [99]}).json()["code"] == "unknown_track"
    assert client.put(url, json={**request, "opponent_track_ids": [1]}).json()["code"] == "solo_has_opponent"


def test_switching_solo_and_sparring_does_not_index_missing_opponent(imported):
    _, client, set_id = imported
    url = f"/api/v1/videos/{VIDEO}/designation"
    solo = {"set_id": set_id, "user_track_ids": [1], "opponent_track_ids": [], "mode": "solo"}
    assert client.put(url, json=solo).status_code == 200
    assert put(client, set_id, [1], [2]).status_code == 200
    assert client.put(url, json=solo).status_code == 200
    assert len(client.get(url).json()["assignments"]) == 1


def test_other_users_and_missing_results_get_not_found(imported, env):
    app, client, set_id = imported
    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    other = TestClient(app)
    for r in (other.get(f"/api/v1/videos/{VIDEO}/designation"), other.get(f"/api/v1/videos/{VIDEO}/designation-frames"), put(other, set_id, [1], [2])):
        assert r.status_code == 404
    assert client.get("/api/v1/videos/nope/designation").status_code == 404


def test_deleting_the_video_removes_the_designation(imported):
    app, client, set_id = imported
    put(client, set_id, [1], [2])
    assert client.delete(f"/api/v1/videos/{VIDEO}").json()["status"] == "deleted"
    with app.state.db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0] == 0
