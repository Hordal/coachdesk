"""선수 검출·추적·포즈 파이프라인과 맞춘 데이터 형식(상세 설계 14, 27의 9절)."""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.domain.models import (
    CORE_JOINTS,
    AnalysisWarning,
    DetectionFrame,
    FrameTransform,
    ModuleOutcome,
    Participant,
    PoseFrame,
    PoseSet,
    TrackAssignment,
    TrackAssignmentSet,
    TrackFrame,
    blank_keypoints,
)
from app.pipeline.stages import StageResponse, StageName
from scripts.export_schema import OUT

TRANSFORM = FrameTransform(
    coded_width=1920, coded_height=1080, display_width=1920, display_height=1080,
    analysis_width=1280, analysis_height=720, scale=1280 / 1920,
)


def test_core_joints_are_the_thirteen_named_points():
    assert len(CORE_JOINTS) == 13 and len(set(CORE_JOINTS)) == 13
    assert CORE_JOINTS[0] == "nose" and "left_ear" not in CORE_JOINTS and "left_heel" not in CORE_JOINTS


def test_blank_keypoints_use_null_not_zero():
    blank = blank_keypoints()
    assert set(blank) == set(CORE_JOINTS) and all(v == (None, None, None) for v in blank.values())


def test_pose_frame_requires_every_core_joint_but_allows_extra_points():
    assert PoseFrame(timestamp_us=0, participant_id="A", keypoints=blank_keypoints()).status == "VALID"
    partial = {k: v for k, v in blank_keypoints().items() if k != "nose"}
    with pytest.raises(ValidationError):
        PoseFrame(timestamp_us=0, participant_id="A", keypoints=partial)
    extra = PoseFrame(timestamp_us=0, participant_id="A", keypoints={**blank_keypoints(), "left_heel": (1.0, 2.0, 0.5)})
    assert extra.keypoints["left_heel"] == (1.0, 2.0, 0.5)  # 발 점은 추가 관절로 보존


def test_fit_follows_the_letterbox_rule_of_the_ai_pipeline():
    # 가로 영상을 같은 비율의 분석 크기로: 패딩 없음
    wide = FrameTransform.fit(1920, 1080, 0, 1280, 720)
    assert (wide.scale, wide.pad_x, wide.pad_y) == (pytest.approx(2 / 3), 0.0, 0.0)
    # 세로 영상: 높이에 맞추고 좌우를 가운데 정렬로 채운다
    portrait = FrameTransform.fit(1080, 1920, 0, 1280, 720)
    assert (portrait.scale, portrait.pad_x, portrait.pad_y) == (0.375, 437.5, 0.0)
    assert (portrait.display_width, portrait.display_height) == (1080, 1920)
    # 회전 90도: 표시 크기는 가로·세로가 바뀐다
    rotated = FrameTransform.fit(1920, 1080, 90, 1280, 720)
    assert (rotated.display_width, rotated.display_height, rotated.rotation_degrees) == (1080, 1920, 90)
    assert rotated.scale == 0.375 and rotated.pad_x == 437.5
    # 같은 영상을 270도로 표시해도 같은 크기이고, 360도는 0도다
    assert FrameTransform.fit(1920, 1080, 270, 1280, 720).display_width == 1080
    assert FrameTransform.fit(1920, 1080, 360, 1280, 720).rotation_degrees == 0
    with pytest.raises(ValueError):
        FrameTransform.fit(1920, 1080, 45, 1280, 720)
    with pytest.raises(ValidationError):
        FrameTransform(coded_width=1, coded_height=1, display_width=1, display_height=1, analysis_width=1, analysis_height=1, scale=1.0, rotation_degrees=45)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_analysis_coordinates_return_to_the_coded_frame_for_every_rotation(rotation):
    coded_w, coded_h = 1920, 1080
    t = FrameTransform.fit(coded_w, coded_h, rotation, 1280, 720)

    def to_display_forward(cx: float, cy: float) -> tuple[float, float]:
        # 부호화 좌표에 시계 방향 회전을 적용해 표시 좌표를 만든다
        if rotation == 0:
            return cx, cy
        if rotation == 90:
            return coded_h - cy, cx
        if rotation == 180:
            return coded_w - cx, coded_h - cy
        return cy, coded_w - cx

    for cx, cy in ((0.0, 0.0), (100.0, 40.0), (1919.0, 1079.0), (960.0, 540.0)):
        analysis = t.to_analysis(*to_display_forward(cx, cy))
        assert t.to_coded(*analysis) == pytest.approx((cx, cy))


def test_frame_transform_round_trips_with_scale_and_padding():
    letterbox = FrameTransform.fit(1080, 1920, 0, 1280, 720)
    for x, y in ((0.0, 0.0), (540.0, 960.0), (1079.5, 1919.0)):
        ax, ay = letterbox.to_analysis(x, y)
        assert 0 <= ax <= 1280 and 0 <= ay <= 720
        assert letterbox.to_display(ax, ay) == pytest.approx((x, y))
    assert TRANSFORM.to_display(*TRANSFORM.to_analysis(100.0, 50.0)) == pytest.approx((100.0, 50.0))


def test_unknown_fields_and_bad_values_are_rejected():
    with pytest.raises(ValidationError):
        PoseFrame(timestamp_us=0, participant_id="A", keypoints=blank_keypoints(), status="valid")  # 소문자 상태 값
    with pytest.raises(ValidationError):
        FrameTransform(coded_width=1, coded_height=1, display_width=1, display_height=1, analysis_width=1, analysis_height=1, scale=0)
    with pytest.raises(ValidationError):
        Participant(participant_id="A", role="unknown")  # 역할은 USER 또는 OPPONENT뿐


def test_track_id_is_not_a_participant_id():
    frame = TrackFrame.model_validate({"timestamp_us": 33_333, "tracks": [{"track_id": 7, "bbox_xyxy": [1, 2, 3, 4], "tracking_score": 0.9}]})
    assert frame.tracks[0].track_id == 7 and frame.status == "VALID"
    with pytest.raises(ValidationError):
        TrackFrame.model_validate({"timestamp_us": 0, "tracks": [{"track_id": 7, "participant_id": "A", "bbox_xyxy": [1, 2, 3, 4]}]})


def test_assignment_is_not_run_until_the_user_selects():
    empty = TrackAssignmentSet()
    assert empty.state == "NOT_RUN" and empty.assignments == []
    with pytest.raises(ValidationError):
        TrackAssignment(participant_id="A", role="USER", track_ids=[])  # 트랙 없는 배정은 만들 수 없다
    chosen = TrackAssignmentSet(state="ASSIGNED", assignments=[TrackAssignment(participant_id="A", role="USER", track_ids=[1, 4])])
    assert chosen.assignments[0].track_ids == [1, 4]  # 한 선수에 트랙 여러 개


def test_detection_frame_defaults():
    frame = DetectionFrame.model_validate({"timestamp_us": 0, "detections": [{"detection_id": "d1", "bbox_xyxy": [0, 0, 10, 20], "score": 0.8}]})
    assert frame.detections[0].class_name == "person"


def test_stage_warnings_are_structured():
    warning = AnalysisWarning(module="tracker", level="WARN", message="신뢰도 0.4 미만 검출은 검증하지 못함")
    assert warning.timestamp_us is None
    response = StageResponse(run_id="r", stage=StageName.TRACKER, status="completed", warnings=[warning])
    assert response.warnings[0].level == "WARN"
    with pytest.raises(ValidationError):
        AnalysisWarning(module="x", level="warning", message="m")
    assert ModuleOutcome(status="UNKNOWN", message="입력 없음").version is None


def test_exchange_schema_is_exported_and_accepts_valid_data():
    schema = json.loads((OUT / "observations.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    defs = schema["$defs"]
    assert {"VideoMetadata", "DetectionRecord", "TrackRecord", "PoseSet", "TrackAssignmentSet"} <= set(defs)
    pose = PoseSet(
        landmark_set_id="core13", coordinate_space_id="analysis_px:1280x720", transform=TRANSFORM,
        frames=[PoseFrame(timestamp_us=0, participant_id="A", keypoints=blank_keypoints())],
    ).model_dump(mode="json")
    validator = Draft202012Validator({"$ref": "#/$defs/PoseSet", "$defs": defs})
    assert list(validator.iter_errors(pose)) == []
    pose["frames"][0]["status"] = "valid"
    assert list(validator.iter_errors(pose))
