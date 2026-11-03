"""중요 장면 선정(02B R8, D09). '큰'·'명확한'·'결정적'의 기준은 전문가 확정 전이라 사실 조건만으로 후보를 만든다.

후보는 결과 주장이 아니라 복기 후보다. 순위: 직후 영향 > 누적, 같은 순위는 시간순. 근거 장면 구간을 함께 낸다.
반격은 판정 기준(D06) 확정 전이라 후보로 만들지 않고 보류 목록에 사유와 함께 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .events import category_of
from .policy import CONFIRMED, event_state
from .relations import QUESTION_BALANCE_RECOVERY, CounterLink

HIT_SURFACES = frozenset({"head", "body", "leg"})
IMMEDIATE = 0
CUMULATIVE = 1


@dataclass(frozen=True)
class SceneParams:
    max_scenes: Optional[int] = None
    evidence_before_us: int = 1_000_000
    evidence_after_us: int = 500_000
    rule_version: str = "team-provisional-v1"


def select_scenes(
    events: Sequence[Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
    *,
    counters: Sequence[CounterLink] = (),
    duration_us: int,
    params: SceneParams = SceneParams(),
) -> dict[str, list[dict[str, Any]]]:
    """중요 장면 후보와 보류 목록을 돌려준다. 접촉은 부위가 머리·몸통·다리로 확인된 것만 '피격 후보'로 본다."""
    by_id = {e["event_id"]: e for e in events}
    scenes: list[dict[str, Any]] = []

    def add(kind: str, subject: str, start: int, end: int, tier: int, basis: dict[str, Any], participant: Optional[str]) -> None:
        scenes.append({
            "scene_id": f"SC_{kind}_{subject}", "scene_type": kind, "subject_ref": subject, "participant_id": participant,
            "tier": tier, "start_us": start, "end_us": end, "basis": basis,
            "evidence_start_us": max(0, start - params.evidence_before_us), "evidence_end_us": min(duration_us, end + params.evidence_after_us),
            "status": "candidate", "rule_version": params.rule_version,
        })

    for e in events:
        if category_of(e["event_label"]) == "contact" and event_state(e) == CONFIRMED and e.get("target_surface") in HIT_SURFACES:
            add("hit_received", e["event_id"], e["start_timestamp_us"], e["end_timestamp_us"], IMMEDIATE,
                {"target_surface": e["target_surface"], "source_surface": e.get("source_surface")}, e.get("target_participant_id"))
    for a in assessments:
        if a["question_code"] == QUESTION_BALANCE_RECOVERY and a["value"] == "present" and a["subject_ref"] in by_id:
            atk = by_id[a["subject_ref"]]
            add("balance_recovery_delayed", atk["event_id"], atk["start_timestamp_us"], atk["end_timestamp_us"], IMMEDIATE,
                {"assessment_id": a["assessment_id"], "note": a.get("note")}, atk.get("participant_id"))
    scenes.sort(key=lambda s: (s["tier"], s["start_us"], s["scene_id"]))
    withheld = [
        {"scene_type": "decisive_counter", "subject_ref": c.reply_id, "reason_code": "counter_rule_unvalidated"}
        for c in counters if c.kind == "COUNTER"
    ]
    if params.max_scenes is not None and len(scenes) > params.max_scenes:
        withheld += [{"scene_type": s["scene_type"], "subject_ref": s["subject_ref"], "reason_code": "over_limit"} for s in scenes[params.max_scenes:]]
        scenes = scenes[: params.max_scenes]
    for rank, s in enumerate(scenes, 1):
        s["rank"] = rank
    return {"scenes": scenes, "withheld": withheld}
