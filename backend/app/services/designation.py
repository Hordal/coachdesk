"""선수 지정(상세 설계 11, 27의 9절).

가져온 검출·추적 결과의 트랙은 후보일 뿐이며, 어느 트랙이 사용자(USER)이고 상대(OPPONENT)인지는 사용자가 직접 고른다.
입력 순서, 화면의 좌우 위치, 트랙 번호의 크기로 신원을 정하지 않으며 같은 저장 결과와 같은 선택은 항상 같은 결과를 낸다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..domain.models import FrameTransform, Participant, TrackAssignment, TrackAssignmentSet, TrackRecord
from ..storage.db import Database, now_rfc3339
from .observations import ObservationRepo, ObservationSet

USER_PARTICIPANT_ID = "A"  # 가상 예제와 같은 표기. 사용자
OPPONENT_PARTICIPANT_ID = "B"
MAX_FRAMES_SHOWN = 5
SEPARATED_IOU = 0.1  # 후보 프레임을 고를 때 두 상자가 겹친 것으로 보는 기준. 판정이 아니라 보기 좋은 프레임을 고르는 용도다.


class DesignationError(Exception):
    def __init__(self, status: int, code: str, title: str, **extra: object) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.extra = status, code, title, extra


@dataclass(frozen=True)
class CandidateBox:
    track_id: int
    bbox_display: tuple[float, float, float, float]


@dataclass(frozen=True)
class CandidateFrame:
    frame_index: int
    time_us: int
    separated: bool  # 주요 후보 상자가 서로 겹치지 않은 프레임
    boxes: list[CandidateBox]


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def candidate_frames(tracks: list[TrackRecord], observation: ObservationSet, transform: FrameTransform) -> list[CandidateFrame]:
    """후보 트랙이 보기 좋게 보이는 대표 프레임.

    오래 보이고 화면에서 큰 트랙 두 개(보인 프레임 수 × 면적 중앙값이 큰 순서)가 함께 보이고 서로 겹치지 않는 프레임을 우선한다.
    배경 인물을 거르는 것이 아니라 보여 줄 프레임을 고르는 기준이며, 그런 프레임이 없으면 겹친 장면이라도 보여 준다(separated=false).
    같은 입력에서는 항상 같은 프레임이 나오도록 정렬 기준을 고정한다(프레임 번호, 트랙 번호).
    """
    ranked = [t.track_id for t in sorted(observation.tracks, key=lambda t: (-(t.frame_count * t.median_area_ratio), t.track_id))]
    leading = set(ranked[:2])
    separated: list[int] = []
    anywhere: list[int] = []
    for index, frame in enumerate(tracks):
        if not frame.tracks:
            continue
        anywhere.append(index)
        visible = [t for t in frame.tracks if t.track_id in leading]
        if len(visible) == 2 and _iou(visible[0].bbox, visible[1].bbox) < SEPARATED_IOU:
            separated.append(index)
    pool, flag = (separated, True) if separated else (anywhere, False)
    if not pool:
        return []
    count = min(MAX_FRAMES_SHOWN, len(pool))
    chosen = sorted({pool[round(i * (len(pool) - 1) / max(count - 1, 1))] for i in range(count)})
    result = []
    for index in chosen:
        boxes = []
        for t in sorted(tracks[index].tracks, key=lambda t: t.track_id):
            x1, y1 = transform.to_display(t.bbox[0], t.bbox[1])
            x2, y2 = transform.to_display(t.bbox[2], t.bbox[3])
            boxes.append(CandidateBox(t.track_id, (round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2))))
        result.append(CandidateFrame(index, tracks[index].timestamp_us, flag, boxes))
    return result

def check_selection(tracks: list[TrackRecord], known: set[int], user: list[int], opponent: list[int], *, solo: bool = False) -> None:
    """선택이 데이터와 모순되지 않는지 검사한다. 어긋나는 점을 모두 모아 알린다."""
    problems: list[str] = []
    if not user or (not solo and not opponent):
        raise DesignationError(422, "selection_incomplete", "사용자와 상대의 트랙을 모두 골라야 함")
    if solo and opponent:
        raise DesignationError(422, "solo_has_opponent", "개인 훈련에서는 상대를 지정하지 않습니다")
    for label, ids in (("사용자", user), ("상대", opponent)):
        if len(set(ids)) != len(ids):
            problems.append(f"{label} 트랙에 같은 번호가 두 번 있음")
    unknown = sorted((set(user) | set(opponent)) - known)
    if unknown:
        raise DesignationError(422, "unknown_track", "가져온 결과에 없는 트랙 번호", track_ids=unknown)
    both = sorted(set(user) & set(opponent))
    if both:
        raise DesignationError(422, "track_in_both", "한 트랙을 사용자와 상대에 모두 지정할 수 없음", track_ids=both)
    for label, ids in (("사용자", user), ("상대", opponent)):
        chosen = set(ids)
        for index, frame in enumerate(tracks):
            seen = [t.track_id for t in frame.tracks if t.track_id in chosen]
            if len(seen) > 1:
                # 한 선수의 조각들은 같은 프레임에 동시에 보일 수 없다.
                problems.append(f"{label}로 고른 트랙 {sorted(seen)}이 프레임 {index}에서 동시에 보임. 한 선수의 트랙은 겹치지 않아야 함")
                break
    if problems:
        raise DesignationError(422, "inconsistent_selection", "고른 트랙이 서로 맞지 않음", problems=problems)


class DesignationRepo:
    def __init__(self, db: Database, observations: ObservationRepo) -> None:
        self._db = db
        self._observations = observations

    def current(self, video_id: str, owner_id: str) -> tuple[TrackAssignmentSet, str | None]:
        """현재 지정. 선택하기 전에는 NOT_RUN이고 배정이 비어 있다."""
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM designations WHERE video_id = ? AND owner_id = ? ORDER BY created_at DESC, designation_id DESC LIMIT 1",
                (video_id, owner_id),
            ).fetchone()
        if row is None:
            return TrackAssignmentSet(), None
        latest = self._observations.latest(video_id, owner_id)
        if latest is None or latest.set_id != row["set_id"]:
            # 지정한 뒤 검출·추적 결과를 다시 가져왔다. 새 결과의 트랙 번호와 맞는다고 가정하지 않는다.
            return TrackAssignmentSet(), row["set_id"]
        return TrackAssignmentSet.model_validate_json(row["assignment_json"]), row["set_id"]

    def save(self, *, video_id: str, owner_id: str, observation: ObservationSet, user: list[int], opponent: list[int]) -> TrackAssignmentSet:
        assignment = TrackAssignmentSet(
            state="ASSIGNED",
            assignments=[TrackAssignment(participant_id=USER_PARTICIPANT_ID, role="USER", track_ids=sorted(user))]
            + ([TrackAssignment(participant_id=OPPONENT_PARTICIPANT_ID, role="OPPONENT", track_ids=sorted(opponent))] if opponent else []),
        )
        with self._db.tx(immediate=True) as conn:
            conn.execute(
                "INSERT INTO designations (designation_id, owner_id, video_id, set_id, assignment_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, owner_id, video_id, observation.set_id, assignment.model_dump_json(), now_rfc3339()),
            )
        return assignment

    @staticmethod
    def participants(assignment: TrackAssignmentSet) -> list[Participant]:
        return [Participant(participant_id=a.participant_id, role=a.role, track_ids=a.track_ids, user_designated=a.role == "USER") for a in assignment.assignments]
