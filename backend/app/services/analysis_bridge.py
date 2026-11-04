"""Adapt run-frozen, quality-filtered poses to the upstream state/rule engine."""
from collections import Counter
import math

from ..domain.analysis import attacks, derive_guard_return
from ..domain.models import PoseObservationRecord
from ..domain.policy import CONFIRMED, event_state
from ..domain.states import StateParams, judge_states

UPSTREAM_REVISION = "3b793344c92f795914f561f5d62e6b466ffff22e"


def observation_spans(view, duration_us):
    """Ranges of observed sample times, without filling long observation gaps."""
    groups = {}
    for sample in view.samples:
        if any(j.state == "observed" and j.score is not None and j.score >= .65 for j in sample.joints):
            groups.setdefault(sample.participant_id, set()).add(sample.time_us)
    spans = []
    for participant, stamps in groups.items():
        current = []
        for stamp in sorted(stamps):
            if current and stamp-current[-1] > 150_000:
                spans.append({"participant_id": participant, "start_us": current[0], "end_us": min(duration_us,current[-1]+1), "sample_count": len(current)})
                current = []
            current.append(stamp)
        if current:
            spans.append({"participant_id": participant, "start_us": current[0], "end_us": min(duration_us,current[-1]+1), "sample_count": len(current)})
    return spans


def analyze_rules(view, bundle):
    rows = []
    for index, sample in enumerate(view.samples):
        marks = {}
        for joint in sample.joints:
            if (joint.state == "observed" and joint.score is not None and joint.score >= .65
                    and joint.x is not None and joint.y is not None
                    and math.isfinite(joint.x) and math.isfinite(joint.y)):
                marks[joint.name] = {"x_px": joint.x, "y_px": joint.y, "visibility": joint.score,
                                     "presence": joint.score, "finite": True, "state": "VALID"}
        rows.append(PoseObservationRecord(frame_index=index, timestamp_us=sample.time_us,
                                         participant_id=sample.participant_id, landmarks=marks))
    # Do not infer opponent direction or stance, or bridge long missing intervals.
    states = judge_states(rows, params=StateParams(min_visibility=.65, max_gap_us=150_000))
    events = [e.model_dump(mode="json") for e in bundle.events]
    confirmed = [e for e in attacks(events) if event_state(e) == CONFIRMED]
    recorded_states = [s.model_dump(mode="json") for s in bundle.state_intervals]
    has_rear = any(s.get("body_part") == "rear_hand" for s in recorded_states)
    derived = derive_guard_return(events, recorded_states, video_id=bundle.video_id,
                                  duration_us=bundle.duration_us) if confirmed and has_rear else None
    return {"source_revision": UPSTREAM_REVISION, "method": "upstream-guard-rules-with-filtered-run-poses-v1",
            "states": states.states, "unknown_count": len(states.unknown),
            "unknown_reasons": dict(Counter(s["reason_code"] for s in states.unknown)),
            "pose_samples": len(rows), "confirmed_attacks": len(confirmed),
            "candidate_events": sum(event_state(e) != CONFIRMED for e in events),
            "guard_return": derived,
            "guard_return_status": "calculated" if derived else "held",
            "guard_return_reason": None if derived else "확인된 공격과 뒷손 상태가 모두 필요합니다. 후보를 확정하거나 상대 방향·스탠스를 추측하지 않습니다.",
            "note": "화면 좌표를 이용한 시험용 가드 상태입니다. 기술·명중·득점이나 가드 실수의 정답 판정이 아닙니다."}
