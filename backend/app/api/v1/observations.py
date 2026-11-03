"""선수 검출·추적 결과 가져오기와 후보 트랙 조회(상세 설계 27의 9절)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ...domain.models import ObservationImport
from ...media import probe
from ...media.store import MediaStore
from ...services.observations import (
    MAX_FRAMES,
    MAX_REPORTED,
    PLAYBACK_TOLERANCE_US,
    SOURCE_TOLERANCE_US,
    ObservationRepo,
    ObservationSet,
    content_digest,
    validate,
)
from ...storage.videos import VideoRepo
from ...services.designation import DesignationError, DesignationRepo, candidate_frames, check_selection
from ..deps import User, current_user, get_designations, get_media, get_observations, get_videos
from ...settings import Settings
from ..errors import ProblemError
from ..schemas import (
    CandidateBoxView,
    CandidateFrameView,
    DesignationAssignmentView,
    DesignationFramesView,
    DesignationRequest2,
    DesignationView,
    ObservationSetView,
    TrackSummaryView,
)

router = APIRouter(tags=["observations"])


def _view(found: ObservationSet, assignment_state: str = "NOT_RUN") -> ObservationSetView:
    meta = found.metadata
    return ObservationSetView(
        set_id=found.set_id, video_id=found.video_id, created_at=found.created_at, source=found.source,
        frame_count=found.frame_count, detection_count=found.detection_count, track_count=found.track_count,
        analysis_width=meta.get("analysis_width"), analysis_height=meta.get("analysis_height"),
        assignment_state=assignment_state,  # type: ignore[arg-type]
        tracks=[TrackSummaryView(**t.model_dump()) for t in found.tracks],
        warnings=list(meta.get("warnings") or []),
    )


def reference_pts(video_id: str, settings: Settings, media: MediaStore) -> tuple[list[int], int]:
    """분석 파이프라인이 쓴 프레임 시각표와 허용 오차. 원본의 PTS를 쓰고, 원본이 없으면 재생용 사본(시각이 조금 달라질 수 있음)을 쓴다."""
    original = next(iter(sorted((settings.originals_dir / video_id).glob("source.*"))), None)
    playback = media.video_path(video_id)
    reference, tolerance = (original, SOURCE_TOLERANCE_US) if original is not None else (playback, PLAYBACK_TOLERANCE_US)
    if reference is None:
        raise ProblemError(409, "video_media_missing", "영상 파일이 없어 프레임 시각을 대조할 수 없음")
    try:
        return [f.pts_us for f in probe.probe_video(reference).frames], tolerance
    except probe.MediaError as exc:
        raise ProblemError(422, "invalid_media", "영상 정보를 읽을 수 없음") from exc


def _ready_video(video_id: str, user: User, videos: VideoRepo):
    video = videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    if video.status != "ready":
        raise ProblemError(409, "video_not_ready", "영상 준비가 끝나지 않아 결과를 가져올 수 없음")
    return video


@router.post("/videos/{video_id}/observations", response_model=ObservationSetView, status_code=status.HTTP_201_CREATED)
def import_observations(
    video_id: str,
    body: ObservationImport,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    media: MediaStore = Depends(get_media),
    repo: ObservationRepo = Depends(get_observations),
) -> ObservationSetView:
    """검출·추적 결과를 읽기 전용으로 가져온다. 영상의 프레임 표와 시각이 맞지 않으면 아무것도 저장하지 않는다.

    같은 내용을 다시 보내면 새로 만들지 않고 이미 가져온 것을 200으로 돌려준다.
    """
    video = _ready_video(video_id, user, videos)
    if len(body.tracks) > MAX_FRAMES:
        raise ProblemError(413, "too_many_frames", "프레임이 너무 많음", f"최대 {MAX_FRAMES}프레임")
    settings: Settings = request.app.state.settings
    table, tolerance = reference_pts(video.video_id, settings, media)

    errors = validate(table, body, tolerance)
    if errors:
        raise ProblemError(
            422, "invalid_observations", "가져온 내용이 영상과 맞지 않거나 형식에 맞지 않음", f"오류 {len(errors)}건",
            errors=errors[:MAX_REPORTED], error_count=len(errors),
        )
    digest = content_digest(body)
    existing = repo.by_hash(video_id, user.owner_id, digest)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _view(existing)
    assert body.metadata.transform is not None
    return _view(repo.create(owner_id=user.owner_id, video_id=video_id, payload=body, digest=digest, transform=body.metadata.transform))


@router.get("/videos/{video_id}/observations", response_model=ObservationSetView)
def get_observations_view(
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    repo: ObservationRepo = Depends(get_observations),
    designations: DesignationRepo = Depends(get_designations),
) -> ObservationSetView:
    """가장 최근에 가져온 검출·추적 결과와 후보 트랙의 진단 값."""
    if videos.get_for_owner(video_id, user.owner_id) is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    found = repo.latest(video_id, user.owner_id)
    if found is None:
        raise ProblemError(404, "observations_not_found", "가져온 검출·추적 결과가 없음", f"video_id={video_id}")
    assignment, _ = designations.current(video_id, user.owner_id)
    return _view(found, assignment.state)


def _latest_or_404(video_id: str, user: User, videos: VideoRepo, repo: ObservationRepo) -> ObservationSet:
    if videos.get_for_owner(video_id, user.owner_id) is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    found = repo.latest(video_id, user.owner_id)
    if found is None:
        raise ProblemError(404, "observations_not_found", "가져온 검출·추적 결과가 없음", f"video_id={video_id}")
    return found


@router.get("/videos/{video_id}/designation-frames", response_model=DesignationFramesView)
def get_designation_frames(
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    repo: ObservationRepo = Depends(get_observations),
) -> DesignationFramesView:
    """후보 트랙이 보기 좋게 보이는 대표 프레임과 표시 영상 좌표의 상자. 프레임 이미지는 `frame-image`로 받는다."""
    found = _latest_or_404(video_id, user, videos, repo)
    from ...domain.models import FrameTransform

    transform = FrameTransform.model_validate(found.metadata["transform"])
    frames = candidate_frames(repo.load_tracks(video_id, found.set_id), found, transform)
    return DesignationFramesView(
        set_id=found.set_id, display_width=transform.display_width, display_height=transform.display_height,
        candidate_state="OK" if found.track_count >= 2 and frames else "INSUFFICIENT",
        frames=[
            CandidateFrameView(
                frame_index=f.frame_index, time_us=f.time_us, separated=f.separated,
                boxes=[CandidateBoxView(track_id=b.track_id, bbox=b.bbox_display) for b in f.boxes],
            )
            for f in frames
        ],
    )


def _designation_view(designations: DesignationRepo, video_id: str, user: User) -> DesignationView:
    assignment, set_id = designations.current(video_id, user.owner_id)
    return DesignationView(
        state=assignment.state, set_id=set_id,
        assignments=[DesignationAssignmentView(participant_id=a.participant_id, role=a.role, track_ids=a.track_ids) for a in assignment.assignments],
    )


@router.get("/videos/{video_id}/designation", response_model=DesignationView)
def get_designation(
    video_id: str,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    designations: DesignationRepo = Depends(get_designations),
) -> DesignationView:
    """현재 선수 지정. 사용자가 선택하기 전에는 NOT_RUN이고 배정이 비어 있다."""
    if videos.get_for_owner(video_id, user.owner_id) is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    return _designation_view(designations, video_id, user)


@router.put("/videos/{video_id}/designation", response_model=DesignationView)
def put_designation(
    video_id: str,
    body: DesignationRequest2,
    user: User = Depends(current_user),
    videos: VideoRepo = Depends(get_videos),
    repo: ObservationRepo = Depends(get_observations),
    designations: DesignationRepo = Depends(get_designations),
) -> DesignationView:
    """사용자가 사용자(USER)와 상대(OPPONENT)의 트랙을 직접 고른다. 한 선수에 트랙 여러 개를 연결할 수 있다.

    같은 선택을 다시 보내도 결과가 같고, 바꾸면 이력이 남는다. 신원은 입력 순서나 화면 위치로 정하지 않는다.
    """
    found = _latest_or_404(video_id, user, videos, repo)
    if body.set_id != found.set_id:
        raise ProblemError(409, "stale_observations", "그 사이 검출·추적 결과가 바뀌었음. 화면을 새로 고쳐 다시 선택해야 함", None, latest_set_id=found.set_id)
    if found.track_count < 2:
        raise ProblemError(409, "insufficient_candidates", "후보 트랙이 두 개 미만이라 선수를 지정할 수 없음")
    try:
        check_selection(repo.load_tracks(video_id, found.set_id), {t.track_id for t in found.tracks}, body.user_track_ids, body.opponent_track_ids)
    except DesignationError as exc:
        raise ProblemError(exc.status, exc.code, exc.title, None, **exc.extra) from exc
    current, current_set = designations.current(video_id, user.owner_id)
    wanted = (sorted(body.user_track_ids), sorted(body.opponent_track_ids))
    same = current.state == "ASSIGNED" and current_set == found.set_id and (
        sorted(current.assignments[0].track_ids), sorted(current.assignments[1].track_ids)
    ) == wanted
    if not same:  # 같은 선택을 다시 보내면 이력을 늘리지 않는다
        designations.save(video_id=video_id, owner_id=user.owner_id, observation=found, user=body.user_track_ids, opponent=body.opponent_track_ids)
    return _designation_view(designations, video_id, user)
