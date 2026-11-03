"""사용자 정정, 오류 신고, 의도 설명(상세 설계 11, 16)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import User, current_user
from app.api.main import create_app
from app.settings import Settings
from app.storage.db import Database

from .test_manual_import import add_video, payload


@pytest.fixture()
def env(tmp_path):
    settings = Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data")
    app = create_app(settings)
    client = TestClient(app)
    db = Database(settings.db_path)
    add_video(db, "vid1")
    run_id = client.post("/api/v1/videos/vid1/imports", json=payload()).json()["run_id"]
    return app, client, db, run_id


def roles(client, run_id) -> dict[str, tuple[str, bool]]:
    return {p["participant_id"]: (p["role"], p["user_designated"]) for p in client.get(f"/api/v1/runs/{run_id}").json()["participants"]}


def designate(client, run_id, who):
    return client.post(f"/api/v1/runs/{run_id}/corrections", json={"user_participant_id": who})


# --- 본인 지정 정정 -------------------------------------------------------------------

def test_preview_shows_changes_without_saving(env):
    _, client, _, run_id = env
    before = len(client.get("/api/v1/runs").json())
    r = client.post(f"/api/v1/runs/{run_id}/corrections/preview", json={"user_participant_id": "B"})
    assert r.status_code == 200
    body = r.json()
    assert {(c["participant_id"], c["role_before"], c["role_after"]) for c in body["changes"]} == {("A", "USER", "OPPONENT"), ("B", "OPPONENT", "USER")}
    assert body["recalculation"] == "not_required"
    assert len(client.get("/api/v1/runs").json()) == before


def test_correction_creates_a_new_run_and_preserves_the_original(env):
    _, client, _, run_id = env
    assert roles(client, run_id) == {"A": ("USER", True), "B": ("OPPONENT", False)}

    r = designate(client, run_id, "B")
    assert r.status_code == 201
    new = r.json()
    assert new["run_id"] != run_id and new["parent_run_id"] == run_id and new["superseded_by"] is None
    assert roles(client, new["run_id"]) == {"A": ("OPPONENT", False), "B": ("USER", True)}

    assert roles(client, run_id) == {"A": ("USER", True), "B": ("OPPONENT", False)}  # 최초 결과는 그대로
    assert client.get(f"/api/v1/runs/{run_id}").json()["superseded_by"] == new["run_id"]

    # 집계와 문장은 선수 번호 기준이라 같다
    old_f = client.get(f"/api/v1/runs/{run_id}/findings").json()
    new_f = client.get(f"/api/v1/runs/{new['run_id']}/findings").json()
    assert [f["text"] for f in old_f] == [f["text"] for f in new_f]
    assert client.get(f"/api/v1/videos/vid1/summary").json()["run_id"] == new["run_id"]


def test_history_lists_the_chain_from_any_run(env):
    _, client, _, run_id = env
    second = designate(client, run_id, "B").json()["run_id"]
    third = designate(client, second, "A").json()["run_id"]
    for start in (run_id, second, third):
        h = client.get(f"/api/v1/runs/{start}/corrections").json()
        assert h["original_run_id"] == run_id and h["latest_run_id"] == third
        assert [(e["from_run_id"], e["run_id"]) for e in h["entries"]] == [(run_id, second), (second, third)]
    assert roles(client, third) == {"A": ("USER", True), "B": ("OPPONENT", False)}  # 오선택을 되돌릴 수 있다


def test_correcting_an_already_corrected_run_is_a_conflict_with_the_latest_id(env):
    _, client, _, run_id = env
    second = designate(client, run_id, "B").json()["run_id"]
    stale = designate(client, run_id, "B")
    assert stale.status_code == 409 and stale.json()["code"] == "stale_run" and stale.json()["latest_run_id"] == second
    assert len([r for r in client.get("/api/v1/runs").json() if r.get("parent_run_id") == run_id]) == 1  # 중복 제출로 실행이 늘지 않는다


def test_same_designation_is_rejected_and_unknown_participant_is_invalid(env):
    _, client, _, run_id = env
    same = designate(client, run_id, "A")
    assert same.status_code == 409 and same.json()["code"] == "no_change"
    unknown = designate(client, run_id, "Z")
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_participant"
    assert client.post(f"/api/v1/runs/{run_id}/corrections", json={"kind": "round_boundary"}).status_code == 422  # 아직 지원하지 않는 정정


def test_unanalyzed_run_has_nothing_to_designate(env, tmp_path):
    _, client, db, _ = env
    from app.storage.memory import RunStore, StoredRun
    from app.domain.models import ResultBundle
    from app.pipeline.registry import RunMode

    raw = {
        "schema_version": "0.1", "dataset_kind": "unanalyzed", "annotation_source": "none", "expert_validated": False,
        "definition_version": "none", "video_id": "vid1", "duration_us": 1000, "sport": "boxing", "time_unit": "us",
        "interval_convention": "[start,end)", "participants": [],
    }
    RunStore(db).add_persistent(StoredRun("empty-run", "dev-user", RunMode.FULL, "partial", ResultBundle.model_validate(raw), raw))
    r = designate(client, "empty-run", "A")
    assert r.status_code == 422 and r.json()["code"] == "nothing_to_designate"


def test_fixture_runs_can_be_corrected_too_and_the_fixture_is_untouched(env):
    _, client, _, _ = env
    r = designate(client, "fixture-counter", "B")
    assert r.status_code == 201
    assert roles(client, "fixture-counter") == {"A": ("USER", True), "B": ("OPPONENT", False)}


def test_other_users_get_not_found(env):
    app, _, _, run_id = env
    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    other = TestClient(app)
    for r in (
        designate(other, run_id, "B"),
        other.post(f"/api/v1/runs/{run_id}/corrections/preview", json={"user_participant_id": "B"}),
        other.get(f"/api/v1/runs/{run_id}/corrections"),
        other.post(f"/api/v1/runs/{run_id}/reports", json={"category": "other", "text": "x"}),
        other.get(f"/api/v1/runs/{run_id}/reports"),
        other.post(f"/api/v1/runs/{run_id}/context-notes", json={"text": "x"}),
    ):
        assert r.status_code == 404


# --- 신고·의도 설명 ---------------------------------------------------------------------

def test_reports_are_stored_without_changing_the_result(env):
    _, client, _, run_id = env
    before = client.get(f"/api/v1/runs/{run_id}").json()
    r = client.post(f"/api/v1/runs/{run_id}/reports", json={"category": "technique", "subject_ref": "E01", "text": "이 장면은 킥이 아니라 푸시입니다"})
    assert r.status_code == 201 and r.json()["category"] == "technique"
    listed = client.get(f"/api/v1/runs/{run_id}/reports").json()
    assert [x["subject_ref"] for x in listed] == ["E01"]
    assert client.get(f"/api/v1/runs/{run_id}").json() == before  # 신고만으로 계산이 바뀌지 않는다
    assert client.get(f"/api/v1/runs/{run_id}/aggregates/AG01").json()["aggregate"]["counts"]["numerator"] == 3


def test_report_validation(env):
    _, client, _, run_id = env
    base = f"/api/v1/runs/{run_id}/reports"
    assert client.post(base, json={"category": "bogus", "text": "x"}).status_code == 422
    assert client.post(base, json={"category": "other", "text": ""}).status_code == 422
    assert client.post(base, json={"category": "other", "text": "x" * 2001}).status_code == 422


def test_context_notes_are_kept_separate_and_validated(env):
    _, client, _, run_id = env
    base = f"/api/v1/runs/{run_id}/context-notes"
    ok = client.post(base, json={"participant_id": "A", "start_us": 9_000_000, "end_us": 10_000_000, "text": "일부러 거리를 줄였다"})
    assert ok.status_code == 201 and ok.json()["text"] == "일부러 거리를 줄였다"
    assert client.post(base, json={"text": "전체에 대한 설명"}).status_code == 201
    assert len(client.get(base).json()) == 2
    assert client.post(base, json={"start_us": 5, "text": "x"}).json()["code"] == "invalid_range"  # 시작만 있는 구간
    assert client.post(base, json={"start_us": 9, "end_us": 3, "text": "x"}).json()["code"] == "invalid_range"
    assert client.post(base, json={"start_us": 0, "end_us": 10**12, "text": "x"}).json()["code"] == "invalid_range"
    assert client.post(base, json={"participant_id": "Z", "text": "x"}).json()["code"] == "unknown_participant"
    assert client.get(f"/api/v1/runs/{run_id}/findings").status_code == 200  # 관측 결과와 섞이지 않는다
