"""후보 사건 정책(02B R1~R8 공통, 24·25의 후보 원칙).

팀 동작 검출 모델의 사건은 `status=CANDIDATE`, `verified=false`가 기본이다. 후보는 '확인된' 사건이 아니므로
확인된 전체(분모)에 넣지 않고, 후보로만 따로 센다. 후보를 자동으로 확인 사건으로 올리지 않는다.
상태가 UNKNOWN인 사건은 판단 불가로 다룬다.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

CONFIRMED = "confirmed"
CANDIDATE = "candidate"
UNKNOWN = "unknown"


def event_state(event: Mapping[str, Any]) -> str:
    """confirmed: status가 VALID이고 verified가 false가 아님. unknown: status UNKNOWN. 그 밖(CANDIDATE, verified=false, 상태 없음)은 candidate."""
    status = event.get("status")
    if status == "UNKNOWN":
        return UNKNOWN
    if status == "VALID" and event.get("verified") is not False:
        return CONFIRMED
    return CANDIDATE


def split_events(events: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {CONFIRMED: [], CANDIDATE: [], UNKNOWN: []}
    for e in events:
        out[event_state(e)].append(e)
    return out


def held_note(events: Iterable[Mapping[str, Any]]) -> str | None:
    """집계에서 빠진 사건 수를 알리는 문구. 빠진 것이 없으면 None."""
    parts = split_events(events)
    n_c, n_u = len(parts[CANDIDATE]), len(parts[UNKNOWN])
    if not (n_c or n_u):
        return None
    pieces = []
    if n_c:
        pieces.append(f"후보 사건 {n_c}건")
    if n_u:
        pieces.append(f"상태 불명 사건 {n_u}건")
    return f"{', '.join(pieces)}은 확인되지 않아 집계에서 제외함"
