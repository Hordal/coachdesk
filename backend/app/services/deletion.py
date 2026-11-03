"""영상 삭제와 보관 기간 만료(상세 설계 15).

삭제 절차: 요청 즉시 조회 차단 → 작업 취소 → 파일 삭제 → DB 행 삭제 → 완료 기록.
파일이 모두 지워진 것을 확인한 뒤에만 기록을 지우고, 도중에 실패하면 삭제 중 표시를 남겨 다시 시도한다.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..jobs.store import JobStore
from ..settings import Settings
from ..storage.db import Database, now_rfc3339
from ..storage.videos import VideoRecord, VideoRepo
from .deletion_journal import record

log = logging.getLogger("deletion")


class VideoBusy(Exception):
    """처리 중인 영상의 파일은 처리가 끝나거나 취소된 뒤에 지울 수 있다."""


@dataclass(frozen=True)
class DeleteOutcome:
    completed: bool  # False이면 아직 정리 중이며 다시 시도한다


class DeletionService:
    def __init__(self, db: Database, settings: Settings, videos: VideoRepo, jobs: JobStore) -> None:
        self._db = db
        self._settings = settings
        self._videos = videos
        self._jobs = jobs

    # --- 파일 --------------------------------------------------------------------

    def _media_paths(self, video_id: str) -> list[Path]:
        s = self._settings
        return [s.originals_dir / video_id, s.playback_dir / f"{video_id}.mp4", s.playback_dir / f"{video_id}.mp4.tmp"]

    def _remove_files(self, video_id: str) -> bool:
        """원본·재생용 사본·임시 파일을 지우고 남은 것이 없는지 확인한다."""
        ok = True
        for path in self._media_paths(video_id):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                log.warning("file_delete_failed video_id=%s", video_id)
                ok = False
        return ok and not any(p.exists() for p in self._media_paths(video_id))

    def _remove_results(self, video_id: str) -> bool:
        """가져온 검출·추적 결과 파일. 영상 전체를 지울 때만 지운다(구조화된 결과는 영상 파일 삭제 뒤에도 남는다)."""
        path = self._settings.observations_dir / video_id
        poses = self._settings.poses_dir / video_id
        local = self._settings.data_dir / "local-analysis" / video_id
        try:
            if local.exists():
                shutil.rmtree(local)
            if poses.exists():
                shutil.rmtree(poses)
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            log.warning("result_delete_failed video_id=%s", video_id)
            return False
        return not path.exists()

    def _active_job(self, video_id: str) -> bool:
        with self._db.tx() as conn:
            row = conn.execute("SELECT 1 FROM jobs WHERE video_id = ? AND status IN ('queued', 'running')", (video_id,)).fetchone()
            analysis = conn.execute("SELECT 1 FROM analysis_tasks WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone()
        return row is not None or analysis is not None

    # --- 요청 --------------------------------------------------------------------

    def delete_media(self, video: VideoRecord, *, reason: str = "deleted") -> None:
        """원본과 재생용 사본만 지운다. 구조화된 결과는 남고 근거 영상은 재생할 수 없다고 표시한다."""
        if self._active_job(video.video_id):
            raise VideoBusy(video.video_id)
        record(self._settings.data_dir, video.video_id, "media")
        if not self._remove_files(video.video_id):
            raise OSError("파일을 지우지 못함")
        with self._db.tx(immediate=True) as conn:
            conn.execute(
                "UPDATE videos SET media_state = ?, media_removed_at = COALESCE(media_removed_at, ?) WHERE video_id = ?",
                (reason, now_rfc3339(), video.video_id),
            )

    def request_delete_all(self, video: VideoRecord, owner_id: str) -> DeleteOutcome:
        """영상과 결과를 모두 지운다. 즉시 조회를 막고, 처리 중인 작업은 취소를 요청한 뒤 정리가 끝나면 지운다."""
        record(self._settings.data_dir, video.video_id, "all")
        with self._db.tx(immediate=True) as conn:
            conn.execute("UPDATE videos SET deleting = 1 WHERE video_id = ?", (video.video_id,))
            conn.execute("UPDATE analysis_tasks SET status='cancelled',lease_hash=NULL,updated_at=? WHERE video_id=? AND status IN ('queued','running')", (now_rfc3339(), video.video_id))
            rows = conn.execute("SELECT job_id FROM jobs WHERE video_id = ? AND status IN ('queued', 'running')", (video.video_id,)).fetchall()
        for row in rows:
            self._jobs.request_cancel(row["job_id"], owner_id)
        return self.purge(video.video_id)

    def purge(self, video_id: str) -> DeleteOutcome:
        """삭제 중인 영상을 지운다. 작업이 아직 돌고 있거나 파일을 지우지 못하면 나중에 다시 시도한다."""
        video = self._videos.get(video_id)
        if video is None:
            return DeleteOutcome(True)
        if not video.deleting:
            return DeleteOutcome(False)
        with self._db.tx() as conn:
            running = conn.execute("SELECT 1 FROM jobs WHERE video_id = ? AND status = 'running'", (video_id,)).fetchone()
        if running or not self._remove_files(video_id) or not self._remove_results(video_id):
            return DeleteOutcome(False)
        with self._db.tx(immediate=True) as conn:
            for table in ("corrections", "reports", "context_notes", "observation_sets", "pose_sets", "designations"):
                conn.execute(f"DELETE FROM {table} WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM runs WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM jobs WHERE video_id = ?", (video_id,))  # job_events는 연쇄 삭제
            conn.execute("DELETE FROM uploads WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        return DeleteOutcome(True)

    # --- 주기 정리(워커) ------------------------------------------------------------

    def sweep(self, now: datetime | None = None) -> dict[str, int]:
        """삭제 중인 영상을 마저 지우고, 보관 기간이 지난 원본·재생용 사본을 만료 처리한다."""
        now = now or datetime.now(timezone.utc)
        with self._db.tx() as conn:
            pending = [r["video_id"] for r in conn.execute("SELECT video_id FROM videos WHERE deleting = 1")]
        purged = sum(1 for v in pending if self.purge(v).completed)

        expired = 0
        days = self._settings.retention_days
        if days > 0:
            cutoff = (now - timedelta(days=days)).isoformat(timespec="milliseconds")
            with self._db.tx() as conn:
                due = conn.execute(
                    "SELECT video_id FROM videos WHERE deleting = 0 AND media_state = 'present' AND status IN ('ready', 'failed', 'cancelled') "
                    "AND COALESCE(ready_at, created_at) <= ?",
                    (cutoff,),
                ).fetchall()
            for row in due:
                video = self._videos.get(row["video_id"])
                if video is None:
                    continue
                try:
                    self.delete_media(video, reason="expired")
                    expired += 1
                except (VideoBusy, OSError):
                    continue
        return {"purged": purged, "expired": expired}
