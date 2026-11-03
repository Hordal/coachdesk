"""팀 연구 포즈 가져오기(상세 설계 27의 9절, 14의 PoseObservation·PoseQuality·WindowCandidates).

MMACoachAgent 연구 묶음의 `pose_observations.jsonl`·`pose_quality(_frames).jsonl`·`window_candidates.jsonl` 줄을 그대로 받는다.
영상의 프레임 시각과 맞지 않으면 저장하지 않고, 받은 값은 고치지 않는다. 33점·visibility·presence를 모두 보존한다.
`candidate:N`과 pose_index는 선수가 아니다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..domain.models import CORE_JOINTS, MEDIAPIPE_LANDMARKS, PoseImport, PoseObservationRecord, WindowCandidateRecord
from ..settings import Settings
from ..storage.db import Database, now_rfc3339

MAX_REPORTED = 20
MAX_ROWS = 300_000
_KNOWN = set(MEDIAPIPE_LANDMARKS)


class PosesRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors[0] if errors else "가져오기 거절")
        self.errors = errors


class PoseSummary(BaseModel):
    """가져온 포즈의 요약. 정확도가 아니라 어떤 줄이 얼마나 있는지만 센다."""

    row_count: int
    frame_count: int
    no_landmark_rows: int
    quality_row_count: int
    window_count: int
    clip_ids: list[str]
    status_counts: dict[str, int]
    mode_counts: dict[str, int]
    source_counts: dict[str, int]
    participant_ids: list[str]
    feature_ready_rows: int
    first_us: int | None
    last_us: int | None


@dataclass(frozen=True)
class PoseSet:
    set_id: str
    owner_id: str
    video_id: str
    content_sha256: str
    source: str
    summary: PoseSummary
    created_at: str


def validate(reference_pts_us: list[int], payload: PoseImport, tolerance_us: int) -> list[str]:
    """영상의 프레임 시각표와 가져온 줄을 대조한다. 어긋나는 곳을 모아 돌려준다."""
    errors: list[str] = []
    n = len(reference_pts_us)
    clips = sorted({r.clip_id for r in payload.poses if r.clip_id})
    if len(clips) > 1:  # 프레임 번호는 클립(영상)마다 다르므로 한 영상에 한 클립만 가져온다
        errors.append(f"한 번에 한 영상(clip_id)의 포즈만 가져올 수 있음: {clips}")

    def time_error(label: str, frame_index: int, timestamp_us: int) -> str | None:
        if frame_index >= n:
            return f"{label}: frame_index {frame_index}가 영상의 프레임 수({n}) 밖"
        if abs(reference_pts_us[frame_index] - timestamp_us) > tolerance_us:
            return f"{label}: frame_index {frame_index}의 시각({timestamp_us}µs)이 영상({reference_pts_us[frame_index]}µs)과 맞지 않음"
        return None

    for i, row in enumerate(payload.poses):
        label = f"poses[{i}]"
        problem = time_error(label, row.frame_index, row.timestamp_us)
        if problem:
            errors.append(problem)
        marks = row.landmarks
        if not marks:
            continue  # 포즈가 없는 프레임은 점을 채우지 않고 그대로 둔다
        unknown = sorted(set(marks) - _KNOWN)
        if unknown:
            errors.append(f"{label}: 알 수 없는 랜드마크 이름 {unknown[:5]}")
        missing = [name for name in CORE_JOINTS if name not in marks]
        if missing:
            errors.append(f"{label}: 핵심 관절이 없음 {missing}")
        for name, mark in marks.items():
            if mark.finite is False:
                continue  # 팀이 유한하지 않다고 표시한 점은 값 검사를 하지 않는다
            for field, value in (("x_px", mark.x_px), ("y_px", mark.y_px)):
                if value is not None and not math.isfinite(value):
                    errors.append(f"{label} {name}.{field}: 유한한 값이 아님")
            for field, value in (("visibility", mark.visibility), ("presence", mark.presence)):
                if value is not None and not (math.isfinite(value) and 0.0 <= value <= 1.0):
                    errors.append(f"{label} {name}.{field}: 0~1이어야 함")
    for i, row in enumerate(payload.quality):
        problem = time_error(f"quality[{i}]", row.frame_index, row.timestamp_us)
        if problem:
            errors.append(problem)
    for i, win in enumerate(payload.windows):
        if win.frame_start > win.frame_end or win.pts_start > win.pts_end:
            errors.append(f"windows[{i}]: 시작이 끝보다 늦음")
        elif win.frame_end >= n:
            errors.append(f"windows[{i}]: frame_end {win.frame_end}가 영상의 프레임 수({n}) 밖")
    return errors[: MAX_REPORTED * 5]


def summarize(payload: PoseImport) -> PoseSummary:
    rows = payload.poses
    stamps = [r.timestamp_us for r in rows]
    participants = sorted({r.participant_id for r in rows if r.participant_id})
    return PoseSummary(
        row_count=len(rows),
        frame_count=len({r.frame_index for r in rows}),
        no_landmark_rows=sum(1 for r in rows if not r.landmarks),
        quality_row_count=len(payload.quality),
        window_count=len(payload.windows),
        clip_ids=sorted({r.clip_id for r in rows if r.clip_id}),
        status_counts=dict(Counter(r.status for r in rows if r.status)),
        mode_counts=dict(Counter(r.mode for r in rows if r.mode)),
        source_counts=dict(Counter(r.source for r in rows if r.source)),
        participant_ids=participants,
        feature_ready_rows=sum(1 for q in payload.quality if q.feature_ready) + sum(1 for r in rows if r.status == "FEATURE_ELIGIBLE"),
        first_us=min(stamps) if stamps else None,
        last_us=max(stamps) if stamps else None,
    )


def content_digest(payload: PoseImport) -> str:
    return hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest()


class PoseRepo:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._dir = settings.poses_dir

    def directory(self, video_id: str) -> Path:
        return self._dir / video_id

    def _row(self, row: Any) -> PoseSet:
        return PoseSet(
            set_id=row["set_id"], owner_id=row["owner_id"], video_id=row["video_id"], content_sha256=row["content_sha256"],
            source=row["source"], summary=PoseSummary.model_validate_json(row["summary_json"]), created_at=row["created_at"],
        )

    def latest(self, video_id: str, owner_id: str) -> PoseSet | None:
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM pose_sets WHERE video_id = ? AND owner_id = ? ORDER BY created_at DESC, set_id DESC LIMIT 1", (video_id, owner_id)
            ).fetchone()
        return self._row(row) if row else None

    def get(self, video_id: str, owner_id: str, set_id: str) -> PoseSet | None:
        with self._db.tx() as conn:
            row = conn.execute("SELECT * FROM pose_sets WHERE video_id = ? AND owner_id = ? AND set_id = ?", (video_id, owner_id, set_id)).fetchone()
        return self._row(row) if row else None

    def by_hash(self, video_id: str, owner_id: str, digest: str) -> PoseSet | None:
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM pose_sets WHERE video_id = ? AND owner_id = ? AND content_sha256 = ?", (video_id, owner_id, digest)
            ).fetchone()
        return self._row(row) if row else None

    def create(self, *, owner_id: str, video_id: str, payload: PoseImport, digest: str) -> PoseSet:
        set_id = uuid.uuid4().hex
        directory = self.directory(video_id)
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"{set_id}.json"
        tmp = final.with_name(final.name + ".tmp")
        tmp.write_text(payload.model_dump_json(), encoding="utf-8")
        os.replace(tmp, final)  # 완성한 뒤에만 등록한다
        summary = summarize(payload)
        try:
            with self._db.tx(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO pose_sets (set_id, owner_id, video_id, content_sha256, source, summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (set_id, owner_id, video_id, digest, payload.source, summary.model_dump_json(), now_rfc3339()),
                )
        except sqlite3.IntegrityError:
            final.unlink(missing_ok=True)
            existing=self.by_hash(video_id,owner_id,digest)
            if existing is not None:
                return existing
            raise
        except Exception:
            final.unlink(missing_ok=True)
            raise
        found = self.by_hash(video_id, owner_id, digest)
        assert found is not None
        return found

    def load(self, video_id: str, set_id: str) -> PoseImport:
        return PoseImport.model_validate_json((self.directory(video_id) / f"{set_id}.json").read_text(encoding="utf-8"))

    def load_poses(self, video_id: str, set_id: str) -> list[PoseObservationRecord]:
        return self.load(video_id, set_id).poses

    def load_windows(self, video_id: str, set_id: str) -> list[WindowCandidateRecord]:
        return self.load(video_id, set_id).windows
