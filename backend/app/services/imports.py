"""manual 모드 가져오기(상세 설계 27의 3.3, 14).

사람이 입력한 사건·판정을 받아 집계와 문장 단계만 실행한다. 집계의 분자·분모는 입력의 값을
믿지 않고 판정에서 계산하고, 계약 검사와 문장 템플릿 검증을 통과한 결과만 새 실행으로 저장한다.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import ValidationError

from ..domain.aggregation import index_assessments, recompute_aggregate
from ..domain.analysis import derive_guard_return
from ..domain.contract_check import check_document
from ..domain.events import assign_event_ids
from ..domain.policy import held_note
from ..domain.findings import ConsistencyError, render_finding
from ..domain.models import ManualImport, ResultBundle, StateInterval
from ..domain.states import judge_states
from ..domain.relations import derive_balance_recovery
from ..pipeline.registry import RunMode
from ..storage.memory import RunStore, StoredRun
from .poses import PoseRepo
from ..storage.videos import VideoRecord

SCHEMA_VERSION = "0.3"
MAX_REPORTED = 20


class ImportRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors[0] if errors else "가져오기 거절")
        self.errors = errors


def _with_event_participants(
    participants: list[dict[str, Any]], events: list[dict[str, Any]], states: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """사건·상태가 가리키는 선수가 목록에 없으면 후보(UNRESOLVED)로 추가한다. 팀 검출 모델은 선수 지정 전에 `candidate:N`을 쓴다."""
    known = {p["participant_id"] for p in participants}
    added: dict[str, dict[str, Any]] = {}
    for s in states or []:
        if s["participant_id"] not in known:
            added.setdefault(s["participant_id"], {"participant_id": s["participant_id"], "role": "UNRESOLVED", "track_ids": [], "user_designated": False, "body_profile": None})
    for e in events:
        for key in ("participant_id", "target_participant_id", "top_participant_id", "bottom_participant_id"):
            pid = e.get(key)
            if pid is None or pid in known:
                continue
            p = added.setdefault(pid, {"participant_id": pid, "role": "UNRESOLVED", "track_ids": [], "user_designated": False, "body_profile": None})
            if key == "participant_id" and e.get("role") in ("USER", "OPPONENT"):
                p["role"] = e["role"]
            if key == "participant_id" and e.get("track_id") is not None and e["track_id"] not in p["track_ids"]:
                p["track_ids"].append(e["track_id"])
    return participants + list(added.values())


def build_bundle(video: VideoRecord, data: ManualImport) -> dict[str, Any]:
    """가져오기 입력과 영상 정보로 결과 묶음을 만든다. 집계 값은 판정에서 계산한다."""
    if video.duration_us is None:
        raise ImportRejected(["영상 준비가 끝나지 않아 가져올 수 없습니다"])
    doc = data.model_dump(mode="json")
    doc["events"] = assign_event_ids(doc["events"])
    doc["participants"] = _with_event_participants(doc["participants"], doc["events"], doc["state_intervals"])
    doc.pop("states_from_poses", None)
    derive = doc.pop("derive", [])
    if derive and (note := held_note(doc["events"])):
        doc.setdefault("notes", []).append(note)
    for name, fn in (("rear_guard_return", derive_guard_return), ("balance_recovery", derive_balance_recovery)):
        if name in derive:
            derived = fn(doc["events"], doc["state_intervals"], video_id=video.video_id, duration_us=video.duration_us)
            for key, items in derived.items():
                doc[key] = doc[key] + items
    aidx, _ = index_assessments(doc["assessments"])
    aggregates = []
    for spec in doc["aggregates"]:
        counts = recompute_aggregate(spec, aidx)
        aggregates.append(
            {
                **spec,
                "candidate_refs": list(dict.fromkeys(spec["candidate_refs"])),
                "denominator_refs": counts.denominator,
                "numerator_refs": counts.numerator,
                "unknown_refs": counts.unknown,
                "excluded_refs": counts.excluded,
                "counts": {
                    "candidates": len(counts.candidates),
                    "denominator": len(counts.denominator),
                    "numerator": len(counts.numerator),
                    "unknown": len(counts.unknown),
                    "excluded": len(counts.excluded),
                },
                "ratio": counts.ratio,
            }
        )
    doc["aggregates"] = aggregates
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": "manual",
        "expert_validated": False,  # 가져오기만으로 전문가 검수가 되지는 않는다
        "video_id": video.video_id,
        "duration_us": video.duration_us,
        "sport": video.sport,
        "time_unit": "us",
        "interval_convention": "[start,end)",
        **doc,
    }


def validate_bundle(video: VideoRecord, bundle: dict[str, Any]) -> ResultBundle:
    """형식, 영상과의 일치, 계약 검사, 문장 템플릿 검증. 하나라도 어긋나면 모두 모아 거절한다."""
    errors: list[str] = []
    try:
        model = ResultBundle.model_validate(bundle)
    except ValidationError as exc:  # 입력 모델이 이미 검사하므로 서버가 채운 값의 문제일 때만 닿는다
        raise ImportRejected([f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:MAX_REPORTED]]) from exc

    for ev in model.evidence:
        if ev.video_id != video.video_id:
            errors.append(f"{ev.evidence_id}: 이 영상의 근거가 아닙니다")
    for agg in model.aggregates:
        if agg.scope.video_id != video.video_id:
            errors.append(f"{agg.aggregate_id}: 집계 범위의 영상이 이 영상과 다릅니다")

    report, _ = check_document(bundle, video.video_id)
    errors.extend(report.errors)

    if not errors:
        for finding in bundle["findings"]:
            try:
                render_finding(bundle, finding)
            except ConsistencyError as exc:
                errors.append(f"{finding['finding_id']}: {exc}")
            except (StopIteration, KeyError, IndexError):  # 템플릿이 필요로 하는 집계가 입력에 없음
                errors.append(f"{finding['finding_id']}: 템플릿에 필요한 집계가 input_refs에 없습니다")
    if errors:
        raise ImportRejected(errors)
    return model


def apply_pose_states(data: ManualImport, video: VideoRecord, poses: PoseRepo | None) -> ManualImport:
    """`states_from_poses`가 있으면 가져온 포즈에서 가드 상태 구간을 만들어 더하고, 판단 불가 구간 요약을 메모로 남긴다."""
    req = data.states_from_poses
    if req is None:
        return data
    found = None
    if poses is not None:
        found = poses.get(video.video_id, video.owner_id, req.set_id) if req.set_id else poses.latest(video.video_id, video.owner_id)
    if found is None:
        raise ImportRejected(["states_from_poses: 가져온 포즈가 없음"])
    result = judge_states(poses.load_poses(video.video_id, found.set_id), opponent_dir=req.opponent_direction)
    taken = {s.state_id for s in data.state_intervals}
    clash = [s["state_id"] for s in result.states if s["state_id"] in taken]
    if clash:
        raise ImportRejected([f"states_from_poses: 이미 있는 state_id와 겹침 {clash[:3]}"])
    reasons: dict[str, int] = {}
    for u in result.unknown:
        reasons[u["reason_code"]] = reasons.get(u["reason_code"], 0) + 1
    note = f"포즈 {found.set_id}에서 가드 상태 {len(result.states)}개 구간 생성"
    if reasons:
        note += "; 판단 불가 구간 " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))
    return data.model_copy(update={
        "state_intervals": data.state_intervals + [StateInterval(**s) for s in result.states],
        "notes": data.notes + [note],
    })


def import_manual(runs: RunStore, video: VideoRecord, data: ManualImport, parent_run_id: str | None, poses: PoseRepo | None = None, *, dataset_kind: Literal["manual", "model"] = "manual", extra=None, run_id: str | None = None) -> StoredRun:
    data = apply_pose_states(data, video, poses)
    bundle = build_bundle(video, data)
    bundle["dataset_kind"] = dataset_kind
    model = validate_bundle(video, bundle)
    run = StoredRun(run_id=run_id or uuid.uuid4().hex, owner_id=video.owner_id, mode=RunMode.MANUAL, status="completed", bundle=model, raw=bundle)
    runs.add_persistent(run, parent_run_id=parent_run_id, extra=extra)
    return run
