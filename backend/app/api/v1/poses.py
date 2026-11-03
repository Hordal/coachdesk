"""팀 연구 포즈 가져오기와 조회(상세 설계 27의 9절)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ...domain.models import PoseImport
from ...media.store import MediaStore
from ...services.poses import MAX_REPORTED, MAX_ROWS, PoseRepo, PoseSet, content_digest, validate
from ...settings import Settings
from ...storage.videos import VideoRepo
from ..deps import User, current_user, get_media, get_poses, get_videos
from ..errors import ProblemError
from ..schemas import PoseSetView
from .observations import _ready_video, reference_pts

router = APIRouter(tags=["poses"])


def _view(found: PoseSet) -> PoseSetView:
    return PoseSetView(set_id=found.set_id, video_id=found.video_id, created_at=found.created_at, source=found.source, **found.summary.model_dump())


@router.post("/videos/{video_id}/poses", response_model=PoseSetView, status_code=status.HTTP_201_CREATED)
def import_poses(
    video_id: str,
    body: PoseImport,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    media: MediaStore = Depends(get_media),
    repo: PoseRepo = Depends(get_poses),
) -> PoseSetView:
    """팀 연구 포즈를 읽기 전용으로 가져온다. 영상의 프레임 시각과 맞지 않으면 아무것도 저장하지 않는다.

    포즈 줄의 값은 고치지 않고 33점·visibility·presence를 그대로 보존한다. 선수 신원은 정하지 않는다.
    같은 내용을 다시 보내면 새로 만들지 않고 이미 가져온 것을 200으로 돌려준다.
    """
    video = _ready_video(video_id, user, videos)
    if len(body.poses) > MAX_ROWS:
        raise ProblemError(413, "too_many_rows", "포즈 줄이 너무 많음", f"최대 {MAX_ROWS}줄")
    settings: Settings = request.app.state.settings
    table, tolerance = reference_pts(video.video_id, settings, media)
    errors = validate(table, body, tolerance)
    if errors:
        raise ProblemError(
            422, "invalid_poses", "가져온 내용이 영상과 맞지 않거나 형식에 맞지 않음", f"오류 {len(errors)}건",
            errors=errors[:MAX_REPORTED], error_count=len(errors),
        )
    digest = content_digest(body)
    existing = repo.by_hash(video_id, user.owner_id, digest)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _view(existing)
    return _view(repo.create(owner_id=user.owner_id, video_id=video_id, payload=body, digest=digest))


@router.get("/videos/{video_id}/poses", response_model=PoseSetView)
def get_pose_set(
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    repo: PoseRepo = Depends(get_poses),
) -> PoseSetView:
    """가장 최근에 가져온 팀 연구 포즈의 요약."""
    if videos.get_for_owner(video_id, user.owner_id) is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    found = repo.latest(video_id, user.owner_id)
    if found is None:
        raise ProblemError(404, "poses_not_found", "가져온 포즈가 없음", f"video_id={video_id}")
    return _view(found)
