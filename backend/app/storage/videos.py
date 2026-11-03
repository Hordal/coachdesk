"""업로드한 영상의 기록(상세 설계 14의 Video)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .db import Database, now_rfc3339


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    owner_id: str
    filename: str
    sport: str
    source_path: str
    size_bytes: int
    sha256: str
    duration_us: int | None
    width: int | None
    height: int | None
    status: str  # processing | ready | failed | cancelled
    created_at: str
    media_state: str = "present"  # present | expired | deleted: 원본·재생용 사본이 남아 있는지
    media_removed_at: str | None = None
    deleting: bool = False  # 영상과 결과 전체 삭제가 진행 중이면 조회를 막는다
    ready_at: str | None = None


def _record(row: sqlite3.Row) -> VideoRecord:
    data = {k: row[k] for k in row.keys()}
    data["deleting"] = bool(data["deleting"])
    return VideoRecord(**data)


class VideoRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, video_id: str) -> VideoRecord | None:
        with self._db.tx() as conn:
            row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        return _record(row) if row else None

    def get_for_owner(self, video_id: str, owner_id: str) -> VideoRecord | None:
        """삭제 중인 영상은 없는 것과 같이 취급한다."""
        video = self.get(video_id)
        return video if video and video.owner_id == owner_id and not video.deleting else None

    def list_for_owner(self, owner_id: str) -> list[VideoRecord]:
        with self._db.tx() as conn:
            rows = conn.execute("SELECT * FROM videos WHERE owner_id = ? AND deleting = 0 ORDER BY created_at DESC, video_id", (owner_id,)).fetchall()
        return [_record(r) for r in rows]

    def owns(self, video_id: str, owner_id: str) -> bool:
        return self.get_for_owner(video_id, owner_id) is not None

    def set_status(self, video_id: str, status: str) -> None:
        with self._db.tx(immediate=True) as conn:
            conn.execute("UPDATE videos SET status = ? WHERE video_id = ?", (status, video_id))

    def set_ready(self, video_id: str, *, duration_us: int, width: int, height: int) -> None:
        with self._db.tx(immediate=True) as conn:
            conn.execute(
                "UPDATE videos SET status = 'ready', duration_us = ?, width = ?, height = ?, ready_at = COALESCE(ready_at, ?) WHERE video_id = ?",
                (duration_us, width, height, now_rfc3339(), video_id),
            )

    def latest_run_id(self, video_id: str) -> str | None:
        with self._db.tx() as conn:
            row = conn.execute("SELECT run_id FROM runs WHERE video_id = ? ORDER BY created_at DESC, run_id DESC LIMIT 1", (video_id,)).fetchone()
        return row["run_id"] if row else None
