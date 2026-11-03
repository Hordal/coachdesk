"""4단계: R7 일반화(밸런스 회복), R8 중요 장면, 금지 표현 검사."""

from __future__ import annotations

import pytest

from app.domain.aggregation import index_assessments, recompute_aggregate
from app.domain.findings import FORBIDDEN_PATTERNS, ForbiddenExpression, forbidden_rules, render_finding
from app.domain.relations import CounterLink, derive_balance_recovery
from app.domain.scenes import SceneParams, select_scenes

S = 1_000_000


def atk(eid, start, pid="A", label="CROSS"):
    return {"event_id": eid, "event_label": label, "status": "VALID", "verified": True, "participant_id": pid, "target_participant_id": "B", "start_timestamp_us": start, "end_timestamp_us": start + 500_000}


def bal(i, label, s, e):
    return {"state_id": i, "participant_id": "A", "state_category": "balance", "label": label, "start_us": s, "end_us": e}


def test_balance_recovery_aggregate_reuses_generalised_r7():
    events = [atk("e1", 10 * S), atk("e2", 20 * S), atk("e3", 30 * S)]
    states = [
        bal("b1", "unstable", 10 * S + 500_000, 11 * S + 500_000), bal("b2", "stable", 11 * S + 500_000, 14 * S),  # e1: 1초 지연
        bal("b3", "stable", 20 * S + 500_000, 24 * S),  # e2: 지연 없음
    ]  # e3: 상태 없음 → 판단 불가
    out = derive_balance_recovery(events, states, video_id="v", duration_us=60 * S)
    values = {a["subject_ref"]: a["value"] for a in out["assessments"]}
    assert values == {"e1": "present", "e2": "absent", "e3": "unknown"}
    assert {a["question_code"] for a in out["assessments"]} == {"balance_recovery_delayed"}
    agg = out["aggregates"][0]
    assert agg["question_code"] == "balance_recovery_delayed" and "밸런스 회복 지연" in agg["description"]
    aidx, _ = index_assessments(out["assessments"])
    counts = recompute_aggregate({**agg, "counts": None}, aidx)
    assert (len(counts.numerator), len(counts.denominator), len(counts.unknown)) == (1, 2, 1)
    assert [f["status"] for f in out["findings"]] == ["valid", "withheld", "withheld"]


def test_scenes_rank_and_withhold():
    contact = {"event_id": "c1", "event_label": "DIRECT_CONTACT", "status": "VALID", "verified": True, "participant_id": "B", "target_participant_id": "A", "start_timestamp_us": 5 * S,
               "end_timestamp_us": 5 * S + 40_000, "source_surface": "glove", "target_surface": "head"}
    guard_contact = {**contact, "event_id": "c2", "start_timestamp_us": 7 * S, "end_timestamp_us": 7 * S + 40_000, "target_surface": "glove"}
    late = atk("e1", 3 * S)
    assessments = [{"assessment_id": "AS1", "subject_ref": "e1", "question_code": "balance_recovery_delayed", "value": "present", "note": "회복 지연 900ms"},
                   {"assessment_id": "AS2", "subject_ref": "e1", "question_code": "rear_guard_return_delayed", "value": "present"}]
    got = select_scenes([contact, guard_contact, late], assessments, counters=[CounterLink("e1", "d1", "e9", "COUNTER"), CounterLink("e1", "d1", "e8", "EXCHANGE")], duration_us=60 * S)
    assert [(s["rank"], s["scene_type"]) for s in got["scenes"]] == [(1, "balance_recovery_delayed"), (2, "hit_received")]  # 가드 접촉·가드 지연은 후보 아님
    assert got["scenes"][1]["evidence_start_us"] == 4 * S and got["scenes"][1]["participant_id"] == "A"
    assert got["withheld"] == [{"scene_type": "decisive_counter", "subject_ref": "e9", "reason_code": "counter_rule_unvalidated"}]
    limited = select_scenes([contact, late], assessments, duration_us=60 * S, params=SceneParams(max_scenes=1))
    assert len(limited["scenes"]) == 1 and limited["withheld"][0]["reason_code"] == "over_limit"


@pytest.mark.parametrize("text,rule", [
    ("A 선수가 판정승을 거두었습니다", "official_result"),
    ("검출 정확도 92%", "score_as_accuracy"),
    ("가드가 내려가 있었기 때문에 맞았습니다", "causal_from_order"),
    ("피로로 지쳤습니다", "injury_or_state_claim"),
    ("나쁜 가드였습니다", "name_only_judgement"),
])
def test_forbidden_expressions_are_detected(text, rule):
    assert rule in forbidden_rules(text) and rule in FORBIDDEN_PATTERNS


def test_templates_pass_and_unsafe_template_output_is_rejected(monkeypatch):
    ok = "확인된 크로스 6회 중 공격 후 뒷손 가드 복귀 지연 4회가 확인되었습니다. 판정 가능 5회 기준이며 판단 불가 1회, 제외 0회는 비율에 포함하지 않았습니다."
    assert forbidden_rules(ok) == []
    from app.domain import findings
    from app.domain.findings import RenderedFinding

    monkeypatch.setitem(findings.TEMPLATES, "bad", lambda b, f: RenderedFinding("bad", "가드 때문에 맞았습니다", {}))
    with pytest.raises(ForbiddenExpression):
        render_finding({}, {"status": "valid", "template_id": "bad"})
