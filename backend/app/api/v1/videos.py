"""영상 정보, 프레임 표, 정확한 프레임 이미지, 서명된 영상 접근(상세 설계 05, 21, 27)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, Response

from ...domain.models import ManualImport
from ...jobs.store import JobStore
from ...media import probe
from ...media.binaries import BinaryNotFound
from ...media.store import MediaStore, UrlSigner
from ...services.imports import MAX_REPORTED, ImportRejected, import_manual
from ...storage.memory import RunStore
from ...storage.videos import VideoRepo
from ...services.deletion import DeletionService, VideoBusy
from ..deps import User, current_user, get_deletion, get_jobs, get_media, get_poses, get_signer, get_store, get_videos, owned_video_id
from ...services.poses import PoseRepo
from ..errors import ProblemError
from ..schemas import DeleteResult, FrameEntryView, FramesView, PlaybackView, RunSummary, VideoInfoView, VideoSummary
from .runs import summarize
from .views import video_summary

router = APIRouter(tags=["videos"])


@router.get("/videos", response_model=list[VideoSummary])
def list_videos(
    request: Request,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    jobs: JobStore = Depends(get_jobs),
) -> list[VideoSummary]:
    """업로드한 영상과 처리 상태. 가상 예제의 영상은 포함하지 않는다."""
    days = request.app.state.settings.retention_days
    return [video_summary(v, jobs.latest_for_video(v.video_id), videos.latest_run_id(v.video_id), days) for v in videos.list_for_owner(user.owner_id)]


@router.get("/videos/{video_id}/summary", response_model=VideoSummary)
def get_video_summary(
    request: Request,
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    jobs: JobStore = Depends(get_jobs),
) -> VideoSummary:
    video = videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    return video_summary(video, jobs.latest_for_video(video_id), videos.latest_run_id(video_id), request.app.state.settings.retention_days)


@router.post("/videos/{video_id}/imports", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
def import_results(
    video_id: str,
    data: ManualImport,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    store: RunStore = Depends(get_store),
    poses: PoseRepo = Depends(get_poses),
) -> RunSummary:
    """사람이 입력한 사건·판정을 가져와 새 실행(manual 모드)으로 저장한다. 이전 실행은 그대로 둔다."""
    video = videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    if video.status != "ready":
        raise ProblemError(409, "video_not_ready", "영상 준비가 끝나지 않아 결과를 가져올 수 없음")
    try:
        run = import_manual(store, video, data, videos.latest_run_id(video_id), poses)
    except ImportRejected as exc:
        raise ProblemError(
            422, "invalid_import", "가져온 내용이 데이터 계약에 맞지 않음", f"오류 {len(exc.errors)}건",
            errors=exc.errors[:MAX_REPORTED], error_count=len(exc.errors),
        ) from exc
    return summarize(run, store, videos)


@router.delete("/videos/{video_id}", response_model=DeleteResult, status_code=status.HTTP_202_ACCEPTED)
def delete_video(
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    deletion: DeletionService = Depends(get_deletion),
) -> DeleteResult:
    """영상과 결과를 모두 지운다. 즉시 조회를 막고, 정리가 끝나기 전에는 deleting으로 응답한다(15)."""
    video = videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    outcome = deletion.request_delete_all(video, user.owner_id)
    return DeleteResult(video_id=video_id, status="deleted" if outcome.completed else "deleting")


@router.delete("/videos/{video_id}/media", response_model=VideoSummary)
def delete_video_media(
    request: Request,
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    jobs: JobStore = Depends(get_jobs),
    deletion: DeletionService = Depends(get_deletion),
) -> VideoSummary:
    """원본과 재생용 사본만 지운다. 요약·수치 같은 결과는 남고 근거 영상은 재생할 수 없다."""
    video = videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    try:
        deletion.delete_media(video)
    except VideoBusy as exc:
        raise ProblemError(409, "video_busy", "처리 중인 영상은 처리가 끝나거나 취소된 뒤에 지울 수 있음") from exc
    except OSError as exc:
        raise ProblemError(503, "delete_incomplete", "파일을 모두 지우지 못했음. 잠시 뒤 다시 시도해야 함") from exc
    video = videos.get_for_owner(video_id, user.owner_id)
    assert video is not None
    return video_summary(video, jobs.latest_for_video(video_id), videos.latest_run_id(video_id), request.app.state.settings.retention_days)


@router.get("/videos/{video_id}/playback", response_model=PlaybackView)
def get_playback(
    video_id: str = Depends(owned_video_id),
    videos: VideoRepo = Depends(get_videos),
    media: MediaStore = Depends(get_media),
    signer: UrlSigner = Depends(get_signer),
) -> PlaybackView:
    """영상 전체의 재생 주소(분석 근거와 무관). 재생용 사본이 아직 없으면 재생할 수 없다."""
    if media.video_path(video_id) is None:
        record = videos.get(video_id)
        reason = f"evidence_{record.media_state}" if record and record.media_state != "present" else "evidence_unavailable"
        return PlaybackView(playable=False, access_url=None, reason_code=reason)
    return PlaybackView(playable=True, access_url=signer.sign(video_id), reason_code=None)


def _video(video_id: str, media: MediaStore) -> tuple[Path, probe.VideoInfo]:
    path = media.video_path(video_id)
    if path is None:
        raise ProblemError(404, "media_not_found", "영상 파일이 없음", f"video_id={video_id}")
    try:
        return path, probe.probe_video(path)
    except BinaryNotFound as exc:
        raise ProblemError(503, "media_tool_unavailable", "영상 처리 도구를 사용할 수 없음") from exc
    except probe.MediaError as exc:
        raise ProblemError(422, "invalid_media", "영상 정보를 읽을 수 없음") from exc


@router.get("/videos/{video_id}", response_model=VideoInfoView)
def get_video(video_id: str = Depends(owned_video_id), media: MediaStore = Depends(get_media)) -> VideoInfoView:
    _, info = _video(video_id, media)
    return VideoInfoView(
        video_id=video_id,
        width=info.width,
        height=info.height,
        duration_us=info.duration_us,
        frame_count=info.frame_count,
        nominal_fps=info.nominal_fps,
        average_fps=info.average_fps,
        is_vfr=info.is_vfr,
    )


@router.get("/videos/{video_id}/frames", response_model=FramesView)
def get_frames(
    video_id: str = Depends(owned_video_id),
    media: MediaStore = Depends(get_media),
    from_us: int = Query(0, ge=0),
    to_us: int = Query(..., gt=0),
) -> FramesView:
    """구간의 실제 프레임 표시 시각. 명목 fps로 만든 값이 아니다."""
    if not from_us < to_us:
        raise ProblemError(422, "invalid_range", "구간이 올바르지 않음", f"from_us={from_us}, to_us={to_us}")
    _, info = _video(video_id, media)
    frames = probe.frames_in_range(info, from_us, to_us)
    return FramesView(
        video_id=video_id,
        from_us=from_us,
        to_us=to_us,
        frames=[FrameEntryView(index=f.index, pts_us=f.pts_us) for f in frames],
    )


@router.get("/videos/{video_id}/frame-image")
def get_frame_image(
    video_id: str = Depends(owned_video_id),
    media: MediaStore = Depends(get_media),
    time_us: int = Query(..., ge=0),
) -> Response:
    """time_us에 표시되는 프레임을 디코더가 식별한 그대로 JPEG로 제공한다. 브라우저 탐색이 정확하지 않을 때 쓴다."""
    path, info = _video(video_id, media)
    frame = probe.frame_at(info, time_us)
    try:
        data = probe.extract_frame_jpeg(path, info, frame)
    except probe.MediaError as exc:
        raise ProblemError(422, "frame_extract_failed", "프레임을 추출할 수 없음", str(exc)) from exc
    if not data:
        raise ProblemError(422, "frame_extract_failed", "프레임을 추출할 수 없음")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "X-Frame-Index": str(frame.index),
            "X-Frame-Pts-Us": str(frame.pts_us),
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/media/{video_id}")
def get_media_file(
    video_id: str,
    exp: int = Query(...),
    sig: str = Query(...),
    media: MediaStore = Depends(get_media),
    signer: UrlSigner = Depends(get_signer),
) -> FileResponse:
    """서명된 주소로만 영상을 제공한다(범위 요청 지원). 서명은 소유자 확인 후에만 발급된다."""
    if not signer.verify(video_id, exp, sig):
        raise ProblemError(403, "invalid_signature", "접근 주소가 유효하지 않거나 만료됨")
    path = media.video_path(video_id)
    if path is None:
        raise ProblemError(404, "media_not_found", "영상 파일이 없음")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})
