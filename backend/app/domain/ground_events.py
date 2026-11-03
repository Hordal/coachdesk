"""그라운드 사건 계약(33의 7절)을 결과 묶음의 사건·근거로 바꾼다.

계약의 한 줄은 AI가 내는 JSON 한 객체다. 사건 형식은 팀 동작 검출 모델 형식(phase1b_v1)을 따르므로
행위자는 `participant_id`, 상대는 `target_participant_id`, 시각은 `start/end_timestamp_us`로 옮긴다.
값의 검사(종류별 필수 필드, 선수 참조)는 Event 모델과 contract_check가 맡는다.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import GROUND_EVENT_TYPES, Event, EventStatus

CONTRACT_VERSION = "1.0"


class _EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_timestamp_us: int = Field(ge=0)
    end_timestamp_us: int = Field(ge=0)
    participant_tracks: dict[str, list[int]] = Field(default_factory=dict)


class GroundEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    event_id: str
    event_type: str
    start_timestamp_us: int = Field(ge=0)
    end_timestamp_us: int = Field(ge=0)
    status: EventStatus
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    evidence: _EvidenceIn
    initiator_participant_id: Optional[str] = None  # TAKEDOWN
    defender_participant_id: Optional[str] = None  # TAKEDOWN
    actor_participant_id: Optional[str] = None  # SUBMISSION_FINISH(제안서에 필드 이름이 없어 기본 형식을 따름)
    target_participant_id: Optional[str] = None  # SUBMISSION_FINISH
    outcome: Optional[str] = None
    relation: Optional[str] = None
    top_participant_id: Optional[str] = None
    bottom_participant_id: Optional[str] = None
    duration_us: Optional[int] = Field(default=None, ge=0)
    finish_context: Optional[str] = None


def evidence_id_for(event_id: str) -> str:
    return f"ev_{event_id}"


def from_contract(rows: list[dict[str, Any]], video_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """계약 형식의 사건 목록을 (사건, 근거) 목록으로 바꾼다. 어긋나는 줄은 모두 모아 ValueError로 낸다."""
    events: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, row in enumerate(rows):
        name = row.get("event_id", f"#{i}") if isinstance(row, dict) else f"#{i}"
        try:
            src = GroundEventIn.model_validate(row)
        except ValidationError as exc:
            errors.extend(f"{name}: {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            continue
        if src.schema_version != CONTRACT_VERSION:
            errors.append(f"{name}: schema_version {src.schema_version}은 지원하지 않음(지원: {CONTRACT_VERSION})")
            continue
        if src.event_type not in GROUND_EVENT_TYPES:
            errors.append(f"{name}: event_type {src.event_type}은 지원하지 않음(지원: {list(GROUND_EVENT_TYPES)})")
            continue
        if src.duration_us is not None and src.duration_us != src.end_timestamp_us - src.start_timestamp_us:
            errors.append(f"{name}: duration_us가 end − start와 다름")
            continue
        actor = src.initiator_participant_id or src.actor_participant_id
        target = src.defender_participant_id or src.target_participant_id
        participants = [p for p in (actor, target, src.top_participant_id, src.bottom_participant_id) if p]
        ev_id = evidence_id_for(src.event_id)
        evidences.append(
            {
                "evidence_id": ev_id,
                "video_id": video_id,
                "start_us": src.evidence.start_timestamp_us,
                "end_us": src.evidence.end_timestamp_us,
                "participant_refs": list(dict.fromkeys(participants or src.evidence.participant_tracks.keys())),
                "availability": "available",
            }
        )
        data = {
            "schema_version": "phase1b_v1",
            "event_id": src.event_id,
            "event_label": src.event_type,
            "status": src.status,
            "participant_id": actor,
            "target_participant_id": target,
            "start_timestamp_us": src.start_timestamp_us,
            "end_timestamp_us": src.end_timestamp_us,
            "confidence": src.confidence,
            "outcome": src.outcome,
            "relation": src.relation,
            "top_participant_id": src.top_participant_id,
            "bottom_participant_id": src.bottom_participant_id,
            "finish_context": src.finish_context,
            "evidence_ref": ev_id,
        }
        try:
            Event.model_validate(data)
        except ValidationError as exc:
            errors.extend(f"{name}: {e['msg']}" for e in exc.errors())
            continue
        events.append(data)
    if errors:
        raise ValueError("\n".join(errors))
    return events, evidences
