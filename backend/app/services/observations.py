"""선수 검출·추적 결과 가져오기(상세 설계 27의 9절).

MMACoachAgent 저장소의 `ai/`가 만든 `metadata.json`, 검출·추적 프레임을 읽기 전용으로 받는다. 받은 내용은 고치지 않고
그대로 저장하며, 영상의 프레임 표와 시각이 맞지 않으면 저장하지 않는다. 트랙은 후보일 뿐 선수가 아니다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import uuid
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..domain.models import DetectionRecord, FrameTransform, ObservationImport, TrackRecord
from ..settings import Settings
from ..storage.db import Database, now_rfc3339

MAX_REPORTED = 20
MAX_FRAMES = 400_000
SOURCE_TOLERANCE_US = 2  # 원본의 프레임 시각과 비교할 때(같은 PTS에서 온 값이라 반올림 차이만 허용)
PLAYBACK_TOLERANCE_US = 1_000  # 재생용 사본과 비교할 때. 영상 준비 단계가 확인한 원본↔사본 시각 대응 한도와 같다


class ObservationsRejected(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors[0] if errors else "가져오기 거절")
        self.errors = errors


class TrackSummary(BaseModel):
    """후보를 보여 줄 때 참고하는 진단 값. 선수 신원을 판정하지 않는다."""

    track_id: int
    first_us: int
    last_us: int
    frame_count: int
    missing_frames: int  # 첫 관측과 마지막 관측 사이에서 이 트랙이 없는 프레임 수
    longest_gap_us: int
    median_area_ratio: float  # 분석 프레임 면적 대비 상자 면적의 중앙값
    mean_score: float | None


@dataclass(frozen=True)
class ObservationSet:
    set_id: str
    owner_id: str
    video_id: str
    content_sha256: str
    frame_count: int
    detection_count: int
    track_count: int
    source: str
    metadata: dict[str, Any]
    tracks: list[TrackSummary]
    created_at: str


# --- 검증 ---------------------------------------------------------------------------

def _bbox_error(label: str, bbox: tuple[float, float, float, float]) -> str | None:
    if not all(math.isfinite(v) for v in bbox):
        return f"{label}: 상자 좌표가 유한한 값이 아님"
    left, top, right, bottom = bbox
    if not (left < right and top < bottom):
        return f"{label}: 상자가 left < right, top < bottom이어야 함 {tuple(bbox)}"
    return None


def validate(reference_pts_us: list[int], payload: ObservationImport, tolerance_us: int = SOURCE_TOLERANCE_US) -> list[str]:
    """영상의 프레임 표와 가져온 내용을 대조한다. 어긋나는 곳을 모두 모아 돌려준다.

    기준 표는 분석 파이프라인이 읽은 원본 영상의 프레임 시각이다. 원본이 없으면 재생용 사본의 표를 쓰고 그 한도를 넓힌다.
    """
    errors: list[str] = []
    meta = payload.metadata
    stamps = meta.frame_timestamps_us

    if not stamps:
        errors.append("metadata.frame_timestamps_us가 없음")
    elif any(s is None for s in stamps):
        errors.append("PTS가 없는 프레임이 있어 시각을 맞출 수 없음")
    if meta.transform is None:
        errors.append("metadata.transform이 없어 분석 좌표를 영상 좌표로 되돌릴 수 없음")
    if meta.frame_count is not None and stamps and meta.frame_count != len(stamps):
        errors.append(f"metadata.frame_count({meta.frame_count})와 frame_timestamps_us 길이({len(stamps)})가 다름")
    if meta.derivative_frame_count is not None and meta.frame_count is not None and meta.derivative_frame_count != meta.frame_count:
        # 분석 영상의 프레임 수가 원본 PTS 수와 다르면 같은 번호의 시각 대응을 믿을 수 없다.
        errors.append(f"분석 영상 프레임 수({meta.derivative_frame_count})가 원본 PTS 수({meta.frame_count})와 달라 프레임 번호로 시각을 맞출 수 없음")
    if errors:
        return errors[:MAX_REPORTED]

    times = [int(s) for s in stamps if s is not None]
    if any(b <= a for a, b in zip(times, times[1:])):
        errors.append("frame_timestamps_us가 증가하지 않음")
    if len(times) != len(reference_pts_us):
        errors.append(f"프레임 수가 영상과 다름(가져온 값 {len(times)}, 영상 {len(reference_pts_us)})")
    elif times:
        offset = reference_pts_us[0] - times[0]  # 시작 시각이 옮겨졌을 수 있다. 상수 차이만 허용한다.
        for index, (mine, theirs) in enumerate(zip(reference_pts_us, times)):
            if abs((mine - offset) - theirs) > tolerance_us:
                errors.append(f"프레임 {index}의 시각이 영상과 맞지 않음(영상 {mine - offset}µs, 가져온 값 {theirs}µs)")
                break

    for kind, frames in (("tracks", payload.tracks), ("detections", payload.detections)):
        if kind == "detections" and not frames:
            continue  # 검출은 선택 사항이다
        if len(frames) != len(times):
            errors.append(f"{kind} 프레임 수({len(frames)})가 frame_timestamps_us({len(times)})와 다름")
            continue
        for index, frame in enumerate(frames):
            if frame.frame_index is not None and frame.frame_index != index:
                errors.append(f"{kind}[{index}]의 frame_index({frame.frame_index})가 줄 순서와 다름")
                break
            if frame.timestamp_us != times[index]:
                errors.append(f"{kind}[{index}]의 시각({frame.timestamp_us})이 frame_timestamps_us({times[index]})와 다름")
                break

    transform = meta.transform
    for index, dframe in enumerate(payload.detections):
        # 검출 좌표는 분석 프레임이다. 크기와 좌표 공간이 변환 정보와 다르면 좌표를 믿을 수 없다.
        if transform is not None and dframe.width is not None and (dframe.width, dframe.height) != (transform.analysis_width, transform.analysis_height):
            errors.append(f"detections[{index}]의 프레임 크기({dframe.width}×{dframe.height})가 분석 프레임({transform.analysis_width}×{transform.analysis_height})과 다름")
            break
        if dframe.coordinate_space is not None and dframe.coordinate_space != "analysis_frame_xyxy_px":
            errors.append(f"detections[{index}]의 coordinate_space({dframe.coordinate_space})를 지원하지 않음")
            break

    for index, tframe in enumerate(payload.tracks):
        seen: set[int] = set()
        for track in tframe.tracks:
            if track.track_id in seen:
                errors.append(f"tracks[{index}]: track_id {track.track_id}가 한 프레임에 두 번 나옴")
            seen.add(track.track_id)
            problem = _bbox_error(f"tracks[{index}] track {track.track_id}", track.bbox)
            if problem:
                errors.append(problem)
            if track.confidence is not None and not (math.isfinite(track.confidence) and 0.0 <= track.confidence <= 1.0):
                errors.append(f"tracks[{index}] track {track.track_id}: confidence는 0~1이어야 함")
    for index, dframe in enumerate(payload.detections):
        for n, det in enumerate(dframe.detections):
            if not (math.isfinite(det.confidence) and 0.0 <= det.confidence <= 1.0):
                errors.append(f"detections[{index}][{n}]: confidence는 0~1이어야 함")
            problem = _bbox_error(f"detections[{index}][{n}]", det.bbox)
            if problem:
                errors.append(problem)
    return errors[: MAX_REPORTED * 5]


# --- 후보 트랙 진단 ---------------------------------------------------------------------

def summarize_tracks(tracks: list[TrackRecord], transform: FrameTransform) -> list[TrackSummary]:
    area = float(transform.analysis_width * transform.analysis_height)
    by_track: dict[int, list[tuple[int, int, float, float | None]]] = {}
    for index, frame in enumerate(tracks):
        for track in frame.tracks:
            left, top, right, bottom = track.bbox
            by_track.setdefault(track.track_id, []).append((index, frame.timestamp_us, (right - left) * (bottom - top) / area, track.confidence))
    summaries: list[TrackSummary] = []
    for track_id, rows in sorted(by_track.items()):
        indexes = [r[0] for r in rows]
        stamps = [r[1] for r in rows]
        gaps = [stamps[i + 1] - stamps[i] for i in range(len(rows) - 1) if indexes[i + 1] - indexes[i] > 1]
        scores = [r[3] for r in rows if r[3] is not None]
        summaries.append(
            TrackSummary(
                track_id=track_id,
                first_us=stamps[0],
                last_us=stamps[-1],
                frame_count=len(rows),
                missing_frames=(indexes[-1] - indexes[0] + 1) - len(rows),
                longest_gap_us=max(gaps, default=0),
                median_area_ratio=round(statistics.median(r[2] for r in rows), 6),
                mean_score=round(sum(scores) / len(scores), 6) if scores else None,
            )
        )
    return summaries


# --- 저장 ---------------------------------------------------------------------------

class ObservationRepo:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._dir = settings.observations_dir

    def directory(self, video_id: str) -> Path:
        return self._dir / video_id

    def _row(self, row: Any) -> ObservationSet:
        return ObservationSet(
            set_id=row["set_id"], owner_id=row["owner_id"], video_id=row["video_id"], content_sha256=row["content_sha256"],
            frame_count=row["frame_count"], detection_count=row["detection_count"], track_count=row["track_count"],
            source=row["source"], metadata=json.loads(row["metadata_json"]),
            tracks=[TrackSummary.model_validate(t) for t in json.loads(row["summaries_json"])], created_at=row["created_at"],
        )

    def latest(self, video_id: str, owner_id: str) -> ObservationSet | None:
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM observation_sets WHERE video_id = ? AND owner_id = ? ORDER BY created_at DESC, set_id DESC LIMIT 1",
                (video_id, owner_id),
            ).fetchone()
        return self._row(row) if row else None

    def by_hash(self, video_id: str, owner_id: str, digest: str) -> ObservationSet | None:
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM observation_sets WHERE video_id = ? AND owner_id = ? AND content_sha256 = ?", (video_id, owner_id, digest)
            ).fetchone()
        return self._row(row) if row else None

    def create(self, *, owner_id: str, video_id: str, payload: ObservationImport, digest: str, transform: FrameTransform) -> ObservationSet:
        set_id = uuid.uuid4().hex
        directory = self.directory(video_id)
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"{set_id}.json"
        tmp = final.with_name(final.name + ".tmp")
        body = {"tracks": [f.model_dump(mode="json") for f in payload.tracks], "detections": [f.model_dump(mode="json") for f in payload.detections]}
        tmp.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, final)  # 완성한 뒤에만 등록한다
        # 분석을 만든 컴퓨터의 로컬 경로는 저장하지 않는다.
        metadata = payload.metadata.model_dump(mode="json", exclude={"derivative_path"})
        summaries = summarize_tracks(payload.tracks, transform)
        try:
            with self._db.tx(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO observation_sets (set_id, owner_id, video_id, content_sha256, frame_count, detection_count, track_count, source, metadata_json, summaries_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        set_id, owner_id, video_id, digest, len(payload.tracks), sum(len(f.detections) for f in payload.detections),
                        len(summaries), payload.source, json.dumps(metadata, ensure_ascii=False),
                        json.dumps([s.model_dump() for s in summaries]), now_rfc3339(),
                    ),
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

    def load_tracks(self, video_id: str, set_id: str) -> list[TrackRecord]:
        data = json.loads((self.directory(video_id) / f"{set_id}.json").read_text(encoding="utf-8"))
        return [TrackRecord.model_validate(f) for f in data["tracks"]]

    def load_detections(self, video_id: str, set_id: str) -> list[DetectionRecord]:
        data = json.loads((self.directory(video_id) / f"{set_id}.json").read_text(encoding="utf-8"))
        return [DetectionRecord.model_validate(f) for f in data["detections"]]


def content_digest(payload: ObservationImport) -> str:
    return hashlib.sha256(payload.model_dump_json(exclude={"metadata": {"derivative_path"}}).encode("utf-8")).hexdigest()
