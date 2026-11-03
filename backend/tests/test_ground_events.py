"""그라운드 사건(33): 모델 규칙과 계약 형식 변환."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.domain.contract_check import check_document
from app.domain.ground_events import from_contract
from app.domain.models import Event

TAKEDOWN = {
    "schema_version": "1.0",
    "event_id": "mma_event_001",
    "event_type": "TAKEDOWN",
    "outcome": "SUCCESS",
    "initiator_participant_id": "USER",
    "defender_participant_id": "OPPONENT",
    "start_timestamp_us": 12_500_000,
    "end_timestamp_us": 14_100_000,
    "status": "VALID",
    "confidence": 0.91,
    "evidence": {"start_timestamp_us": 12_100_000, "end_timestamp_us": 14_500_000, "participant_tracks": {"USER": [1], "OPPONENT": [0]}},
}
TOP_CONTROL = {
    "schema_version": "1.0",
    "event_id": "mma_event_002",
    "event_type": "TOP_CONTROL",
    "top_participant_id": "USER",
    "bottom_participant_id": "OPPONENT",
    "duration_us": 5_000_000,
    "start_timestamp_us": 14_100_000,
    "end_timestamp_us": 19_100_000,
    "status": "VALID",
    "confidence": 0.7,
    "evidence": {"start_timestamp_us": 14_100_000, "end_timestamp_us": 19_100_000, "participant_tracks": {"USER": [1], "OPPONENT": [0]}},
}


def test_contract_example_becomes_event_and_evidence():
    events, evidences = from_contract([TAKEDOWN], "v1")
    event = Event.model_validate(events[0])
    assert (event.event_label, event.outcome) == ("TAKEDOWN", "SUCCESS")
    assert (event.participant_id, event.target_participant_id) == ("USER", "OPPONENT")
    assert (event.start_timestamp_us, event.end_timestamp_us) == (12_500_000, 14_100_000)  # 원본 PTS 마이크로초 그대로
    assert evidences[0]["evidence_id"] == event.evidence_ref
    assert (evidences[0]["start_us"], evidences[0]["end_us"]) == (12_100_000, 14_500_000)
    assert evidences[0]["participant_refs"] == ["USER", "OPPONENT"]


def test_converted_events_pass_the_contract_check():
    events, evidences = from_contract([TAKEDOWN, TOP_CONTROL], "v1")
    doc = {
        "video_id": "v1",
        "duration_us": 60_000_000,
        "participants": [{"participant_id": "USER"}, {"participant_id": "OPPONENT"}],
        "events": events,
        "evidence": evidences,
    }
    report, _ = check_document(doc, "v1")
    assert [e for e in report.errors if "mma_event" in e or "ev_" in e] == []  # 최상위 필수 필드 오류는 이 시험의 대상이 아니다
    doc["events"][1]["top_participant_id"] = "NOBODY"
    report, _ = check_document(doc, "v1")
    assert any("NOBODY" in e for e in report.errors)


def test_duration_must_equal_end_minus_start():
    bad = copy.deepcopy(TOP_CONTROL)
    bad["duration_us"] = 1
    with pytest.raises(ValueError, match="duration_us"):
        from_contract([bad], "v1")


def test_unknown_outcome_with_valid_status_is_allowed_and_unknown_event_may_omit_outcome():
    attempt_only = {**TAKEDOWN, "outcome": "UNKNOWN"}
    unreliable = {**TAKEDOWN, "event_id": "mma_event_003", "status": "UNKNOWN", "outcome": None}
    events, _ = from_contract([attempt_only, unreliable], "v1")
    assert [(e["status"], e["outcome"]) for e in events] == [("VALID", "UNKNOWN"), ("UNKNOWN", None)]


def test_valid_event_needs_its_result_field():
    missing = {k: v for k, v in TAKEDOWN.items() if k != "outcome"}
    with pytest.raises(ValueError, match="outcome"):
        from_contract([missing], "v1")


def test_relation_labels_and_top_bottom():
    clear = {**TOP_CONTROL, "event_type": "GROUND_RELATION", "relation": "CLEAR_TOP_BOTTOM"}
    clear.pop("duration_us")
    events, _ = from_contract([clear], "v1")
    assert events[0]["relation"] == "CLEAR_TOP_BOTTOM"
    neutral = {**clear, "event_id": "mma_event_004", "relation": "NEUTRAL_ENTANGLED", "top_participant_id": None, "bottom_participant_id": None}
    assert from_contract([neutral], "v1")[0][0]["top_participant_id"] is None  # 우위가 없으면 top·bottom을 비운다
    no_pair = {**clear, "event_id": "mma_event_005", "top_participant_id": None}
    with pytest.raises(ValueError, match="top_participant_id"):
        from_contract([no_pair], "v1")


def test_submission_finish_actor_and_context():
    row = {
        "schema_version": "1.0", "event_id": "mma_event_006", "event_type": "SUBMISSION_FINISH",
        "actor_participant_id": "USER", "target_participant_id": "OPPONENT", "finish_context": "UNKNOWN",
        "start_timestamp_us": 1, "end_timestamp_us": 2, "status": "VALID", "confidence": None,
        "evidence": {"start_timestamp_us": 0, "end_timestamp_us": 3},
    }
    events, _ = from_contract([row], "v1")
    assert (events[0]["participant_id"], events[0]["target_participant_id"], events[0]["finish_context"]) == ("USER", "OPPONENT", "UNKNOWN")


def test_rejects_unknown_version_unknown_type_and_extra_fields():
    for patch, text in (
        ({"schema_version": "2.0"}, "schema_version"),
        ({"event_type": "ARMBAR"}, "event_type|type"),
        ({"technique": "armbar"}, "technique"),
    ):
        with pytest.raises(ValueError, match=text):
            from_contract([{**TAKEDOWN, **patch}], "v1")


def test_ground_fields_are_limited_to_ground_events():
    base = dict(event_id="e", status="VALID", start_timestamp_us=0, end_timestamp_us=1)
    with pytest.raises(ValidationError):
        Event(event_label="CROSS", relation="UNKNOWN", **base)
    with pytest.raises(ValidationError):
        Event(event_label="TAKEDOWN", outcome="SUCCESS", relation="UNKNOWN", **base)
    # 팀 동작 검출 모델의 후보 사건은 outcome=UNKNOWN을 그대로 쓴다
    candidate = Event(event_id="e", event_label="PUNCH_CANDIDATE", outcome="UNKNOWN", status="CANDIDATE", start_timestamp_us=0, end_timestamp_us=1)
    assert (candidate.status, candidate.verified, candidate.schema_version) == ("CANDIDATE", False, "phase1b_v1")
