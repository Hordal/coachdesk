"""상태 판정 엔진(25의 3·4절): 가드 Feature와 규칙."""

from __future__ import annotations

from app.domain.analysis import judge_guard_return, AnalysisParams
from app.domain.models import MEDIAPIPE_LANDMARKS, PoseObservationRecord
from app.domain.states import StateParams, compute_features, judge_hand, judge_states

STEP = 33_333


def pose(t_idx: int, *, wrist_y: float, wrist_x: float = 510.0, elbow_ok: bool = True, vis: float = 0.9, pid: str | None = "candidate:0") -> PoseObservationRecord:
    """몸통 길이 100px인 정면 포즈. 코 (500,100), 어깨 y=200, 골반 y=300. 오른손목만 값을 바꾼다."""
    base = {
        "nose": (500, 100), "left_shoulder": (450, 200), "right_shoulder": (550, 200), "left_hip": (470, 300), "right_hip": (530, 300),
        "left_ankle": (440, 500), "right_ankle": (560, 500), "left_elbow": (420, 260), "left_wrist": (490, 120), "right_elbow": (580, 260),
        "right_wrist": (wrist_x, wrist_y),
    }
    marks = {}
    for i, name in enumerate(MEDIAPIPE_LANDMARKS):
        x, y = base.get(name, (500, 400))
        v = vis if name != "right_elbow" or elbow_ok else 0.1
        marks[name] = {"x_px": x, "y_px": y, "visibility": v, "presence": 0.9, "off_image": False, "finite": True}
    return PoseObservationRecord.model_validate({
        "frame_index": t_idx, "timestamp_us": t_idx * STEP, "participant_id": pid, "status": "FEATURE_ELIGIBLE", "landmarks": marks,
    })


P = StateParams()


def test_features_use_torso_normalisation_and_leave_missing_values_empty():
    f = compute_features(pose(0, wrist_y=130), P)
    assert round(f.torso, 3) == 100.0
    assert round(f.right.hand_height, 2) == 0.30  # 코(100)보다 30px 아래 / 몸통 100
    assert f.right.hand_forward is None and "hand_forward" in f.right.missing  # 상대 방향이 없으면 결측
    low_vis = compute_features(pose(0, wrist_y=130, vis=0.2), P)
    assert low_vis.unusable == "torso_invalid"
    no_pose = compute_features(PoseObservationRecord.model_validate({"frame_index": 0, "timestamp_us": 0, "landmarks": None}), P)
    assert no_pose.unusable == "no_pose"


def test_hand_labels_follow_the_provisional_thresholds():
    assert judge_hand(compute_features(pose(0, wrist_y=130), P).right, P) == ("high_guard", None)
    assert judge_hand(compute_features(pose(0, wrist_y=220), P).right, P) == ("low_guard", None)
    assert judge_hand(compute_features(pose(0, wrist_y=180), P).right, P)[0] == "other_guard"
    assert judge_hand(compute_features(pose(0, wrist_y=130), P).left, P)[0] == "high_guard"


def test_states_merge_runs_and_short_runs_become_unknown():
    rows = [pose(i, wrist_y=130) for i in range(0, 10)] + [pose(i, wrist_y=230) for i in range(10, 12)] + [pose(i, wrist_y=130) for i in range(12, 20)]
    out = judge_states(rows)
    right = [s for s in out.states if s["body_part"] == "right_hand"]
    assert [s["label"] for s in right] == ["high_guard", "high_guard"]  # 짧은 로우가드 사이를 잇지 않는다
    assert any(u["body_part"] == "right_hand" and u["reason_code"] == "too_short" for u in out.unknown)
    assert right[0]["start_us"] == 0 and right[0]["end_us"] == 10 * STEP
    assert all(s["state_category"] == "guard_hand" for s in out.states)


def test_unusable_pose_is_unknown_not_zero():
    rows = [pose(i, wrist_y=130, vis=0.1) for i in range(10)]
    out = judge_states(rows)
    assert out.states == [] and {u["reason_code"] for u in out.unknown} == {"torso_invalid"}


def test_rows_without_participant_are_ignored_and_candidates_stay_separate():
    rows = [pose(i, wrist_y=130, pid="candidate:0") for i in range(8)] + [pose(i, wrist_y=230, pid="candidate:1") for i in range(8)] + [pose(i, wrist_y=130, pid=None) for i in range(8)]
    out = judge_states(rows)
    by = {(s["participant_id"], s["body_part"]): s["label"] for s in out.states}
    assert by[("candidate:0", "right_hand")] == "high_guard" and by[("candidate:1", "right_hand")] == "low_guard"
    assert {s["participant_id"] for s in out.states} == {"candidate:0", "candidate:1"}


def test_rear_hand_needs_opponent_direction_and_stance():
    rows = [pose(i, wrist_y=130) for i in range(10)]
    assert not any(s["body_part"] == "rear_hand" for s in judge_states(rows).states)
    # 상대가 오른쪽(+1): 오른발이 더 나가 있으면 앞발은 오른쪽 → 뒷손은 왼손
    stance = [pose(i, wrist_y=130) for i in range(10)]
    for r in stance:
        r.landmarks["right_ankle"].x_px = 600.0  # 오른발이 상대 쪽으로 160px 더 나감
    out = judge_states(stance, opponent_dir={"candidate:0": 1})
    rear = [s for s in out.states if s["body_part"] == "rear_hand"]
    assert rear and rear[0]["label"] == "high_guard"
    # 앞뒤 차이가 작으면 스탠스 판단 불가
    for r in rows:
        r.landmarks["right_ankle"].x_px = 440.0
    same = judge_states(rows, opponent_dir={"candidate:0": 1})
    assert not any(s["body_part"] == "rear_hand" for s in same.states)
    assert any(u["body_part"] == "rear_hand" and u["reason_code"] == "stance_unknown" for u in same.unknown)


def test_states_feed_the_guard_return_rule():
    # 공격 뒤 뒷손이 낮다가 400ms 안에 하이가드로 돌아오면 지연 없음, 계속 낮으면 지연
    low = [pose(i, wrist_y=230) for i in range(0, 20)]
    high = [pose(i, wrist_y=130) for i in range(20, 40)]
    for r in low + high:
        r.landmarks["right_ankle"].x_px = 600.0
    # 오른발이 앞발이면 뒷손은 왼손: 왼손목 높이를 바꾼다
    for r in low:
        r.landmarks["left_wrist"].y_px = 230.0
    out = judge_states(low + high, opponent_dir={"candidate:0": 1})
    attack = {"participant_id": "candidate:0", "end_timestamp_us": 0}
    got = judge_guard_return(attack, out.states, AnalysisParams())
    assert got.value == "present" and got.delay_us == 20 * STEP


def test_hysteresis_keeps_the_state_near_the_boundary_and_still_switches_on_a_clear_change():
    # 코 y=100, 몸통 100: wrist_y 134~136은 손 높이 0.34~0.36(기준 0.35)에서 번갈아 흔들림
    wobble = [pose(i, wrist_y=134 if i % 2 else 136) for i in range(20)]
    off = judge_states(wobble, params=StateParams(hysteresis_margin=0.0))
    on = judge_states(wobble)
    left = lambda out: [s for s in out.states if s["body_part"] == "right_hand"]
    assert len(left(on)) == 1 and left(on)[0]["label"] == "high_guard"  # 하나의 연속 구간
    assert left(off) == []  # 끈 쪽은 33ms짜리 깜빡임뿐이라 모두 too_short
    # 분명히 기준을 벗어나면(여유보다 많이) 바뀐다
    moved = [pose(i, wrist_y=130) for i in range(10)] + [pose(i, wrist_y=170) for i in range(10, 20)]
    labels = [s["label"] for s in judge_states(moved).states if s["body_part"] == "right_hand"]
    assert labels == ["high_guard", "other_guard"]


def test_hysteresis_does_not_carry_across_unknown_or_gaps():
    rows = [pose(i, wrist_y=130) for i in range(8)] + [pose(i, wrist_y=130, vis=0.1) for i in range(8, 10)] + [pose(i, wrist_y=136 + 6) for i in range(10, 20)]
    out = judge_states(rows)
    labels = [s["label"] for s in out.states if s["body_part"] == "right_hand"]
    assert labels == ["high_guard", "other_guard"]  # 판단 불가를 지난 뒤에는 이전 하이가드를 이어받지 않고 엄격한 기준으로 판정
