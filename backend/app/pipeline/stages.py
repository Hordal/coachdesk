"""분석 단계 인터페이스(상세 설계 14의 공통 작업 요청·응답, 27의 3.2).

단계 구현은 가상 결과, 규칙, 학습 모델 중 무엇이든 같은 요청·응답 형식을 지킨다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, Sequence

from ..domain.models import AnalysisWarning


class StageName(StrEnum):
    VIDEO_READER = "video_reader"
    DETECTOR = "detector"
    TRACKER = "tracker"
    ASSIGNMENT_RESOLVER = "assignment_resolver"
    POSE_ESTIMATOR = "pose_estimator"
    METRIC_CALCULATOR = "metric_calculator"
    STATE_JUDGE = "state_judge"
    ACTION_CLASSIFIER = "action_classifier"
    CONTACT_JUDGE = "contact_judge"
    RELATION_LINKER = "relation_linker"
    AGGREGATOR = "aggregator"
    FINDING_BUILDER = "finding_builder"


# 처리 순서. 앞 단계의 출력이 뒤 단계의 입력이 된다(10).
STAGE_ORDER: tuple[StageName, ...] = tuple(StageName)

# 작업 상태. 데이터·판정 상태와 구별한다(14).
JobStatus = str  # queued | running | completed | partial | failed | cancelled


@dataclass(frozen=True)
class StageRequest:
    schema_version: str
    run_id: str
    stage: StageName
    input_refs: Sequence[str]
    config_ref: str | None = None
    interval: tuple[int, int] | None = None  # [start_us, end_us). 전체 처리면 None


@dataclass
class StageResponse:
    run_id: str
    stage: StageName
    status: JobStatus
    output_refs: list[str] = field(default_factory=list)
    warnings: list[AnalysisWarning] = field(default_factory=list)
    error: dict[str, Any] | None = None  # code, stage, retryable, message, trace_id


class Stage(Protocol):
    """분석 단계 구현이 지켜야 하는 형식."""

    name: StageName
    implementation: str  # 예: "fixture", "manual", "rules-v0", "rtmpose-coco"
    version: str

    def run(self, request: StageRequest) -> StageResponse: ...
