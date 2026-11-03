"""규칙 기반 분석 엔진(02B R5·R7): 공격 후 가드 복귀 지연."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.analysis import AnalysisParams, derive_guard_return, judge_guard_return
from app.settings import Settings
from app.storage.db import Database, now_rfc3339

SCENARIO6 = Path(__file__).resolve().parents[2] / "Notion" / "02A_시나리오6_가상_데이터.json"
DURATION_US = 540_000_000
P = AnalysisParams()


def state(sid, label, start, end, participant="A"):
    return {"state_id": sid, "participant_id": participant, "body_part": "rear_hand", "state_category": "guard_hand", "label": label, "start_us": start, "end_us": end}


def attack(end=1_000_000):
    return {"event_id": "E1", "event_label": "CROSS", "status": "VALID", "verified": True, "participant_id": "A", "start_timestamp_us": end - 500_000, "end_timestamp_us": end}


def test_returns_immediately_is_not_delayed():
    r = judge_guard_return(attack(), [state("S1", "high_guard", 1_000_000, 1_500_000)], P)
    assert (r.value, r.delay_us) == ("absent", 0)


def test_return_after_the_limit_is_delayed():
    r = judge_guard_return(attack(), [state("S1", "low_guard", 1_000_000, 1_600_000), state("S2", "high_guard", 1_600_000, 2_000_000)], P)
    assert (r.value, r.delay_us, r.state_refs) == ("present", 600_000, ("S1", "S2"))
    early = judge_guard_return(attack(), [state("S1", "low_guard", 1_000_000, 1_300_000), state("S2", "high_guard", 1_300_000, 2_000_000)], P)
    assert (early.value, early.delay_us) == ("absent", 300_000)


def test_non_return_observed_longer_than_the_limit_is_a_lower_bound():
    r = judge_guard_return(attack(), [state("S1", "low_guard", 1_000_000, 1_500_000)], P)
    assert (r.value, r.delay_us, r.lower_bound) == ("present", 500_000, True)


def test_short_observation_without_return_is_unknown_not_zero():
    r = judge_guard_return(attack(), [state("S1", "low_guard", 1_000_000, 1_300_000)], P)
    assert (r.value, r.reason_code, r.delay_us) == ("unknown", "insufficient_observation", None)


def test_missing_states_are_unknown():
    assert judge_guard_return(attack(), [], P).reason_code == "missing_input"
    far = [state("S1", "low_guard", 5_000_000, 6_000_000)]  # 공격 직후 상태를 모름
    assert judge_guard_return(attack(), far, P).reason_code == "missing_input"
    other = [state("S1", "low_guard", 1_000_000, 2_000_000, participant="B")]  # 다른 선수의 상태는 쓰지 않음
    assert judge_guard_return(attack(), other, P).reason_code == "missing_input"


def test_derive_builds_assessments_aggregates_and_withheld_claims():
    out = derive_guard_return([attack()], [state("S1", "low_guard", 1_000_000, 1_600_000)], video_id="v", duration_us=10_000_000)
    assert [a["value"] for a in out["assessments"]] == ["present"]
    assert out["aggregates"][0]["candidate_refs"] == ["E1"] and out["aggregates"][0]["counting_unit"] == "cross_attempt"
    assert [(f["claim_type"], f["status"]) for f in out["findings"]] == [("aggregate", "valid"), ("observation", "withheld"), ("correction", "withheld")]
    assert out["evidence"][0]["start_us"] == 0 and out["evidence"][0]["end_us"] == 1_500_000  # 영상 범위로 자름


@pytest.fixture()
def env(tmp_path):
    settings = Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data")
    app = create_app(settings)
    db = Database(settings.db_path)
    with db.tx(immediate=True) as conn:
        conn.execute(
            "INSERT INTO videos (video_id, owner_id, filename, sport, source_path, size_bytes, sha256, duration_us, width, height, status, created_at) "
            "VALUES ('vid1', 'dev-user', 'a.mp4', 'boxing', 'x', 1, 'h', ?, 320, 180, 'ready', ?)",
            (DURATION_US, now_rfc3339()),
        )
    return TestClient(app)


def scenario6_input() -> dict:
    """시나리오 6 예제에서 사건·상태 구간만 남기고 판정·집계·근거·주장은 서버가 만들게 한다."""
    doc = json.loads(SCENARIO6.read_text(encoding="utf-8"))
    for key in ("schema_version", "dataset_kind", "expert_validated", "video_id", "duration_us", "sport", "time_unit", "interval_convention", "expected_summary",
                "assessments", "aggregates", "evidence", "findings"):
        doc.pop(key, None)
    doc["annotation_source"] = "labeler-a"
    doc["derive"] = ["rear_guard_return"]
    return doc


def test_derived_result_reproduces_the_scenario6_example(env):
    r = env.post("/api/v1/videos/vid1/imports", json=scenario6_input())
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    expected = json.loads(SCENARIO6.read_text(encoding="utf-8"))["expected_summary"]

    aggregates = env.get(f"/api/v1/runs/{run_id}/aggregates/AG_A_CROSS").json()["aggregate"]
    assert aggregates["counts"]["candidates"] == expected["observed_crosses"]
    assert aggregates["counts"]["numerator"] == expected["return_delayed"]
    assert aggregates["counts"]["denominator"] == expected["return_assessable"]
    assert aggregates["counts"]["unknown"] == expected["unknown"]
    assert aggregates["numerator_refs"] == ["E01", "E03", "E04", "E06"]

    derived = env.get(f"/api/v1/runs/{run_id}/findings").json()
    reference = env.get("/api/v1/runs/fixture-scenario6/findings").json()
    assert [f["text"] for f in derived] == [f["text"] for f in reference]
    assert derived[0]["text"] and derived[1]["text"] is None


def test_derive_conflicts_with_hand_written_assessments(env):
    body = scenario6_input()
    full = json.loads(SCENARIO6.read_text(encoding="utf-8"))
    body["assessments"] = [full["assessments"][0] | {"assessment_id": "AS_E01_rear_guard_return_delayed"}]
    r = env.post("/api/v1/videos/vid1/imports", json=body)
    assert r.status_code == 422 and any("중복" in e for e in r.json()["errors"])
