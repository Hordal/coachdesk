"""분석 도구의 데이터 계약과 검증 전 동작 후보 가져오기."""
from typing import Literal

from fastapi import APIRouter, Depends, Response, Request
from pydantic import Field

from ...domain.models import EventIn, ManualImport, ObservationImport, PoseImport, Strict
from ...services.imports import MAX_REPORTED, ImportRejected, import_manual
from ...services.poses import PoseRepo
from ...storage.memory import RunStore
from ...storage.videos import VideoRepo
from ..deps import User, current_user, get_poses, get_store, get_videos
from ..errors import ProblemError
from ..schemas import RunSummary
from .observations import _ready_video
from .runs import summarize

router = APIRouter(tags=["data-intake"])


class CandidateEvent(EventIn):
    status: Literal["CANDIDATE"]
    verified: Literal[False] = False
    outcome: Literal["UNKNOWN"] = "UNKNOWN"
    role: Literal["UNRESOLVED"] = "UNRESOLVED"


class ActionImport(Strict):
    source: str = Field(default="team-research", min_length=1, max_length=200)
    definition_version: str = Field(default="phase1b_v1", min_length=1, max_length=100)
    events: list[CandidateEvent] = Field(default_factory=list, max_length=100_000)


CONTRACTS = {
    "observations": (ObservationImport, "검출·추적", ["metadata.json", "tracking_frames.jsonl", "detection_frames.jsonl (선택)"]),
    "poses": (PoseImport, "포즈·품질", ["pose_observations.jsonl 또는 pose_quality_frames.jsonl", "pose_quality.jsonl (선택)", "window_candidates.jsonl (선택)"]),
    "actions": (ActionImport, "동작 후보", ["action_candidates.jsonl"]),
    "manual": (ManualImport, "사건·판정", ["manual_import.json"]),
}


@router.get("/data-contracts")
def contracts(user: User = Depends(current_user)) -> dict:
    return {
        "time_unit": "us", "interval_convention": "[start,end)",
        "items": [{"kind": kind, "title": title, "files": files, "schema_url": f"/api/v1/data-contracts/{kind}"}
                  for kind, (_, title, files) in CONTRACTS.items()],
    }


@router.get("/data-contracts/{kind}")
def contract(kind: Literal["observations", "poses", "actions", "manual"], response: Response, user: User = Depends(current_user)) -> dict:
    response.headers["Content-Disposition"] = f'attachment; filename="{kind}.schema.json"'
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **CONTRACTS[kind][0].model_json_schema()}


@router.post("/videos/{video_id}/actions", response_model=RunSummary, status_code=201)
def import_actions(
    video_id: str, body: ActionImport, request: Request, user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos), store: RunStore = Depends(get_store), poses: PoseRepo = Depends(get_poses),
) -> RunSummary:
    video = _ready_video(video_id, user, videos)
    assignment,set_id = request.app.state.designations.current(video_id,user.owner_id)
    pose_set = poses.latest(video_id,user.owner_id)
    by_track = {tid:a for a in assignment.assignments for tid in a.track_ids}
    events=[]
    for event in body.events:
        raw=event.model_dump()
        if event.track_id in by_track:
            who=by_track[event.track_id]
            raw.update(source_participant_id=event.participant_id,participant_id=who.participant_id,role=who.role)
        events.append(raw)
    from ...services.designation import DesignationRepo
    def provenance(conn):
        if assignment.state == "ASSIGNED":
            conn.execute("INSERT INTO run_provenance VALUES(?,?,?,?)", (run_id,set_id,pose_set.set_id if pose_set else None,assignment.model_dump_json()))
    import uuid
    run_id=uuid.uuid4().hex
    data = ManualImport(annotation_source=body.source, definition_version=body.definition_version,
                        participants=DesignationRepo.participants(assignment), events=events, notes=["연구 모델의 검증 전 동작 후보. 접촉·명중·선수 신원은 확인되지 않았습니다."])
    try:
        run = import_manual(store, video, data, videos.latest_run_id(video_id), poses, dataset_kind="model", extra=provenance, run_id=run_id)
    except ImportRejected as exc:
        raise ProblemError(422, "invalid_import", "동작 후보가 데이터 계약에 맞지 않음", f"오류 {len(exc.errors)}건",
                           errors=exc.errors[:MAX_REPORTED], error_count=len(exc.errors)) from exc
    return summarize(run, store, videos)
