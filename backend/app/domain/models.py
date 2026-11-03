"""분석 결과 객체(상세 설계 14). 서버·API·JSON Schema의 단일 정의.

JSON Schema(schemas/result_bundle.schema.json)와 OpenAPI는 이 모델에서 내보낸다.
값의 의미 검사(참조 무결성, 집계 정합성)는 contract_check에서 한다.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class Wire(BaseModel):
    """팀 검출 모델이 내는 줄을 그대로 받는 모델. 모르는 필드는 버리지 않고 보존한다."""

    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)


class Strict(BaseModel):
    # 입력(JSON Schema)에서는 기본값이 있는 필드를 생략할 수 있고,
    # 응답(OpenAPI)에서는 서버가 항상 값을 내보내므로 필수로 표시한다.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


Role = Literal["USER", "OPPONENT"]
ParticipantRole = Literal["USER", "OPPONENT", "UNRESOLVED"]  # UNRESOLVED: 선수 지정 전의 후보(팀 동작 검출 모델의 출력)
Sport = Literal["boxing", "kickboxing", "muay_thai", "mma"]
DatasetKind = Literal["synthetic", "manual", "model", "unanalyzed"]  # unanalyzed: 영상 준비만 끝나고 분석 결과가 없음
EventStatus = Literal["CANDIDATE", "VALID", "UNKNOWN"]  # CANDIDATE: 검출 모델이 낸 후보(검증 전). VALID·UNKNOWN: 사건 자체의 신뢰, 결과·관계만 모르면 VALID와 해당 필드 UNKNOWN(33의 7절)
GROUND_EVENT_TYPES = ("TAKEDOWN", "GROUND_RELATION", "TOP_CONTROL", "SUBMISSION_FINISH")
RelationType = Literal["counter_to", "contact_of", "followed_by"]
AssessmentValue = Literal["present", "absent", "unknown", "not_applicable"]
ResultStatus = Literal["VALID", "UNKNOWN", "UNSUPPORTED", "ERROR"]  # 입력이 없거나 판단할 수 없으면 UNKNOWN과 reason_code
DistanceState = Literal["OUTSIDE", "KICK_RANGE", "PUNCH_RANGE", "CLOSE_RANGE", "CLINCH_RANGE", "UNKNOWN"]
MovementDirection = Literal["STATIONARY", "FORWARD", "BACKWARD", "LEFT", "RIGHT", "UNKNOWN"]
RangeChange = Literal["ENTRY", "EXIT", "HOLD", "UNKNOWN"]
AssignmentState = Literal["NOT_RUN", "ASSIGNED"]  # 사용자가 선택하기 전에는 NOT_RUN
ContactRegion = Literal["head", "body", "leg", "guard", "unclear"]
Availability = Literal["available", "expired", "deleted", "unavailable"]
ClaimType = Literal["observation", "aggregate", "causal_hypothesis", "correction"]
FindingStatus = Literal["valid", "withheld"]

TimeUs = Field(ge=0, description="원본 재생 시작 기준 정수 마이크로초")


class BodyProfile(Strict):
    height_cm: Optional[float] = Field(default=None, gt=0)
    reach_cm: Optional[float] = Field(default=None, gt=0)
    main_stance: Optional[Literal["orthodox", "southpaw", "unknown"]] = None
    source: Optional[Literal["user_provided", "measured"]] = None
    entered_at: Optional[str] = Field(default=None, description="RFC 3339")


class Participant(Strict):
    participant_id: str
    role: ParticipantRole
    track_ids: list[int] = Field(default_factory=list, description="이 선수에 연결된 추적 번호. 한 선수에 여러 개일 수 있다")
    user_designated: bool = False
    body_profile: Optional[BodyProfile] = None


class MetricSample(Strict):
    metric_id: str
    definition_id: str
    definition_version: str
    participant_id: Optional[str] = None
    start_us: int = TimeUs
    end_us: int = TimeUs
    value: Optional[float] = None
    unit: Optional[str] = None
    status: ResultStatus
    reason_code: Optional[str] = None


class StateInterval(Strict):
    state_id: str
    participant_id: str
    body_part: Optional[str] = None
    state_category: str
    label: Optional[str] = None
    start_us: int = TimeUs
    end_us: int = TimeUs


class EvidenceMeasure(Wire):
    """사건에 붙는 근거 측정 항목(팀 동작 검출 모델의 `evidence` 한 줄). 영상 구간 근거(Evidence)와 다르다."""

    measure: str
    value: Union[float, int, bool, str, None] = None


class Event(Wire):
    """팀 동작 검출 모델의 사건 형식(phase1b_v1)을 그대로 따른다.

    필드 이름·의미는 팀 출력(`action_candidates.jsonl`)과 같다. 팀 형식에 없는 필드는 서버가 쓰는 확장이다:
    `event_id`(집계·관계·근거가 가리키는 ID. 가져오기 입력(EventIn)에서는 비우면 서버가 만든다), `target_participant_id`(상대 선수),
    `evidence_ref`(영상 구간 근거), `confidence`, 접촉면·상태 참조. 그라운드 필드(33)는 `event_label`이 그라운드 사건일 때만 쓴다.
    """

    schema_version: str = "phase1b_v1"
    event_id: str
    event_label: str
    type: Optional[str] = None
    status: EventStatus
    verified: bool = False
    outcome: Optional[str] = None
    participant_id: Optional[str] = None
    target_participant_id: Optional[str] = None
    role: Optional[ParticipantRole] = None
    track_id: Optional[int] = None
    arm: Optional[str] = None
    synthetic: bool = False
    start_timestamp_us: int = TimeUs
    end_timestamp_us: int = TimeUs
    start_frame_index: Optional[int] = Field(default=None, ge=0)
    end_frame_index: Optional[int] = Field(default=None, ge=0)
    evidence: list[EvidenceMeasure] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    clip_id: Optional[str] = None
    mode: Optional[str] = None
    tracklet_ref: Optional[str] = None
    rule_version: Optional[str] = None
    contamination_gates: list[str] = Field(default_factory=list)
    evidence_path: Optional[str] = None
    input_joint_quality: Optional[str] = None
    # 서버 확장
    evidence_ref: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1, description="내부 판정·평가용. 화면의 정확도가 아니다")
    source_surface: Optional[str] = None
    target_surface: Optional[str] = None
    phase_marks: Optional[dict[str, int]] = None
    pre_state_refs: list[str] = Field(default_factory=list)
    post_state_refs: list[str] = Field(default_factory=list)
    # 그라운드 사건(33)
    relation: Optional[str] = None
    top_participant_id: Optional[str] = None
    bottom_participant_id: Optional[str] = None
    finish_context: Optional[str] = None

    @model_validator(mode="after")
    def _ground_fields_match_label(self) -> "Event":
        fields = {
            "relation": self.relation,
            "top_participant_id": self.top_participant_id,
            "bottom_participant_id": self.bottom_participant_id,
            "finish_context": self.finish_context,
        }
        if self.event_label not in GROUND_EVENT_TYPES:
            used = sorted(k for k, v in fields.items() if v is not None)
            if used:
                raise ValueError(f"{used} 필드는 그라운드 사건({list(GROUND_EVENT_TYPES)})에만 쓴다")
            return self
        allowed = {
            "TAKEDOWN": {"outcome"},
            "GROUND_RELATION": {"relation", "top_participant_id", "bottom_participant_id"},
            "TOP_CONTROL": {"top_participant_id", "bottom_participant_id"},
            "SUBMISSION_FINISH": {"finish_context"},
        }[self.event_label]
        extra = sorted(k for k, v in {**fields, "outcome": self.outcome if self.outcome != "UNKNOWN" else None}.items() if v is not None and k not in allowed)
        if extra:
            raise ValueError(f"{self.event_label}에는 {extra} 필드를 쓰지 않는다")
        if self.event_label == "TAKEDOWN" and self.outcome not in (None, "SUCCESS", "DEFENDED", "UNKNOWN"):
            raise ValueError("TAKEDOWN의 outcome은 SUCCESS·DEFENDED·UNKNOWN 중 하나여야 한다")
        if self.relation not in (None, "CLEAR_TOP_BOTTOM", "NEUTRAL_ENTANGLED", "SCRAMBLE_TRANSITION", "UNKNOWN"):
            raise ValueError("relation은 CLEAR_TOP_BOTTOM·NEUTRAL_ENTANGLED·SCRAMBLE_TRANSITION·UNKNOWN 중 하나여야 한다")
        if self.finish_context not in (None, "MATCH_WIN", "SPARRING_TAP", "UNKNOWN"):
            raise ValueError("finish_context는 MATCH_WIN·SPARRING_TAP·UNKNOWN 중 하나여야 한다")
        if self.status == "VALID":  # 사건 자체를 못 믿는 경우(UNKNOWN)와 검증 전 후보(CANDIDATE)에서만 결과 필드를 비울 수 있다
            required = {"TAKEDOWN": "outcome", "GROUND_RELATION": "relation", "SUBMISSION_FINISH": "finish_context"}.get(self.event_label)
            if required and getattr(self, required) is None:
                raise ValueError(f"{self.event_label}은 {required}가 필요하다(모르면 UNKNOWN)")
            needs_pair = self.event_label == "TOP_CONTROL" or self.relation == "CLEAR_TOP_BOTTOM"
            if needs_pair and (self.top_participant_id is None or self.bottom_participant_id is None):
                raise ValueError(f"{self.event_label}({self.relation or '통제'})은 top_participant_id와 bottom_participant_id가 필요하다")
        return self


class EventIn(Event):
    """가져오기 입력의 사건. 팀 출력에는 ID가 없으므로 event_id를 비울 수 있다."""

    event_id: Optional[str] = None  # type: ignore[assignment]


class EventRelation(Strict):
    relation_id: str
    subject_event_id: str
    relation: RelationType
    object_event_id: str
    assessment_ref: Optional[str] = None
    basis: Optional[str] = None


class Assessment(Strict):
    assessment_id: str
    subject_ref: str
    question_code: str
    value: AssessmentValue
    status: ResultStatus
    reason_code: Optional[str] = None
    contact_region: Optional[ContactRegion] = None
    input_refs: list[str] = Field(default_factory=list)
    note: Optional[str] = None


class Exchange(Strict):
    exchange_id: str
    anchor_event_id: str
    participant_ids: list[str]
    start_us: int = TimeUs
    end_us: int = TimeUs
    event_refs: list[str]
    relation_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class Evidence(Strict):
    evidence_id: str
    video_id: str
    start_us: int = TimeUs
    end_us: int = TimeUs
    participant_refs: list[str] = Field(default_factory=list)
    availability: Availability


class AggregateScope(Strict):
    video_id: str
    participant_id: str
    interval: str


class AggregateCounts(Strict):
    candidates: int = Field(ge=0)
    denominator: int = Field(ge=0)
    numerator: int = Field(ge=0)
    unknown: int = Field(ge=0)
    excluded: int = Field(ge=0)


class Aggregate(Strict):
    aggregate_id: str
    description: Optional[str] = None
    counting_unit: str
    scope: AggregateScope
    condition_refs: list[str] = Field(default_factory=list)
    question_code: str
    contact_region_condition: Optional[ContactRegion] = None
    candidate_refs: list[str]
    denominator_refs: list[str]
    numerator_refs: list[str]
    unknown_refs: list[str]
    excluded_refs: list[str]
    counts: AggregateCounts
    ratio: Optional[float] = Field(default=None, ge=0, le=1, description="분모 0이면 null")


class Finding(Strict):
    finding_id: str
    claim_type: ClaimType
    template_id: Optional[str] = None
    input_refs: list[str]
    evidence_refs: list[str] = Field(default_factory=list)
    rule_ref: Optional[str] = None
    status: FindingStatus
    hold_reason: Optional[str] = None


class AnalysisWarning(Strict):
    module: str
    level: Literal["INFO", "WARN", "ERROR"]
    message: str
    timestamp_us: Optional[int] = Field(default=None, ge=0)


class ModuleOutcome(Strict):
    status: ResultStatus
    message: Optional[str] = None
    version: Optional[str] = None


class FrameTransform(Strict):
    """분석 프레임과 표시·부호화 프레임의 대응(03).

    분석 영상은 표시 영상을 비율을 유지해 분석 크기 안에 맞추고(letterbox) 남는 곳을 가운데 정렬로 채운 것이다.
    analysis = display × scale + pad이고, 표시 영상은 부호화 프레임에 회전(시계 방향)을 적용한 영상이다.
    MMACoachAgent 저장소의 `fit_transform`·`inverse_rotation`과 같은 규칙이다.
    """

    coded_width: int = Field(gt=0)
    coded_height: int = Field(gt=0)
    display_width: int = Field(gt=0)
    display_height: int = Field(gt=0)
    analysis_width: int = Field(gt=0)
    analysis_height: int = Field(gt=0)
    rotation_degrees: int = 0
    scale: float = Field(gt=0)
    pad_x: float = 0.0
    pad_y: float = 0.0

    @model_validator(mode="after")
    def _rotation_is_supported(self) -> "FrameTransform":
        if self.rotation_degrees not in (0, 90, 180, 270):
            raise ValueError("회전 각도는 0, 90, 180, 270만 지원한다")
        return self

    @classmethod
    def fit(
        cls, coded_width: int, coded_height: int, rotation_degrees: int, analysis_width: int, analysis_height: int
    ) -> "FrameTransform":
        """부호화 크기와 회전에서 분석 프레임 변환을 만든다. scale은 가로·세로 중 작은 비율이고 남는 부분은 가운데 정렬 패딩이다."""
        rotation = rotation_degrees % 360
        if rotation not in (0, 90, 180, 270):
            raise ValueError("지원하지 않는 회전 각도")
        display_width, display_height = (coded_height, coded_width) if rotation in (90, 270) else (coded_width, coded_height)
        scale = min(analysis_width / display_width, analysis_height / display_height)
        return cls(
            coded_width=coded_width, coded_height=coded_height,
            display_width=display_width, display_height=display_height,
            analysis_width=analysis_width, analysis_height=analysis_height,
            rotation_degrees=rotation, scale=scale,
            pad_x=(analysis_width - display_width * scale) / 2, pad_y=(analysis_height - display_height * scale) / 2,
        )

    def to_coded(self, x: float, y: float) -> tuple[float, float]:
        """분석 좌표를 부호화 프레임(회전 적용 전) 좌표로 되돌린다."""
        dx, dy = self.to_display(x, y)
        r = self.rotation_degrees % 360
        if r == 0:
            return dx, dy
        if r == 90:
            return dy, self.coded_height - dx
        if r == 180:
            return self.coded_width - dx, self.coded_height - dy
        return self.coded_width - dy, dx

    def to_display(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.pad_x) / self.scale, (y - self.pad_y) / self.scale

    def to_analysis(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.pad_x, y * self.scale + self.pad_y


Bbox = tuple[float, float, float, float]  # [x_min, y_min, x_max, y_max]


class Detection(Strict):
    detection_id: str
    bbox_xyxy: Bbox
    score: float
    class_name: str = "person"


class DetectionFrame(Strict):
    """`ai/schemas`의 검출 형식. 파이프라인 파일에는 쓰이지 않으며 실제 파일 형식은 DetectionRecord다."""

    timestamp_us: int = TimeUs
    detections: list[Detection] = Field(default_factory=list)
    status: ResultStatus = "VALID"


class Track(Strict):
    """추적기가 붙인 임시 번호의 관측. track_id는 participant_id가 아니다."""

    track_id: int
    bbox_xyxy: Bbox
    tracking_score: Optional[float] = None


class TrackFrame(Strict):
    """`ai/schemas`의 추적 형식. 파이프라인 파일에는 쓰이지 않으며 실제 파일 형식은 TrackRecord다."""

    timestamp_us: int = TimeUs
    tracks: list[Track] = Field(default_factory=list)
    status: ResultStatus = "VALID"


class TrackAssignment(Strict):
    """사용자가 고른 선수와 추적 번호의 대응. 선택 전에는 만들지 않고 상태를 NOT_RUN으로 둔다."""

    participant_id: str
    role: Role
    track_ids: list[int] = Field(min_length=1)
    start_us: Optional[int] = Field(default=None, ge=0)
    end_us: Optional[int] = Field(default=None, ge=0)
    source: Literal["user_selection", "auto"] = "user_selection"


class TrackAssignmentSet(Strict):
    state: AssignmentState = "NOT_RUN"
    assignments: list[TrackAssignment] = Field(default_factory=list)


CORE_JOINTS: tuple[str, ...] = (
    "nose",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    "left_hip", "right_hip",
    "left_knee", "right_knee",
    "left_ankle", "right_ankle",
)

Keypoint = tuple[Optional[float], Optional[float], Optional[float]]  # (x, y, 점수). 없는 값은 null


class PoseFrame(Strict):
    """한 프레임·한 선수의 관절. 좌표는 분석 프레임(analysis_px)이며 값이 없으면 null이고 0으로 채우지 않는다."""

    timestamp_us: int = TimeUs
    participant_id: str
    track_id: Optional[int] = None
    keypoints: dict[str, Keypoint]
    status: ResultStatus = "VALID"

    @model_validator(mode="after")
    def _core_joints_present(self) -> "PoseFrame":
        missing = [name for name in CORE_JOINTS if name not in self.keypoints]
        if missing:
            raise ValueError(f"핵심 관절이 없음: {missing}")
        return self


def blank_keypoints() -> dict[str, Keypoint]:
    return {name: (None, None, None) for name in CORE_JOINTS}


class PoseSet(Strict):
    """한 영상의 관절 관측. 프레임별 대량 데이터이므로 결과 묶음(ResultBundle)과 따로 다룬다(14)."""

    landmark_set_id: str
    coordinate_space_id: str
    transform: FrameTransform
    connections: list[tuple[str, str]] = Field(default_factory=list, description="관절 이름 쌍. 뼈대 선")
    frames: list[PoseFrame]


class VideoMetadata(BaseModel):
    """선수 검출·추적 파이프라인의 `metadata.json`(영상 전처리 결과). 우리가 만든 형식이 아니므로 모르는 필드는 무시한다."""

    model_config = ConfigDict(extra="ignore")

    source: str = ""
    duration_us: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    coded_width: Optional[int] = None
    coded_height: Optional[int] = None
    fps: Optional[float] = None
    codec: Optional[str] = None
    rotation_degrees: int = 0
    is_vfr: Optional[bool] = None
    frame_count: Optional[int] = None
    time_base: Optional[str] = None
    start_time_us: Optional[int] = None
    frame_timestamps_us: list[Optional[int]] = Field(default_factory=list)
    timestamp_basis: Optional[str] = None
    analysis_width: Optional[int] = None
    analysis_height: Optional[int] = None
    derivative_frame_count: Optional[int] = None
    transform: Optional[FrameTransform] = None
    warnings: list[str] = Field(default_factory=list)


class WireRecord(BaseModel):
    """팀 파이프라인이 파일(JSONL)에 실제로 쓰는 줄. 모르는 필드(timing_ms 등)도 그대로 보존한다."""

    model_config = ConfigDict(extra="allow")


class DetectionObs(WireRecord):
    """검출 하나. 기준 이름은 `bbox_xyxy`·`score`이고 실험용 산출물의 `bbox`·`confidence`도 받는다."""

    class_id: Optional[int] = None
    class_name: str = "person"
    confidence: float = Field(validation_alias=AliasChoices("score", "confidence"))
    bbox: Bbox = Field(validation_alias=AliasChoices("bbox_xyxy", "bbox"))


class DetectionRecord(WireRecord):
    """`detection_frames.jsonl`의 한 줄. 좌표는 분석 프레임(coordinate_space=analysis_frame_xyxy_px)이다."""

    frame_index: Optional[int] = Field(default=None, ge=0, description="없으면 줄 순서를 프레임 번호로 본다(기준 형식에는 없음)")
    timestamp_us: int = TimeUs
    width: Optional[int] = None
    height: Optional[int] = None
    coordinate_space: Optional[str] = None
    detections: list[DetectionObs] = Field(default_factory=list)


class TrackObs(WireRecord):
    """트랙의 한 프레임 관측. 기준 이름은 `bbox_xyxy`·`tracking_score`이고 실험용 산출물의 `bbox`·`confidence`도 받는다."""

    track_id: int
    bbox: Bbox = Field(validation_alias=AliasChoices("bbox_xyxy", "bbox"))
    confidence: Optional[float] = Field(default=None, validation_alias=AliasChoices("tracking_score", "confidence"))
    class_id: Optional[int] = None
    class_name: str = "person"
    tracking_state: Optional[str] = None


class TrackRecord(WireRecord):
    """`tracking_frames.jsonl`의 한 줄. track_id는 추적기의 임시 번호이며 participant_id가 아니다."""

    frame_index: Optional[int] = Field(default=None, ge=0, description="없으면 줄 순서를 프레임 번호로 본다(기준 형식에는 없음)")
    timestamp_us: int = TimeUs
    tracks: list[TrackObs] = Field(default_factory=list)


MEDIAPIPE_LANDMARKS: tuple[str, ...] = (
    "nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye", "right_eye_outer",
    "left_ear", "right_ear", "mouth_left", "mouth_right",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist",
    "left_pinky", "right_pinky", "left_index", "right_index", "left_thumb", "right_thumb",
    "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
    "left_heel", "right_heel", "left_foot_index", "right_foot_index",
)  # MediaPipe Pose Landmarker의 33점. 인덱스는 이 순서. 핵심 13점(CORE_JOINTS)은 이름이 같다.


class PoseLandmark(WireRecord):
    """팀 연구 포즈의 점 하나. 좌표는 분석 프레임(x_px·y_px)과 정규화 값. visibility·presence는 정확도가 아니다."""

    x_norm: Optional[float] = None
    y_norm: Optional[float] = None
    z_norm: Optional[float] = None
    x_px: Optional[float] = None
    y_px: Optional[float] = None
    visibility: Optional[float] = None
    presence: Optional[float] = None
    off_image: Optional[bool] = None
    finite: Optional[bool] = None
    state: Optional[str] = None
    index: Optional[int] = None


class PoseObservationRecord(WireRecord):
    """`pose_observations.jsonl`(phase1b_v1)·`pose_quality_frames.jsonl`(phase1c_v1)의 한 줄. 포즈 하나의 33점.

    전체 프레임의 pose_index는 선수 ID가 아니고, 크롭의 `candidate:N`은 입력 상자 ID이며 역할은 UNRESOLVED다.
    """

    schema_version: Optional[str] = None
    clip_id: Optional[str] = None
    frame_index: int = Field(ge=0)
    timestamp_us: int = TimeUs
    arm: Optional[str] = None
    pose_index: Optional[int] = None
    participant_id: Optional[str] = None
    role: Optional[ParticipantRole] = None
    track_id: Optional[int] = None
    crop: Optional[Any] = None
    landmarks: Optional[dict[str, PoseLandmark]] = None
    # phase1c 추가
    mode: Optional[str] = None
    source: Optional[str] = None
    attempt: Optional[str] = None
    status: Optional[str] = None
    tracklet_ref: Optional[str] = None
    raw_landmark_count: Optional[int] = None


class PoseQualityRecord(WireRecord):
    """`pose_quality.jsonl`(phase1b_v1)의 한 줄. 관절을 쓸 수 있는지의 판정 기록."""

    frame_index: int = Field(ge=0)
    timestamp_us: int = TimeUs
    arm: Optional[str] = None
    pose_index: Optional[int] = None
    participant_id: Optional[str] = None
    role: Optional[ParticipantRole] = None
    association_status: Optional[str] = None
    feature_ready: Optional[bool] = None
    statuses: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class WindowCandidateRecord(WireRecord):
    """`window_candidates.jsonl`(phase1c_v1)의 한 줄. 구간 안에서 복원한 선수 후보."""

    status: Optional[str] = None
    frame_start: int = Field(ge=0)
    frame_end: int = Field(ge=0)
    pts_start: int = TimeUs
    pts_end: int = TimeUs
    participants: list[dict[str, Any]] = Field(default_factory=list)
    rejected_tracks: list[dict[str, Any]] = Field(default_factory=list)


class PoseImport(Strict):
    """팀 연구 포즈 가져오기 입력(파일 줄을 그대로). 포즈가 있는 프레임만 들어 있으므로 영상의 모든 프레임이 있을 필요는 없다."""

    source: str = Field(default="MMACoachAgent research", max_length=200)
    poses: list[PoseObservationRecord]
    quality: list[PoseQualityRecord] = Field(default_factory=list)
    windows: list[WindowCandidateRecord] = Field(default_factory=list)


class ObservationImport(Strict):
    """선수 검출·추적 결과 가져오기 입력(파이프라인이 쓴 파일 그대로). 검출은 선택 사항이다."""

    source: str = Field(default="MMACoachAgent ai/", max_length=200)
    metadata: VideoMetadata
    tracks: list[TrackRecord]
    detections: list[DetectionRecord] = Field(default_factory=list)


class ResultBundle(Strict):
    """한 영상·한 분석 실행의 결과 묶음."""

    schema_version: str = Field(pattern=r"^(demo-)?\d+\.\d+$")
    dataset_kind: DatasetKind
    annotation_source: str
    expert_validated: bool = False
    definition_version: str
    scenario: Optional[str] = None
    video_id: str
    duration_us: int = Field(gt=0)
    sport: Sport
    time_unit: Literal["us"]
    interval_convention: Literal["[start,end)"]
    notes: list[str] = Field(default_factory=list)
    participants: list[Participant]
    metric_samples: list[MetricSample] = Field(default_factory=list)
    state_intervals: list[StateInterval] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    relations: list[EventRelation] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)
    exchanges: list[Exchange] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    expected_summary: Optional[dict[str, Any]] = Field(
        default=None, description="회귀 검사용 기대값. 운영 결과에는 없음"
    )


class AggregateSpec(Strict):
    """가져오기 입력의 집계 정의. 분자·분모·판단 불가·제외와 비율은 서버가 판정에서 계산하므로 받지 않는다."""

    aggregate_id: str
    description: Optional[str] = None
    counting_unit: str
    scope: AggregateScope
    condition_refs: list[str] = Field(default_factory=list)
    question_code: str
    contact_region_condition: Optional[ContactRegion] = None
    candidate_refs: list[str]


class StatesFromPoses(Strict):
    """가져온 팀 연구 포즈에서 서버가 가드 상태 구간을 만들어 더하도록 요청한다(25의 4-3절).

    상대가 있는 방향은 입력으로만 받는다. 화면 위치나 번호로 추측하지 않으며, 없는 후보는 `left_hand`·`right_hand` 상태만 만들고
    `front_hand`·`rear_hand`는 만들지 않는다(그러면 가드 복귀 판정은 입력 부족으로 판단 불가가 된다).
    """

    set_id: Optional[str] = Field(default=None, description="쓸 포즈 묶음. 비우면 이 영상에서 가장 최근에 가져온 것")
    opponent_direction: dict[str, Literal[1, -1]] = Field(
        default_factory=dict, description="`candidate:N` → 상대가 화면 오른쪽(1)·왼쪽(-1)에 있는 방향. 그 후보의 앞손·뒷손 상태를 만드는 데만 쓴다"
    )


class ManualImport(Strict):
    """사람이 입력한 사건·판정(manual 모드)의 가져오기 파일.

    영상 번호, 길이, 종목, 데이터 종류는 서버가 영상에서 채우므로 받지 않는다.
    집계는 정의만 받고 값은 서버가 계산하며, 주장의 문장은 읽을 때 템플릿으로 만든다.
    """

    annotation_source: str = Field(description="입력한 사람이나 도구. 예: 'labeler-a', 'cvat-export'")
    states_from_poses: Optional[StatesFromPoses] = Field(default=None, description="가져온 포즈로 가드 상태 구간을 만들어 `state_intervals`에 더한다")
    derive: list[Literal["rear_guard_return", "balance_recovery"]] = Field(
        default_factory=list,
        description="서버가 사건·상태 구간에서 판정·집계·주장을 만들어 더할 분석 규칙(02B). 같은 대상·질문의 판정을 직접 입력하면 중복으로 거절한다",
    )
    definition_version: str
    scenario: Optional[str] = None
    notes: list[str] = Field(default_factory=list)
    participants: list[Participant]
    metric_samples: list[MetricSample] = Field(default_factory=list)
    state_intervals: list[StateInterval] = Field(default_factory=list)
    events: list[EventIn] = Field(default_factory=list)
    relations: list[EventRelation] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)
    exchanges: list[Exchange] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    aggregates: list[AggregateSpec] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
