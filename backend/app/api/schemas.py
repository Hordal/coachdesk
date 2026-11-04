"""API 응답 모델. 미산출 값은 null이며 서버가 0으로 채우지 않는다(19)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..domain.models import (
    Aggregate,
    Assessment,
    ClaimType,
    DatasetKind,
    Event,
    EventRelation,
    Evidence,
    Exchange,
    FindingStatus,
    Participant,
    Sport,
    StateInterval,
)


class ApiModel(BaseModel):
    """응답 모델의 기반. 서버가 항상 내보내는 필드는 OpenAPI에서 필수로 표시한다."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class RunSummary(ApiModel):
    run_id: str
    video_id: str
    filename: Optional[str] = Field(default=None, description="올린 영상의 파일 이름. 가상 예제처럼 올린 영상이 없으면 null")
    mode: str
    status: str = Field(description="작업 상태: queued|running|completed|partial|failed|cancelled")
    dataset_kind: DatasetKind
    expert_validated: bool
    definition_version: str
    sport: Sport
    duration_us: int
    participants: list[Participant]
    counts: dict[str, int]
    parent_run_id: Optional[str] = Field(default=None, description="정정으로 만들어진 실행이면 정정 전 실행")
    superseded_by: Optional[str] = Field(default=None, description="이 실행을 정정한 새 실행. 있으면 최신이 아니다")


class FindingView(ApiModel):
    finding_id: str
    claim_type: ClaimType
    status: FindingStatus
    hold_reason: Optional[str] = None
    template_id: Optional[str] = None
    text: Optional[str] = Field(default=None, description="보류된 주장은 null")
    slots: Optional[dict[str, Any]] = None
    input_refs: list[str]
    evidence_refs: list[str]
    rule_ref: Optional[str] = None


class MemberView(ApiModel):
    event: Event
    assessment: Optional[Assessment] = None


class AggregateDetail(ApiModel):
    aggregate: Aggregate
    members: dict[str, list[MemberView]] = Field(
        description="numerator, denominator, unknown, excluded 별 구성 사건과 해당 판정"
    )


class TimelineView(ApiModel):
    from_us: int
    to_us: int
    states: list[StateInterval]
    events: list[Event]
    relations: list[EventRelation]
    assessments: list[Assessment]


class EventDetail(ApiModel):
    event: Event
    assessments: list[Assessment]
    relations: list[EventRelation]
    exchanges: list[Exchange]
    evidence: list[Evidence]


class EvidenceView(ApiModel):
    evidence: Evidence
    playable: bool
    access_url: Optional[str] = Field(default=None, description="짧은 유효기간 서명 URL. 재생 불가면 null")
    reason_code: Optional[str] = None


JobStageStatus = Literal["pending", "running", "completed", "failed", "skipped", "unsupported", "cancelled"]
JobStatus = Literal["queued", "running", "completed", "partial", "failed", "cancelled"]


class JobStageView(ApiModel):
    name: str
    label: str
    status: JobStageStatus
    progress: float = Field(ge=0, le=1, description="단계 안의 진행 비율. 완료 예정 시간은 제공하지 않는다")
    message: Optional[str] = None
    reason_code: Optional[str] = None


class JobErrorView(ApiModel):
    """사용자에게 보여줄 안전한 오류. 경로·스택을 담지 않는다."""

    code: str
    title: str
    stage: Optional[str] = None
    retryable: bool
    trace_id: Optional[str] = None
    at_us: Optional[int] = Field(default=None, description="오류가 난 영상 시각(알 수 있을 때)")


class JobView(ApiModel):
    job_id: str
    kind: str
    status: JobStatus
    stage: Optional[str] = None
    progress: float = Field(ge=0, le=1, description="처리 단계 기준 전체 진행 비율")
    stages: list[JobStageView]
    attempts: int
    max_attempts: int
    cancel_requested: bool
    error: Optional[JobErrorView] = None
    run_id: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class UploadCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sport: Sport


class UploadView(ApiModel):
    upload_id: str
    filename: str
    size_bytes: int
    part_size: int
    part_count: int
    sport: Sport
    status: Literal["receiving", "completed", "aborted", "failed"]
    received_parts: list[int] = Field(description="이미 받은 조각 번호. 이어 올리기에 쓴다")
    video_id: Optional[str] = None
    job_id: Optional[str] = None


class VideoSummary(ApiModel):
    video_id: str
    filename: str
    sport: Sport
    size_bytes: int
    status: Literal["processing", "ready", "failed", "cancelled"]
    duration_us: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: str
    job: Optional[JobView] = None
    run_id: Optional[str] = None
    media_state: Literal["present", "expired", "deleted"] = Field(default="present", description="원본·재생용 사본이 남아 있는지")
    media_expires_at: Optional[str] = Field(default=None, description="자동 삭제 예정 시각(RFC 3339). 보관 기간이 없으면 null")


class PlaybackView(ApiModel):
    playable: bool
    access_url: Optional[str] = Field(default=None, description="짧은 유효기간 서명 URL")
    reason_code: Optional[str] = None


class VideoInfoView(ApiModel):
    video_id: str
    width: int
    height: int
    duration_us: int
    frame_count: int
    nominal_fps: Optional[float] = Field(default=None, description="스트림이 선언한 값. 시각 계산에 쓰지 않는다")
    average_fps: Optional[float] = None
    is_vfr: bool = Field(description="프레임 간격이 일정하지 않음. 프레임 표의 시각을 사용해야 한다")


class FrameEntryView(ApiModel):
    index: int = Field(description="표시 순서 번호(시작 프레임이 0)")
    pts_us: int = Field(description="원본 재생 시작 기준 표시 시각(마이크로초)")


class FramesView(ApiModel):
    video_id: str
    from_us: int
    to_us: int
    frames: list[FrameEntryView]


class Joint(ApiModel):
    """화면 표시 좌표(원점 좌상단, x 오른쪽, y 아래)로 되돌린 관절. 좌표가 없으면 state=missing이며 0으로 채우지 않는다."""

    name: str
    x: Optional[float] = None
    y: Optional[float] = None
    score: Optional[float] = None
    state: Literal["observed", "missing"] = "observed"


class PoseSample(ApiModel):
    time_us: int = Field(ge=0, description="원본 재생 시작 기준 정수 마이크로초")
    participant_id: str
    joints: list[Joint]


class PoseView(ApiModel):
    available: bool
    reason_code: Optional[str] = Field(default=None, description="관절 데이터가 없는 사유")
    landmark_set_id: Optional[str] = None
    coordinate_space_id: Optional[str] = Field(default=None, description="저장 좌표계(분석 프레임). 응답 좌표는 표시 좌표로 되돌린 값이다")
    width: Optional[int] = Field(default=None, description="표시 영상 너비(px)")
    height: Optional[int] = Field(default=None, description="표시 영상 높이(px)")
    connections: list[tuple[str, str]] = Field(default_factory=list)
    samples: list[PoseSample] = Field(default_factory=list)


class TrackSummaryView(ApiModel):
    track_id: int
    first_us: int
    last_us: int
    frame_count: int
    missing_frames: int = Field(description="첫 관측과 마지막 관측 사이에서 이 트랙이 없는 프레임 수")
    longest_gap_us: int
    median_area_ratio: float = Field(description="분석 프레임 면적 대비 상자 면적의 중앙값. 후보를 보여 줄 때의 참고 값이며 신원 판정이 아니다")
    mean_score: Optional[float]


class PoseSetView(ApiModel):
    """가져온 팀 연구 포즈의 요약. 정확도가 아니라 줄 수와 상태 분포다."""

    set_id: str
    video_id: str
    created_at: str
    source: str
    row_count: int
    frame_count: int
    no_landmark_rows: int = Field(description="포즈가 없는 줄(점을 채우지 않음)")
    quality_row_count: int
    window_count: int
    clip_ids: list[str]
    status_counts: dict[str, int]
    mode_counts: dict[str, int]
    source_counts: dict[str, int]
    participant_ids: list[str] = Field(description="`candidate:N`은 선수가 아니라 입력 상자 ID")
    feature_ready_rows: int
    first_us: Optional[int]
    last_us: Optional[int]


class ObservationSetView(ApiModel):
    set_id: str
    video_id: str
    created_at: str
    source: str
    frame_count: int
    detection_count: int
    track_count: int
    analysis_width: Optional[int]
    analysis_height: Optional[int]
    assignment_state: Literal["NOT_RUN", "ASSIGNED"] = Field(description="사용자가 선수를 선택하기 전에는 NOT_RUN")
    tracks: list[TrackSummaryView]
    warnings: list[str]


class CandidateBoxView(ApiModel):
    track_id: int
    bbox: tuple[float, float, float, float] = Field(description="표시 영상 좌표(원점 좌상단, px)의 [x_min, y_min, x_max, y_max]")


class CandidateFrameView(ApiModel):
    frame_index: int
    time_us: int
    separated: bool = Field(description="주요 후보 상자가 서로 겹치지 않은 프레임이면 true. 서로 겹친 프레임뿐이면 false")
    boxes: list[CandidateBoxView]


class DesignationFramesView(ApiModel):
    set_id: str
    display_width: int
    display_height: int
    candidate_state: Literal["OK", "INSUFFICIENT"] = Field(description="후보 트랙이 두 개 미만이면 INSUFFICIENT이며 선수를 지정할 수 없다")
    frames: list[CandidateFrameView]


class DesignationRequest2(ApiModel):
    set_id: str = Field(description="선택의 기준이 된 검출·추적 결과 번호. 그 사이 결과가 바뀌었으면 거절한다")
    user_track_ids: list[int]
    opponent_track_ids: list[int]
    mode: Literal["sparring", "solo"] = "sparring"


class DesignationAssignmentView(ApiModel):
    participant_id: str
    role: Literal["USER", "OPPONENT"]
    track_ids: list[int]


class DesignationView(ApiModel):
    state: Literal["NOT_RUN", "ASSIGNED"]
    set_id: Optional[str] = None
    assignments: list[DesignationAssignmentView]


class DeleteResult(ApiModel):
    video_id: str
    status: Literal["deleted", "deleting"] = Field(description="deleting이면 정리가 끝나지 않았고 완료될 때까지 다시 시도한다")


class DesignationRequest(ApiModel):
    kind: Literal["designation"] = "designation"
    user_participant_id: str = Field(description="사용자 본인인 선수. 상대는 나머지 선수로 정한다")


class CandidateRoleSuggestion(ApiModel):
    """`candidate:N`에 대한 역할 제안. 적용하지 않은 제안이며 사용자의 본인 지정 정정으로만 반영한다."""

    participant_id: str
    suggested_role: Optional[Literal["USER", "OPPONENT"]] = None
    target_participant_id: Optional[str] = Field(default=None, description="지정된 선수 번호(A/B). 제안할 수 없으면 null")
    track_ids: list[int]
    reason_code: Optional[Literal["no_designation", "no_track", "conflict", "track_not_designated", "partial_match"]] = Field(
        default=None, description="제안할 수 없는 사유. 제안이 있으면 null"
    )


class ParticipantChange(ApiModel):
    participant_id: str
    role_before: str
    role_after: str


class CorrectionPreview(ApiModel):
    kind: Literal["designation"]
    changes: list[ParticipantChange]
    recalculation: Literal["not_required"] = Field(description="집계·판정·문장이 선수 번호 기준이라 값이 바뀌지 않는다")
    note: str


class CorrectionEntry(ApiModel):
    correction_id: str
    kind: str
    from_run_id: str
    run_id: str
    created_at: str


class CorrectionHistory(ApiModel):
    original_run_id: str
    latest_run_id: str
    entries: list[CorrectionEntry]


ReportCategory = Literal["position", "technique", "value", "evidence_mismatch", "other"]


class ReportRequest(ApiModel):
    category: ReportCategory
    subject_ref: Optional[str] = Field(default=None, max_length=128, description="관련 사건·집계·근거 ID")
    text: str = Field(min_length=1, max_length=2000)


class ReportView(ApiModel):
    report_id: str
    run_id: str
    category: ReportCategory
    subject_ref: Optional[str]
    text: str
    created_at: str


class ContextNoteRequest(ApiModel):
    participant_id: Optional[str] = Field(default=None, max_length=128)
    start_us: Optional[int] = Field(default=None, ge=0)
    end_us: Optional[int] = Field(default=None, ge=0)
    text: str = Field(min_length=1, max_length=2000)


class ContextNoteView(ApiModel):
    note_id: str
    run_id: str
    participant_id: Optional[str]
    start_us: Optional[int]
    end_us: Optional[int]
    text: str = Field(description="사용자가 직접 설명한 의도(self_report). AI 관측과 섞지 않는다")
    created_at: str


class MeView(ApiModel):
    user_id: str
    email: Optional[str]
    display_name: Optional[str]
    role: Literal["participant", "admin"]
    csrf_token: Optional[str] = Field(description="변경 요청(POST·PUT·DELETE)의 X-CSRF-Token 헤더에 넣는다. dev 모드는 null")
    auth_mode: Literal["dev", "session"]


class LoginRequest(ApiModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class InviteInfo(ApiModel):
    email: str
    role: Literal["participant", "admin"]


class InviteAcceptRequest(ApiModel):
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(max_length=128)
    consent: bool = Field(description="이용 안내와 영상 이용에 동의했는지")


class InviteCreateRequest(ApiModel):
    email: str = Field(max_length=254)
    role: Literal["participant", "admin"] = "participant"
    ttl_hours: Optional[int] = Field(default=None, ge=1, le=24 * 30)


class InviteCreated(ApiModel):
    invite_id: str
    accept_path: str = Field(description="초대 수락 화면 경로. 토큰이 들어 있으며 이 응답에서만 볼 수 있다")
    expires_at: float


class InviteView(ApiModel):
    invite_id: str
    email: str
    role: Literal["participant", "admin"]
    state: Literal["pending", "accepted", "revoked", "expired"]
    created_at: str
    expires_at: float


class UserView(ApiModel):
    user_id: str
    email: str
    display_name: str
    role: Literal["participant", "admin"]
    status: Literal["active", "blocked"]
    created_at: str
    last_login_at: Optional[str]
