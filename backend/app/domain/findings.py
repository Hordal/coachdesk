"""주장(Finding) 문장 생성(상세 설계 14·19).

숫자는 판정에서 다시 계산한 집계에서 가져오고, 저장된 집계값과 다르면 문장을 만들지 않는다.
언어 모델을 쓰지 않으며 템플릿과 값(slots)만으로 문장을 만든다.
보류된 주장은 문장을 만들지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .aggregation import AggregateCounts, index_assessments, recompute_aggregate
from .labels import CONTACT_REGION, COUNTING_UNIT, QUESTION, REASON_PHRASE


class ConsistencyError(Exception):
    """저장된 집계와 판정에서 다시 계산한 집계가 다르다."""


# 금지 표현(02B D10의 3절). 추가·완곡 표현 목록은 전문가 작성 전이라 임시 패턴이다.
FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "official_result": re.compile(r"승리|패배|판정승|판정패|채점|득점|KO\s*승|TKO"),
    "score_as_accuracy": re.compile(r"정확도|정답\s*확률|\d\s*%"),
    "causal_from_order": re.compile(r"(?<!가림 )때문에|탓에|로 인해|원인은|그래서"),
    "injury_or_state_claim": re.compile(r"부상|골절|뇌진탕|데미지|충격을 받|피곤|지쳤|지친|두려|긴장했|겁"),
    "name_only_judgement": re.compile(r"(나쁜|좋은|잘못된|훌륭한)\s*(가드|스탠스|자세|밸런스)"),
}


class ForbiddenExpression(ConsistencyError):
    def __init__(self, rule: str, text: str) -> None:
        super().__init__(f"금지 표현({rule}): {text}")
        self.rule = rule


def forbidden_rules(text: str) -> list[str]:
    return [name for name, pattern in FORBIDDEN_PATTERNS.items() if pattern.search(text)]


@dataclass(frozen=True)
class RenderedFinding:
    template_id: str
    text: str
    slots: dict[str, Any]


def _aggregate(bundle: Mapping[str, Any], agg_id: str) -> Mapping[str, Any]:
    for g in bundle.get("aggregates", []):
        if g["aggregate_id"] == agg_id:
            return g
    raise ConsistencyError(f"집계 {agg_id}를 찾을 수 없음")


def _verified(bundle: Mapping[str, Any], agg: Mapping[str, Any]) -> AggregateCounts:
    aidx, _ = index_assessments(bundle.get("assessments", []))
    counts = recompute_aggregate(agg, aidx)
    stored = agg["counts"]
    recomputed = {
        "candidates": len(counts.candidates),
        "denominator": len(counts.denominator),
        "numerator": len(counts.numerator),
        "unknown": len(counts.unknown),
        "excluded": len(counts.excluded),
    }
    if stored != recomputed:
        raise ConsistencyError(f"{agg['aggregate_id']}: 저장된 집계 {stored}와 재계산 {recomputed}가 다름")
    return counts


def _reason_phrase(bundle: Mapping[str, Any], agg: Mapping[str, Any], member_ids: list[str]) -> str:
    """판단 불가 사유가 하나로 같을 때만 사유를 문장에 넣는다."""
    reasons = {
        a.get("reason_code")
        for a in bundle.get("assessments", [])
        if a["question_code"] == agg["question_code"] and a["subject_ref"] in member_ids
    }
    if len(reasons) == 1:
        return REASON_PHRASE.get(next(iter(reasons)), "")
    return ""


def _unit(agg: Mapping[str, Any]) -> str:
    return COUNTING_UNIT.get(agg["counting_unit"], agg["counting_unit"])


def _kick_counter_summary(bundle: Mapping[str, Any], finding: Mapping[str, Any]) -> RenderedFinding:
    refs = [_aggregate(bundle, r) for r in finding["input_refs"]]
    followed = next(g for g in refs if g["question_code"] == "counter_followed")
    contact = next(g for g in refs if g["question_code"] == "counter_contact")
    f, c = _verified(bundle, followed), _verified(bundle, contact)

    unit = _unit(followed)
    region = CONTACT_REGION.get(contact.get("contact_region_condition") or "", "")
    slots = {
        "unit": unit,
        "observed": len(f.candidates),
        "assessable": len(f.denominator),
        "counter": len(f.numerator),
        "contact": len(c.numerator),
        "contact_region": region,
        "contact_unknown": len(c.unknown),
        "unknown": len(f.unknown),
    }
    if slots["assessable"] == 0:
        text = f"확인된 {unit} {slots['observed']}회 모두 후속 상황을 판정하지 못했습니다."
        return RenderedFinding("kick_counter_summary", text, slots)

    text = (
        f"확인된 {unit} {slots['observed']}회 중 후속 상황을 판정할 수 있었던 것은 {slots['assessable']}회입니다. "
        f"그중 {slots['counter']}회 상대 반격이 이어졌으며, {slots['contact']}회는 {region} 접촉으로 연결됐습니다."
    )
    if slots["contact_unknown"]:
        text += f" 반격 중 {slots['contact_unknown']}회는 접촉 여부를 판단하지 못했습니다."
    if slots["unknown"]:
        phrase = _reason_phrase(bundle, followed, f.unknown)
        text += f" 나머지 {slots['unknown']}회는 {phrase}판단하지 못했습니다."
    return RenderedFinding("kick_counter_summary", text, slots)


def _repeated_post_action_state(bundle: Mapping[str, Any], finding: Mapping[str, Any]) -> RenderedFinding:
    agg = _aggregate(bundle, finding["input_refs"][0])
    c = _verified(bundle, agg)
    unit = _unit(agg)
    label = QUESTION.get(agg["question_code"], agg["question_code"])
    slots = {
        "unit": unit,
        "observed": len(c.candidates),
        "label": label,
        "occurred": len(c.numerator),
        "assessable": len(c.denominator),
        "unknown": len(c.unknown),
        "excluded": len(c.excluded),
    }
    if slots["assessable"] == 0:
        text = f"확인된 {unit} {slots['observed']}회 모두 {label} 여부를 판정하지 못했습니다."
        return RenderedFinding("repeated_post_action_state", text, slots)

    text = f"확인된 {unit} {slots['observed']}회 중 {label} {slots['occurred']}회가 확인되었습니다."
    if slots["unknown"] or slots["excluded"]:
        text += (
            f" 판정 가능 {slots['assessable']}회 기준이며 판단 불가 {slots['unknown']}회, "
            f"제외 {slots['excluded']}회는 비율에 포함하지 않았습니다."
        )
    return RenderedFinding("repeated_post_action_state", text, slots)


TEMPLATES: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], RenderedFinding]] = {
    "kick_counter_summary": _kick_counter_summary,
    "repeated_post_action_state": _repeated_post_action_state,
}


def render_finding(bundle: Mapping[str, Any], finding: Mapping[str, Any]) -> RenderedFinding | None:
    """보류된 주장이거나 템플릿이 없으면 None."""
    if finding.get("status") != "valid":
        return None
    template_id = finding.get("template_id")
    if template_id is None:
        return None
    template = TEMPLATES.get(template_id)
    if template is None:
        raise ConsistencyError(f"알 수 없는 템플릿: {template_id}")
    rendered = template(bundle, finding)
    hit = forbidden_rules(rendered.text)
    if hit:
        raise ForbiddenExpression(hit[0], rendered.text)
    return rendered
