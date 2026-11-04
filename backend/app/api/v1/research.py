"""Use verified research data through the same ownership, PTS and import checks."""
import hashlib
import shutil
from fastapi import APIRouter, Depends, Request, Response

from ...domain.models import ObservationImport, PoseImport
from ...services import research_handoff as handoff
from ...services.uploads import UploadError
from ..deps import User, current_user
from ..errors import ProblemError
from .observations import import_observations
from .poses import import_poses
from .intake import import_actions, ActionImport

router = APIRouter(tags=["research-library"])


def visible(request: Request) -> list[dict]:
    return [c for c in handoff.catalog() if not request.app.state.settings.public_demo or c["public_preview"]]


def match(video_id: str, request: Request, user: User) -> tuple:
    video = request.app.state.videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없습니다")
    clip = next((c for c in visible(request) if c["source_sha256"] == video.sha256), None)
    if clip is None:
        raise ProblemError(404, "research_not_matched", "원본 해시와 일치하는 연구 결과가 없습니다")
    return video, clip


@router.get("/research/clips")
def clips(request: Request, user: User = Depends(current_user)):
    return {"items": [{k: c[k] for k in ("clip_id", "filename", "source_url", "frame_count", "has_research_pose")} for c in visible(request)],
            "note": "연구 결과는 모델 예측이며 정답·명중 판정이 아닙니다. 선수는 직접 지정합니다."}


@router.get("/videos/{video_id}/research")
def research_match(video_id: str, request: Request, user: User = Depends(current_user)):
    video = request.app.state.videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없습니다")
    clip = next((c for c in visible(request) if c["source_sha256"] == video.sha256), None)
    obs = request.app.state.observations.latest(video_id, user.owner_id)
    assignment, _ = request.app.state.designations.current(video_id, user.owner_id)
    return {"available": bool(clip), "clip_id": clip["clip_id"] if clip else None,
            "ready": video.status == "ready", "attached": bool(obs and obs.source == handoff.SOURCE_LABEL),
            "assigned": assignment.state == "ASSIGNED", "has_research_pose": bool(clip and clip["has_research_pose"])}


@router.post("/research/clips/{clip_id}/import")
def import_clip(clip_id: str, request: Request, user: User = Depends(current_user)):
    try:
        clip = handoff.clip_by_id(clip_id, public=request.app.state.settings.public_demo)
        state = request.app.state
        existing = next((v for v in state.videos.list_for_owner(user.owner_id) if v.sha256 == clip["source_sha256"]), None)
        if existing:
            return {"video_id": existing.video_id, "status": existing.status, "reused": True}
        data = handoff.source_bytes(clip)
        required = len(data) * 3 + 16 * 1024**2
        if shutil.disk_usage(state.settings.data_dir).free < required:
            raise ProblemError(507, "research_storage_full", "영상을 준비할 저장 공간이 부족합니다",
                               f"이 영상에는 약 {(required + 1024**2 - 1) // 1024**2}MB의 여유 공간이 필요합니다. 공간을 확보한 뒤 다시 시도하세요.")
        upload, _ = state.uploads.create(user.owner_id, clip["filename"], len(data), "boxing", "research-" + clip["source_sha256"])
        for number, start in enumerate(range(0, len(data), upload.part_size)):
            chunk = data[start:start + upload.part_size]
            state.uploads.put_part(user.owner_id, upload.upload_id, number, chunk, hashlib.sha256(chunk).hexdigest())
        done = state.uploads.complete(user.owner_id, upload.upload_id)
        return {"video_id": done.video_id, "status": "processing", "reused": False}
    except handoff.HandoffError as exc:
        raise ProblemError(404, "research_unavailable", str(exc)) from exc
    except UploadError as exc:
        raise ProblemError(exc.status, exc.code, exc.title, exc.detail) from exc


@router.post("/videos/{video_id}/research/observations")
def attach_observations(video_id: str, request: Request, user: User = Depends(current_user)):
    _, clip = match(video_id, request, user)
    observations, poses, _ = handoff.payloads(clip)
    state = request.app.state
    obs = import_observations(video_id, ObservationImport.model_validate(observations), request, Response(), user,
                              state.videos, state.media, state.observations)
    pose_set = None
    if poses["poses"]:
        pose_set = import_poses(video_id, PoseImport.model_validate(poses), request, Response(), user,
                                state.videos, state.media, state.poses)
    return {"set_id": obs.set_id, "pose_set_id": pose_set.set_id if pose_set else None,
            "message": "원본 시각과 좌표를 검증했습니다. 사진에서 분석할 선수를 직접 지정하세요."}


@router.post("/videos/{video_id}/research/actions")
def attach_actions(video_id: str, request: Request, user: User = Depends(current_user)):
    _, clip = match(video_id, request, user)
    state = request.app.state
    assignment, _ = state.designations.current(video_id, user.owner_id)
    obs = state.observations.latest(video_id, user.owner_id)
    if assignment.state != "ASSIGNED" or not obs or obs.source != handoff.SOURCE_LABEL:
        raise ProblemError(409, "research_designation_required", "연구 검출 결과를 연결하고 선수를 먼저 지정해 주세요")
    if not clip["has_research_pose"]:
        raise ProblemError(409, "research_pose_missing", "이 클립에는 연구 관절 결과가 없습니다. 내장 모델로 새로 분석해 주세요")
    pose_set = state.poses.latest(video_id, user.owner_id)
    if not pose_set or pose_set.source != handoff.SOURCE_LABEL:
        raise ProblemError(409, "research_pose_not_linked", "연구 관절 결과가 변경됐습니다. 연구 결과 연결 후 선수를 다시 확인해 주세요")
    _, _, actions = handoff.payloads(clip)
    return import_actions(video_id, ActionImport(source="handoff-phase1c-video; uncertain IMAGE candidates excluded", events=actions),
                          request, user, state.videos, state.store, state.poses)
