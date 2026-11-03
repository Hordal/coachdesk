"""관계 기반 판정(02B): R1 방어 연결, R3 반격·교환, R4 거리 결과, R6 밸런스 회복.

입력은 사건(팀 동작 검출 모델 형식)·상태 구간·관측 구간이다. 관측하지 못한 것은 '없음'이 아니라 판단 불가로 둔다.
방어·회피 동작 라벨은 팀 모델에 아직 없어 임시 집합이며, 팀 라벨이 정해지면 바꾼다. 매개변수는 02B 3.2의 시작값(team-provisional-v1)이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .analysis import AnalysisParams, GuardReturn, attacks, derive_post_action, judge_recovery
from .policy import CONFIRMED, event_state
from .events import BLOCK_LABELS, DEFENSE_LABELS, EVADE_LABELS  # noqa: F401

BALANCE_STABLE_LABELS = frozenset({"stable"})  # 임시: D04 확정 전
QUESTION_BALANCE_RECOVERY = "balance_recovery_delayed"


@dataclass(frozen=True)
class RelationParams:
    delta_link_us: int = 200_000
    delta_counter_us: int = 600_000
    epsilon_before_us: int = 200_000
    epsilon_after_us: int = 300_000
    tau_d: float = 0.03  # 박스 높이 h 단위
    max_sample_gap_us: int = 200_000  # 거리 표본이 기준 시점에서 이보다 멀면 값이 없는 것으로 본다
    rule_version: str = "team-provisional-v1"


@dataclass(frozen=True)
class DefenseLink:
    attack_id: str
    defender: Optional[str]
    value: str  # linked | none | unknown
    defense_id: Optional[str] = None
    auxiliary_ids: tuple[str, ...] = ()
    reason_code: Optional[str] = None


def defenses(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [e for e in events if e["event_label"].upper() in DEFENSE_LABELS and e.get("participant_id")]


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _covered(observed: Mapping[str, Sequence[tuple[int, int]]] | None, pid: str, start: int, end: int) -> bool:
    if observed is None:
        return False
    return any(s <= start and end <= e for s, e in observed.get(pid, ()))


def link_defenses(
    events: Sequence[Mapping[str, Any]],
    *,
    observed: Mapping[str, Sequence[tuple[int, int]]] | None = None,
    params: RelationParams = RelationParams(),
) -> list[DefenseLink]:
    """R1. 공격마다 상대의 방어 동작을 잇는다.

    `observed`(선수 → 관측한 시간 구간)가 연결 창을 덮을 때만 '없음'이다. 덮지 못하면 판단 불가다.
    방어 동작이 여럿이면 가장 많이 겹치는 것이 주 연결이고 나머지는 보조다.
    """
    defs = defenses(events)
    out: list[DefenseLink] = []
    for a in attacks(events):
        b = a.get("target_participant_id")
        if not b:
            out.append(DefenseLink(a["event_id"], None, "unknown", reason_code="target_missing"))
            continue
        if event_state(a) != CONFIRMED:
            out.append(DefenseLink(a["event_id"], b, "unknown", reason_code="attack_unknown" if a.get("status") == "UNKNOWN" else "candidate_event"))
            continue
        w0, w1 = a["start_timestamp_us"] - params.delta_link_us, a["end_timestamp_us"] + params.delta_link_us
        near = [d for d in defs if d["participant_id"] == b and d["start_timestamp_us"] < w1 and d["end_timestamp_us"] > w0]
        cands = [d for d in near if event_state(d) == CONFIRMED]
        if len(cands) < len(near) and not cands:  # 확인되지 않은 방어 후보만 있으면 '없음'이라 할 수 없다
            out.append(DefenseLink(a["event_id"], b, "unknown", reason_code="defense_candidate_unconfirmed"))
        elif cands:
            cands.sort(key=lambda d: (-_overlap(d["start_timestamp_us"], d["end_timestamp_us"], a["start_timestamp_us"], a["end_timestamp_us"]), d["start_timestamp_us"]))
            out.append(DefenseLink(a["event_id"], b, "linked", cands[0]["event_id"], tuple(d["event_id"] for d in cands[1:])))
        elif _covered(observed, b, w0, w1):
            out.append(DefenseLink(a["event_id"], b, "none"))  # 관측했고 방어 동작이 없었음
        else:
            out.append(DefenseLink(a["event_id"], b, "unknown", reason_code="unobserved"))
    return out


@dataclass(frozen=True)
class CounterLink:
    attack_id: str
    defense_id: str
    reply_id: str
    kind: str  # COUNTER | EXCHANGE


def link_counters(events: Sequence[Mapping[str, Any]], links: Sequence[DefenseLink], params: RelationParams = RelationParams()) -> list[CounterLink]:
    """R3. 방어 연결된 공격마다 방어한 선수의 뒤따른 공격을 반격 또는 교환으로 가른다.

    상대 공격의 최대 동작(`phase_marks.max_extension_us`, 없으면 종료) 이전에 시작했으면 교환, 이후면 반격이다.
    """
    by_id = {e["event_id"]: e for e in events}
    atks = attacks(events)
    out: list[CounterLink] = []
    for link in links:
        if link.value != "linked" or link.defense_id is None:
            continue
        a1, d = by_id[link.attack_id], by_id[link.defense_id]
        peak = (a1.get("phase_marks") or {}).get("max_extension_us", a1["end_timestamp_us"])
        for a2 in atks:
            if a2["participant_id"] != link.defender or a2["event_id"] == a1["event_id"] or event_state(a2) != CONFIRMED:
                continue
            s = a2["start_timestamp_us"]
            if d["start_timestamp_us"] <= s <= d["end_timestamp_us"] + params.delta_counter_us and s > a1["start_timestamp_us"]:
                out.append(CounterLink(a1["event_id"], d["event_id"], a2["event_id"], "EXCHANGE" if s < peak else "COUNTER"))
    return out


@dataclass(frozen=True)
class RangeResult:
    value: str  # maintained | entered | widened | unknown
    delta: Optional[float] = None
    reason_code: Optional[str] = None


def _sample_at(samples: Sequence[tuple[int, float]], t: int, max_gap: int) -> Optional[float]:
    if not samples:
        return None
    ts, d = min(samples, key=lambda x: abs(x[0] - t))
    return d if abs(ts - t) <= max_gap else None


def judge_range(attack: Mapping[str, Any], distance_samples: Sequence[tuple[int, float]], params: RelationParams = RelationParams()) -> RangeResult:
    """R4. 공격 전후 거리(박스 높이 단위) 변화. 표본이 기준 시점 근처에 없으면 판단 불가다."""
    before = _sample_at(distance_samples, attack["start_timestamp_us"] - params.epsilon_before_us, params.max_sample_gap_us)
    after = _sample_at(distance_samples, attack["end_timestamp_us"] + params.epsilon_after_us, params.max_sample_gap_us)
    if before is None or after is None:
        return RangeResult("unknown", None, "missing_input")
    delta = after - before
    if abs(delta) <= params.tau_d:
        return RangeResult("maintained", delta)
    return RangeResult("entered" if delta < 0 else "widened", delta)


def judge_balance_recovery(attack: Mapping[str, Any], states: Sequence[Mapping[str, Any]], params: AnalysisParams = AnalysisParams()) -> GuardReturn:
    """R6. 공격 뒤 밸런스가 안정 라벨로 돌아오기까지의 지연(R5와 같은 구조)."""
    own = [s for s in states if s["participant_id"] == attack["participant_id"] and s["state_category"] == "balance"]
    return judge_recovery(attack, own, BALANCE_STABLE_LABELS, params)


def derive_balance_recovery(
    events: Sequence[Mapping[str, Any]], states: Sequence[Mapping[str, Any]], *, video_id: str, duration_us: int, params: AnalysisParams = AnalysisParams()
) -> dict[str, list[dict[str, Any]]]:
    """R6+R7. 공격마다 밸런스 회복 지연을 판정하고 선수·기술별 반복 집계와 주장 정의를 만든다."""
    return derive_post_action(
        events, states, video_id=video_id, duration_us=duration_us, params=params,
        question_code=QUESTION_BALANCE_RECOVERY, judge=judge_balance_recovery, subject="밸런스 회복 지연",
    )
