"""상태 판정 엔진(25의 3·4절). 33점 포즈에서 가드 Feature를 계산하고 규칙으로 StateInterval을 만든다.

입력은 팀 연구 포즈 줄(`PoseObservationRecord`)이다. 선수 신원은 정하지 않고 `candidate:N` 단위로 판정한다.
관절이 유효하지 않거나 기준값이 비어 있으면 상태를 만들지 않고 사유와 함께 판단 불가 구간으로 남긴다(0·직전 값으로 채우지 않음).
임시 매개변수는 25의 4-2절 값(team-provisional-v1)이며 전문가 확정 전 값이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from .models import PoseLandmark, PoseObservationRecord

GUARD_CATEGORY = "guard_hand"
USABLE_STATUS = "FEATURE_ELIGIBLE"


@dataclass(frozen=True)
class StateParams:
    min_visibility: float = 0.5  # 관절 유효 기준(임시. 25의 2절: 모델별 개발 검증으로 확정)
    high_hand_height_max: float = 0.35  # 코 아래로 내려간 손목 / 몸통
    high_hand_chin_max: float = 0.4
    low_hand_height_min: float = 1.0
    long_forward_min: float = 1.0
    long_elbow_min_deg: float = 150.0
    front_foot_min_diff: float = 0.1  # 앞발 판정 최소 앞뒤 차이(몸통)
    hysteresis_margin: float = 0.09  # 현재 상태를 유지하는 여유(몸통 길이 단위). 손목 좌표 잡음 σ≈0.046의 2배(35의 4절). 0이면 끔
    min_duration_us: int = 200_000
    max_gap_us: int = 400_000  # 이보다 벌어진 관측은 같은 구간으로 잇지 않는다
    rule_version: str = "team-provisional-v1"


@dataclass(frozen=True)
class SideFeatures:
    hand_height: Optional[float] = None
    hand_chin: Optional[float] = None
    elbow_angle: Optional[float] = None
    hand_forward: Optional[float] = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameFeatures:
    timestamp_us: int
    frame_index: int
    torso: Optional[float]
    left: SideFeatures
    right: SideFeatures
    front_side: Optional[str]  # "left" | "right" | None(스탠스 판단 불가)
    unusable: Optional[str] = None  # 프레임 전체를 못 쓰는 사유


@dataclass(frozen=True)
class Judgement:
    """한 프레임의 한 손 판정. label이 None이면 판단 불가이고 reason에 사유가 있다."""

    timestamp_us: int
    label: Optional[str]
    reason: Optional[str] = None


@dataclass
class StateResult:
    states: list[dict] = field(default_factory=list)
    unknown: list[dict] = field(default_factory=list)  # 판단 불가 구간(사유별)


def _valid(mark: Optional[PoseLandmark], params: StateParams) -> bool:
    if mark is None or mark.finite is False or mark.off_image:
        return False
    if mark.x_px is None or mark.y_px is None:
        return False
    if not math.isfinite(mark.x_px) or not math.isfinite(mark.y_px):
        return False
    if mark.state is not None and mark.state != "VALID":
        return False
    if mark.presence is not None and (not math.isfinite(mark.presence) or mark.presence < params.min_visibility):
        return False
    return mark.visibility is not None and mark.visibility >= params.min_visibility


def _pt(mark: PoseLandmark) -> tuple[float, float]:
    return float(mark.x_px), float(mark.y_px)  # type: ignore[arg-type]


def _angle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> Optional[float]:
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def compute_features(row: PoseObservationRecord, params: StateParams, opponent_dir: Optional[int] = None) -> FrameFeatures:
    """포즈 한 줄의 가드·스탠스 Feature. opponent_dir은 상대가 화면 오른쪽(+1)·왼쪽(-1)에 있는지이며 없으면 앞/뒤 의존 Feature는 결측이다."""
    marks = row.landmarks or {}
    empty = SideFeatures(missing=("pose",))
    reasons = row.model_extra.get("reasons", []) if row.model_extra else []
    if row.status in {"REJECTED", "NOT_RUN", "IDENTITY_UNRESOLVED"} or any("CONTAMINATION" in str(r) or "IDENTITY" in str(r) for r in reasons):
        return FrameFeatures(row.timestamp_us, row.frame_index, None, empty, empty, None, unusable="pose_rejected")
    if not marks:
        return FrameFeatures(row.timestamp_us, row.frame_index, None, empty, empty, None, unusable="no_pose")
    need = ("nose", "left_shoulder", "right_shoulder", "left_hip", "right_hip")
    if not all(_valid(marks.get(n), params) for n in need):
        return FrameFeatures(row.timestamp_us, row.frame_index, None, empty, empty, None, unusable="torso_invalid")
    ls, rs, lh, rh = (_pt(marks[n]) for n in need[1:])
    nose = _pt(marks["nose"])
    sh = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    hip = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    torso = math.hypot(sh[0] - hip[0], sh[1] - hip[1])
    if torso <= 0:
        return FrameFeatures(row.timestamp_us, row.frame_index, None, empty, empty, None, unusable="torso_invalid")
    chin = ((nose[0] + sh[0]) / 2, (nose[1] + sh[1]) / 2)  # 턱 추정점: 코와 어깨 중심의 중간

    def side(prefix: str) -> SideFeatures:
        wrist = marks.get(f"{prefix}_wrist")
        elbow = marks.get(f"{prefix}_elbow")
        shoulder = marks.get(f"{prefix}_shoulder")
        missing: list[str] = []
        height = chin_d = forward = elbow_a = None
        if _valid(wrist, params):
            w = _pt(wrist)  # type: ignore[arg-type]
            height = (w[1] - nose[1]) / torso  # 화면 y는 아래로 증가: 코보다 아래로 내려갈수록 큼
            chin_d = math.hypot(w[0] - chin[0], w[1] - chin[1]) / torso
            if opponent_dir in (1, -1):
                forward = (w[0] - hip[0]) * opponent_dir / torso
            else:
                missing.append("hand_forward")
            if _valid(elbow, params) and _valid(shoulder, params):
                elbow_a = _angle(_pt(shoulder), _pt(elbow), w)  # type: ignore[arg-type]
            else:
                missing.append("elbow_angle")
        else:
            missing += ["hand_height", "hand_chin", "hand_forward", "elbow_angle"]
        return SideFeatures(height, chin_d, elbow_a, forward, tuple(missing))

    front = None
    if opponent_dir in (1, -1):
        la, ra = marks.get("left_ankle"), marks.get("right_ankle")
        if _valid(la, params) and _valid(ra, params):
            diff = ((_pt(la)[0] - _pt(ra)[0]) * opponent_dir) / torso  # 양수: 왼발이 상대 쪽으로 더 나감
            if abs(diff) >= params.front_foot_min_diff:
                front = "left" if diff > 0 else "right"
    return FrameFeatures(row.timestamp_us, row.frame_index, torso, side("left"), side("right"), front)


def _holds(label: str, feat: SideFeatures, params: StateParams) -> bool:
    """이전 상태(하이·로우가드)가 여유(hysteresis_margin)만큼 완화한 조건에서도 유지되는지."""
    m = params.hysteresis_margin
    if feat.hand_height is None or feat.hand_chin is None:
        return False
    if label == "high_guard":
        return feat.hand_height <= params.high_hand_height_max + m and feat.hand_chin <= params.high_hand_chin_max + m
    if label == "low_guard":
        return feat.hand_height >= params.low_hand_height_min - m
    return False


def judge_hand(feat: SideFeatures, params: StateParams, previous: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """손 하나의 가드 라벨과 판단 불가 사유. 하이가드 → 롱가드 → 로우가드 순. 어느 쪽도 아니면 `other_guard`.

    `previous`(바로 앞 프레임의 하이·로우가드)가 있으면 경계에서 번갈아 바뀌지 않도록 완화한 조건에서 유지한다(히스테리시스).
    """
    if feat.hand_height is None or feat.hand_chin is None:
        return None, "missing_input"
    if previous and _holds(previous, feat, params):
        return previous, None
    if feat.hand_height <= params.high_hand_height_max and feat.hand_chin <= params.high_hand_chin_max:
        return "high_guard", None
    if feat.hand_height >= params.low_hand_height_min:
        return "low_guard", None
    if feat.hand_forward is not None and feat.elbow_angle is not None and feat.hand_forward >= params.long_forward_min and feat.elbow_angle >= params.long_elbow_min_deg:
        return "long_guard", None
    return "other_guard", None


def _judge_run(feats: Sequence["FrameFeatures"], pick, params: StateParams) -> list[Judgement]:
    """프레임 순서대로 판정한다. 앞 프레임의 라벨을 히스테리시스에 넘기며, 판단 불가이거나 관측 간격이 벌어지면 이어받지 않는다.

    `pick(frame_features) -> (SideFeatures | None, reason | None, 손 이름)`: 판정할 손을 고른다. 손이 바뀌면(앞발이 바뀐 경우) 이전 라벨을 이어받지 않는다.
    """
    out: list[Judgement] = []
    prev: Optional[str] = None
    prev_t: Optional[int] = None
    prev_hand: Optional[str] = None
    for f in feats:
        side, reason, hand = (None, f.unusable, None) if f.unusable else pick(f)
        if side is None:
            out.append(Judgement(f.timestamp_us, None, reason))
            prev = None
        else:
            if (prev_t is not None and f.timestamp_us - prev_t > params.max_gap_us) or hand != prev_hand:
                prev = None
            prev_hand = hand
            label, why = judge_hand(side, params, prev)
            out.append(Judgement(f.timestamp_us, label, why))
            prev = label
        prev_t = f.timestamp_us
    return out


def _pick_rows(rows: Iterable[PoseObservationRecord]) -> dict[str, list[PoseObservationRecord]]:
    """선수 후보별로 묶고, 같은 프레임에 여러 줄이면 팀이 쓸 수 있다고 한 줄을 먼저 쓴다."""
    by_pid: dict[str, dict[int, PoseObservationRecord]] = {}
    for r in rows:
        if not r.participant_id:
            continue
        slot = by_pid.setdefault(r.participant_id, {})
        old = slot.get(r.frame_index)
        better = old is None or (old.status != USABLE_STATUS and r.status == USABLE_STATUS) or (not old.landmarks and bool(r.landmarks))
        if better:
            slot[r.frame_index] = r
    return {pid: sorted(slot.values(), key=lambda r: r.timestamp_us) for pid, slot in by_pid.items()}


def _intervals(seq: Sequence[Judgement], params: StateParams, end_us: Sequence[int]) -> list[tuple[Optional[str], Optional[str], int, int]]:
    """같은 판정이 이어진 구간으로 묶는다. 관측 사이가 max_gap_us보다 벌어지면 끊는다."""
    out: list[list] = []
    for j, e in zip(seq, end_us):
        key = (j.label, j.reason)
        if out and out[-1][0] == key[0] and out[-1][1] == key[1] and j.timestamp_us - out[-1][3] <= params.max_gap_us:
            out[-1][3] = e
        else:
            out.append([key[0], key[1], j.timestamp_us, e])
    return [(a, b, c, d) for a, b, c, d in out]


def judge_states(
    rows: Iterable[PoseObservationRecord],
    *,
    params: StateParams = StateParams(),
    opponent_dir: Optional[Mapping[str, int]] = None,
) -> StateResult:
    """포즈 줄에서 선수 후보별 가드 상태 구간을 만든다.

    `opponent_dir`(후보 ID → 상대가 있는 방향 +1/-1)가 있으면 앞/뒤 손(`front_hand`·`rear_hand`)도 만든다. 없으면 `left_hand`·`right_hand`만 만든다.
    min_duration보다 짧게 이어진 상태는 버리지 않고 판단 불가(`too_short`)로 남긴다.
    """
    result = StateResult()
    for pid, seq in _pick_rows(rows).items():
        direction = (opponent_dir or {}).get(pid)
        feats = [compute_features(r, params, direction) for r in seq]
        # 관측 한 줄의 끝: 다음 관측 시각(간격 이내) 또는 자기 시각
        ends = [
            seq[i + 1].timestamp_us if i + 1 < len(seq) and seq[i + 1].timestamp_us - seq[i].timestamp_us <= params.max_gap_us else seq[i].timestamp_us
            for i in range(len(seq))
        ]
        for body, side_name in (("left_hand", "left"), ("right_hand", "right")):
            judged = _judge_run(feats, lambda f, s=side_name: (f.left if s == "left" else f.right, None, s), params)
            _emit(result, pid, body, judged, ends, params)
        if direction in (1, -1):
            for role in ("front_hand", "rear_hand"):
                def pick(f: FrameFeatures, role: str = role):
                    if f.front_side is None:
                        return None, "stance_unknown", None
                    want = f.front_side if role == "front_hand" else ("right" if f.front_side == "left" else "left")
                    return (f.left if want == "left" else f.right), None, want
                _emit(result, pid, role, _judge_run(feats, pick, params), ends, params)
    return result


def _emit(result: StateResult, pid: str, body: str, judged: Sequence[Judgement], ends: Sequence[int], params: StateParams) -> None:
    n = len(result.states)
    for label, reason, start, end in _intervals(judged, params, ends):
        if label is None:
            result.unknown.append({"participant_id": pid, "body_part": body, "reason_code": reason, "start_us": start, "end_us": end})
        elif end - start < params.min_duration_us:
            result.unknown.append({"participant_id": pid, "body_part": body, "reason_code": "too_short", "start_us": start, "end_us": end})
        else:
            n += 1
            result.states.append({
                "state_id": f"st_{pid.replace(':', '-')}_{body}_{start}", "participant_id": pid, "body_part": body,
                "state_category": GUARD_CATEGORY, "label": label, "start_us": start, "end_us": end,
            })
