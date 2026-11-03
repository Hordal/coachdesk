"""규칙 기반 분석 엔진(02B). 구현된 규칙: R5 공격 후 가드 복귀, R7 반복 집계, 주장 정의.

입력은 사건(팀 동작 검출 모델 형식)과 상태 구간이다. 출력은 가져오기 입력에 더할 판정·근거·집계 정의·주장 정의다.
집계의 분자·분모는 여기서 만들지 않고, 가져오기가 판정에서 다시 계산한다(14).
임시 매개변수는 25의 4-2절 값(team-provisional-v1)이며 전문가 확정 전 값이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .events import DEFENSE_LABELS, category_of
from .policy import CONFIRMED, event_state

QUESTION_GUARD_RETURN = "rear_guard_return_delayed"
GUARD_HIGH_LABELS = frozenset({"high_guard"})  # 가드 복귀로 보는 상태 라벨(D02 확정 전 임시)
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class AnalysisParams:
    guard_late_us: int = 400_000  # 가드 복귀 '늦음' 기준(25의 4-2절)
    guard_window_us: int = 1_500_000  # 공격 종료 뒤 복귀를 기다리는 최대 시간
    evidence_before_us: int = 1_000_000  # 근거 장면: 공격 시작 전
    evidence_after_us: int = 500_000  # 근거 장면: 공격 종료 후
    rule_version: str = "team-provisional-v1"


@dataclass(frozen=True)
class GuardReturn:
    value: str  # present(복귀 지연) | absent | unknown
    reason_code: str | None
    delay_us: int | None
    state_refs: tuple[str, ...]
    lower_bound: bool = False  # 복귀를 보지 못해 지연이 하한값일 때


def attacks(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """행동 사건 중 선수가 있는 것(공격 시도·후보). 접촉·그라운드·방어 사건은 제외한다."""
    return [e for e in events if category_of(e["event_label"]) == "action" and e["event_label"].upper() not in DEFENSE_LABELS and e.get("participant_id")]


def _rear_guard_states(states: Iterable[Mapping[str, Any]], participant_id: str) -> list[Mapping[str, Any]]:
    own = [
        s for s in states
        if s["participant_id"] == participant_id and s["state_category"] == "guard_hand" and s.get("body_part") == "rear_hand"
    ]
    return sorted(own, key=lambda s: s["start_us"])


def judge_guard_return(attack: Mapping[str, Any], states: Sequence[Mapping[str, Any]], params: AnalysisParams) -> GuardReturn:
    """공격 종료 뒤 뒷손 가드가 기준 자세(하이가드)로 돌아오기까지의 지연이 기준을 넘는지 판정한다(R5).

    복귀를 못 봤어도 기준보다 오래 비복귀 상태를 관측했으면 지연(하한)으로 본다. 관측이 기준에 못 미치면 판단 불가다.
    """
    return judge_recovery(attack, _rear_guard_states(states, attack["participant_id"]), GUARD_HIGH_LABELS, params)


def judge_recovery(attack: Mapping[str, Any], own_states: Sequence[Mapping[str, Any]], ok_labels: frozenset[str], params: AnalysisParams) -> GuardReturn:
    """공격 종료 뒤 상태가 기준 자세(ok_labels)로 돌아오기까지의 지연 판정. 가드 복귀(R5)와 밸런스 회복(R6)이 같은 구조를 쓴다."""
    end = attack["end_timestamp_us"]
    seq = [s for s in sorted(own_states, key=lambda s: s["start_us"]) if s["end_us"] > end and s["start_us"] < end + params.guard_window_us]
    if not seq:
        return GuardReturn("unknown", "missing_input", None, ())
    first = seq[0]
    if first["start_us"] > end + params.guard_late_us:  # 공격 직후의 상태를 모른다
        return GuardReturn("unknown", "missing_input", None, ())
    refs = [first["state_id"]]
    lower_bound = False
    if first.get("label") in ok_labels:
        delay = max(0, first["start_us"] - end)
    else:
        back = next((s for s in seq[1:] if s.get("label") in ok_labels), None)
        if back is not None:
            delay = back["start_us"] - end
            refs.append(back["state_id"])
        else:  # 복귀를 보지 못함: 이어서 관측한 구간만큼이 지연의 하한
            covered = end
            for s in seq:
                if s["start_us"] <= covered + params.guard_late_us:
                    covered = max(covered, s["end_us"])
                    if s["state_id"] not in refs:
                        refs.append(s["state_id"])
                else:
                    break
            delay = covered - end
            lower_bound = True
            if delay <= params.guard_late_us:
                return GuardReturn("unknown", "insufficient_observation", None, tuple(refs))
    value = "present" if delay > params.guard_late_us else "absent"
    return GuardReturn(value, None, delay, tuple(refs), lower_bound)


def _slug(text: str) -> str:
    return _SAFE.sub("-", text).strip("-") or "x"


def _counting_unit(event_label: str) -> str:
    base = event_label.lower()
    return base if base.endswith("_attempt") else f"{base}_attempt"


def derive_guard_return(
    events: Sequence[Mapping[str, Any]],
    states: Sequence[Mapping[str, Any]],
    *,
    video_id: str,
    duration_us: int,
    params: AnalysisParams = AnalysisParams(),
) -> dict[str, list[dict[str, Any]]]:
    """공격마다 가드 복귀 지연을 판정하고 선수·기술별 반복 집계와 주장 정의를 만든다(R5+R7).

    주장: 집계 관찰(템플릿 문장), 반복 문제 판정(검토된 규칙 없음 → 보류), 교정(보류). 02A 시나리오 6의 구성과 같다.
    """
    return derive_post_action(
        events, states, video_id=video_id, duration_us=duration_us, params=params,
        question_code=QUESTION_GUARD_RETURN, judge=judge_guard_return, subject="뒷손 가드 복귀 지연",
    )


def derive_post_action(
    events: Sequence[Mapping[str, Any]],
    states: Sequence[Mapping[str, Any]],
    *,
    video_id: str,
    duration_us: int,
    params: AnalysisParams,
    question_code: str,
    judge: Any,
    subject: str,
) -> dict[str, list[dict[str, Any]]]:
    """R7 일반형. 공격 뒤 상태 판정(`judge(attack, states, params) -> GuardReturn`)을 선수·기술별로 모아 집계·주장 정의를 만든다."""
    assessments: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for a in (x for x in attacks(events) if event_state(x) == CONFIRMED):  # 후보·상태 불명 사건은 집계에 넣지 않는다(policy)
        result = judge(a, states, params)
        assessments.append(
            {
                "assessment_id": f"AS_{a['event_id']}_{question_code}",
                "subject_ref": a["event_id"],
                "question_code": question_code,
                "value": result.value,
                "status": "VALID" if result.value in ("present", "absent") else "UNKNOWN",
                "reason_code": result.reason_code,
                "contact_region": None,
                "input_refs": list(result.state_refs),
                "note": None if result.delay_us is None else f"복귀 지연 {result.delay_us // 1000}ms" + (" 이상(복귀 미관측)" if result.lower_bound else ""),
            }
        )
        evidence.append(
            {
                "evidence_id": f"EV_{a['event_id']}",
                "video_id": video_id,
                "start_us": max(0, a["start_timestamp_us"] - params.evidence_before_us),
                "end_us": min(duration_us, a["end_timestamp_us"] + params.evidence_after_us),
                "participant_refs": [a["participant_id"]],
                "availability": "available",
            }
        )
        groups.setdefault((a["participant_id"], a["event_label"]), []).append(a)

    aggregates: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    verdict = {x["subject_ref"]: x["value"] for x in assessments}
    for (participant, label), members in groups.items():
        key = f"{_slug(participant)}_{_slug(label)}"
        agg_id = f"AG_{key}"
        refs = [m["event_id"] for m in members]
        aggregates.append(
            {
                "aggregate_id": agg_id,
                "description": f"확인된 {participant} {label} 중 공격 후 {subject}이 판정된 비율",
                "counting_unit": _counting_unit(label),
                "scope": {"video_id": video_id, "participant_id": participant, "interval": "whole_video"},
                "condition_refs": [],
                "question_code": question_code,
                "candidate_refs": refs,
            }
        )
        delayed = [r for r in refs if verdict[r] == "present"]
        findings += [
            {
                "finding_id": f"F_{key}_observation",
                "claim_type": "aggregate",
                "template_id": "repeated_post_action_state",
                "input_refs": [agg_id],
                "evidence_refs": [f"EV_{r}" for r in refs],
                "rule_ref": None,
                "status": "valid",
                "hold_reason": None,
            },
            {
                "finding_id": f"F_{key}_repeated_problem",
                "claim_type": "observation",
                "template_id": "repeated_problem",
                "input_refs": [agg_id],
                "evidence_refs": [f"EV_{r}" for r in delayed],
                "rule_ref": None,
                "status": "withheld",
                "hold_reason": "repeated_problem_rule_unvalidated",
            },
            {
                "finding_id": f"F_{key}_correction",
                "claim_type": "correction",
                "template_id": None,
                "input_refs": [agg_id],
                "evidence_refs": [],
                "rule_ref": None,
                "status": "withheld",
                "hold_reason": "correction_rule_unvalidated",
            },
        ]
    return {"assessments": assessments, "evidence": evidence, "aggregates": aggregates, "findings": findings}
