"""문장 생성: 숫자는 판정에서 재계산하고 보류된 주장은 문장을 만들지 않는다."""

from __future__ import annotations

import copy
import json

import pytest

from app.domain.findings import ConsistencyError, render_finding
from app.settings import Settings

RUNS = Settings().resolved_fixture_runs()


def load(run_id: str) -> dict:
    return json.loads(RUNS[run_id].read_text(encoding="utf-8"))


def finding(bundle: dict, finding_id: str) -> dict:
    return next(f for f in bundle["findings"] if f["finding_id"] == finding_id)


def test_kick_counter_summary_text_and_slots():
    bundle = load("fixture-counter")
    r = render_finding(bundle, finding(bundle, "F01"))
    assert r.text == (
        "확인된 발차기 5회 중 후속 상황을 판정할 수 있었던 것은 4회입니다. "
        "그중 3회 상대 반격이 이어졌으며, 2회는 머리 접촉으로 연결됐습니다. "
        "나머지 1회는 가림 때문에 판단하지 못했습니다."
    )
    assert r.slots["observed"] == 5 and r.slots["assessable"] == 4
    assert r.slots["counter"] == 3 and r.slots["contact"] == 2 and r.slots["unknown"] == 1


def test_scenario6_text():
    bundle = load("fixture-scenario6")
    r = render_finding(bundle, finding(bundle, "F01"))
    assert r.text == "확인된 크로스 6회 중 공격 후 뒷손 가드 복귀 지연 4회가 확인되었습니다."


def test_unknown_cross_is_reported_not_zero():
    bundle = load("fixture-scenario6")
    bundle = copy.deepcopy(bundle)
    a = next(x for x in bundle["assessments"] if x["assessment_id"] == "AS03")
    a.update(value="unknown", status="UNKNOWN", reason_code="occluded")
    agg = bundle["aggregates"][0]
    agg.update(
        denominator_refs=["E01", "E02", "E04", "E05", "E06"],
        numerator_refs=["E01", "E04", "E06"],
        unknown_refs=["E03"],
        counts={"candidates": 6, "denominator": 5, "numerator": 3, "unknown": 1, "excluded": 0},
        ratio=0.6,
    )
    r = render_finding(bundle, finding(bundle, "F01"))
    assert "3회가 확인되었습니다" in r.text
    assert "판정 가능 5회 기준이며 판단 불가 1회" in r.text


def test_withheld_findings_have_no_text():
    bundle = load("fixture-counter")
    assert render_finding(bundle, finding(bundle, "F02")) is None  # 교정, 보류
    assert render_finding(bundle, finding(bundle, "F03")) is None  # 거리 없음, 보류


def test_stored_count_mismatch_blocks_the_sentence():
    bundle = copy.deepcopy(load("fixture-counter"))
    bundle["aggregates"][0]["counts"]["numerator"] = 4
    with pytest.raises(ConsistencyError):
        render_finding(bundle, finding(bundle, "F01"))


def test_no_assessable_samples_gives_no_ratio_sentence():
    bundle = copy.deepcopy(load("fixture-scenario6"))
    for a in bundle["assessments"]:
        a.update(value="unknown", status="UNKNOWN", reason_code="occluded")
    agg = bundle["aggregates"][0]
    agg.update(
        denominator_refs=[],
        numerator_refs=[],
        unknown_refs=agg["candidate_refs"],
        counts={"candidates": 6, "denominator": 0, "numerator": 0, "unknown": 6, "excluded": 0},
        ratio=None,
    )
    r = render_finding(bundle, finding(bundle, "F01"))
    assert r.text == "확인된 크로스 6회 모두 공격 후 뒷손 가드 복귀 지연 여부를 판정하지 못했습니다."
