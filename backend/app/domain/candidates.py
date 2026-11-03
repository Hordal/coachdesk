"""`candidate:N` 선수 후보의 역할 제안(상세 설계 11).

팀 검출 모델의 `candidate:N`은 입력 상자 ID라 선수가 아니다. 사용자가 고른 트랙 지정(USER/OPPONENT)과 후보의 `track_id`가
완전히 겹칠 때만 역할을 '제안'한다. 제안은 저장하거나 적용하지 않으며, 적용은 사용자의 본인 지정 정정으로만 한다.
입력 순서·화면 위치·번호 크기로 신원을 정하지 않는다.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .models import TrackAssignmentSet


def suggest_candidate_roles(participants: Sequence[Mapping[str, Any]], assignment: TrackAssignmentSet) -> list[dict[str, Any]]:
    """역할이 정해지지 않은(UNRESOLVED) 선수마다 제안 한 건. 제안할 수 없으면 사유를 낸다."""
    by_role = {a.role: (a.participant_id, set(a.track_ids)) for a in assignment.assignments}
    out: list[dict[str, Any]] = []
    for p in participants:
        if p["role"] != "UNRESOLVED":
            continue
        tracks = set(p.get("track_ids") or [])
        item: dict[str, Any] = {"participant_id": p["participant_id"], "suggested_role": None, "target_participant_id": None, "track_ids": sorted(tracks), "reason_code": None}
        if assignment.state != "ASSIGNED" or not by_role:
            item["reason_code"] = "no_designation"
        elif not tracks:
            item["reason_code"] = "no_track"
        else:
            hits = {role: tracks & ids for role, (_, ids) in by_role.items()}
            matched = [role for role, h in hits.items() if h]
            if len(matched) > 1:
                item["reason_code"] = "conflict"
            elif not matched:
                item["reason_code"] = "track_not_designated"
            elif hits[matched[0]] != tracks:
                item["reason_code"] = "partial_match"  # 일부 트랙만 지정에 있음
            else:
                item["suggested_role"] = matched[0]
                item["target_participant_id"] = by_role[matched[0]][0]
        out.append(item)
    return out
