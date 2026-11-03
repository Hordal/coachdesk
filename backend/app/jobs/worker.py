"""분석 워커(상세 설계 12). API와 별도 프로세스로 실행한다.

    python -m app.jobs.worker            대기 중인 작업을 계속 처리한다
    python -m app.jobs.worker --once     처리할 작업이 없을 때까지 처리하고 끝낸다

한 번에 한 작업만 처리한다(13). 실행 중에는 별도 스레드가 heartbeat로 소유권(lease)을 유지하므로
긴 디코딩 중에도 lease가 만료되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import uuid
from typing import Callable

from ..services.deletion import DeletionService
from ..services.uploads import UploadService
from ..settings import Settings
from ..storage.db import Database
from ..storage.memory import RunStore
from ..storage.videos import VideoRepo
from .errors import JobCancelled, PermanentError, TransientError
from .ingest import STAGE_FUNCTIONS, WEIGHTED_STAGES, IngestContext
from .runner import FfmpegRunner, MediaRunner
from .store import ClaimedJob, JobStore, LeaseLost

logger = logging.getLogger("worker")


def log_event(event: str, **fields: object) -> None:
    """구조화된 로그 한 줄. 경로·토큰·서명 주소는 남기지 않는다(17)."""
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


class _Heartbeat(threading.Thread):
    def __init__(self, jobs: JobStore, job_id: str, token: str, interval: float) -> None:
        super().__init__(daemon=True)
        self._jobs, self._job_id, self._token, self._interval = jobs, job_id, token, interval
        self._stop_event = threading.Event()
        self.lost = False

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._jobs.heartbeat(self._job_id, self._token)
            except LeaseLost:
                self.lost = True
                return
            except Exception:  # 일시적인 DB 오류는 다음 주기에 다시 시도한다
                log_event("heartbeat_error", job_id=self._job_id)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)


class Worker:
    def __init__(self, settings: Settings, *, runner: MediaRunner | None = None, clock: Callable[[], float] = time.time) -> None:
        settings.validate_runtime()
        self.settings = settings
        self.db = Database(settings.db_path)
        self.jobs = JobStore(self.db, lease_seconds=settings.lease_seconds, clock=clock)
        self.videos = VideoRepo(self.db)
        self.runs = RunStore(self.db)
        self.runner = runner or FfmpegRunner(preset=settings.transcode_preset, crf=settings.transcode_crf)
        self.deletion = DeletionService(self.db, settings, self.videos, self.jobs)
        self.worker_id = uuid.uuid4().hex[:8]

    # --- 실행 -----------------------------------------------------------------

    def run_once(self) -> bool:
        """만료된 lease를 복구하고 작업 하나를 처리한다. 처리했으면 True."""
        for job_id, outcome in self.jobs.recover_expired():
            log_event("lease_recovered", job_id=job_id, outcome=outcome)
        claimed = self.jobs.claim_next()
        if claimed is None:
            return False
        log_event("job_claimed", job_id=claimed.job_id, attempt=claimed.attempts, worker=self.worker_id)
        self._process(claimed)
        return True

    def run_until_idle(self) -> int:
        count = 0
        while self.run_once():
            count += 1
        return count

    def sweep(self) -> None:
        """삭제 중인 영상을 마저 지우고 보관 기간이 지난 파일을 만료 처리한다(15)."""
        result = self.deletion.sweep()
        result["stale_uploads"] = UploadService(self.db,self.settings,self.jobs).sweep_stale()
        if any(result.values()):
            log_event("sweep", **result)

    def run_forever(self, stop: threading.Event, sweep_seconds: float = 10.0) -> None:
        presence_stop = threading.Event()
        def presence():
            while not presence_stop.is_set():
                try:
                    with self.db.tx(immediate=True) as conn:
                        conn.execute("INSERT INTO worker_presence(worker_id,seen_at) VALUES(?,?) ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at", (self.worker_id,time.time()))
                        conn.execute("DELETE FROM worker_presence WHERE seen_at<?",(time.time()-3600,))
                except Exception:
                    logger.warning("worker_presence_failed")
                presence_stop.wait(10)
        thread = threading.Thread(target=presence,daemon=True)
        thread.start()
        try:
            self._run_loop(stop,sweep_seconds)
        finally:
            presence_stop.set()
            thread.join(timeout=5)
            with self.db.tx(immediate=True) as conn:
                conn.execute("DELETE FROM worker_presence WHERE worker_id=?",(self.worker_id,))

    def _run_loop(self, stop: threading.Event, sweep_seconds: float) -> None:
        next_sweep = 0.0
        while not stop.is_set():
            if time.monotonic() >= next_sweep:
                try:
                    self.sweep()
                except Exception:  # 정리 실패가 작업 처리를 막지 않게 한다
                    logging.getLogger("worker").exception("sweep_failed")
                next_sweep = time.monotonic() + sweep_seconds
            if not self.run_once():
                stop.wait(self.settings.worker_poll_seconds)

    # --- 한 작업의 처리 ----------------------------------------------------------

    def _process(self, claimed: ClaimedJob) -> None:
        job_id, token = claimed.job_id, claimed.token
        video = self.videos.get(claimed.record.video_id or "")
        if video is None:
            self._finish_failed(claimed, "video_missing", "영상 기록을 찾을 수 없습니다", None, None)
            return

        heartbeat = _Heartbeat(self.jobs, job_id, token, self.settings.heartbeat_seconds)
        heartbeat.start()
        last_cancel_check = [0.0]

        def check() -> None:
            if heartbeat.lost:
                raise LeaseLost(job_id)
            now = time.monotonic()
            if now - last_cancel_check[0] >= 0.3:
                last_cancel_check[0] = now
                if self.jobs.cancel_requested(job_id):
                    raise JobCancelled()

        def should_stop() -> bool:
            try:
                check()
            except (JobCancelled, LeaseLost):
                return True
            return False

        ctx = IngestContext(
            settings=self.settings, jobs=self.jobs, videos=self.videos, runs=self.runs, runner=self.runner,
            job_id=job_id, token=token, video=video, check=check, should_stop=should_stop,
        )
        current_stage: str | None = None
        try:
            done = {s["name"] for s in claimed.record.stages if s["status"] == "completed"}
            for name, _label, _weight in WEIGHTED_STAGES:
                if name in done and (name != "playback_copy" or ctx.playback_path.is_file()):
                    continue  # 마지막으로 완성한 단계(체크포인트) 이후부터 다시 시작한다. 출력이 사라졌으면 다시 만든다
                check()
                current_stage = name
                self.jobs.set_stage(job_id, token, name, status="running")
                message = STAGE_FUNCTIONS[name](ctx)
                self.jobs.set_stage(job_id, token, name, status="completed", progress=1.0, message=message)
            run_id = ctx.run_id or self.videos.latest_run_id(video.video_id)
            self.jobs.complete(job_id, token, status="partial", run_id=run_id)
            log_event("job_finished", job_id=job_id, status="partial")
        except LeaseLost:
            # 다른 워커가 가져갔거나 취소로 끝난 작업이다. 이 워커는 더 쓰지 않는다.
            log_event("lease_lost", job_id=job_id, worker=self.worker_id)
        except JobCancelled:
            self._cleanup_outputs(video.video_id)
            try:
                self.jobs.complete(job_id, token, status="cancelled")
                self.videos.set_status(video.video_id, "cancelled")
                log_event("job_finished", job_id=job_id, status="cancelled")
            except LeaseLost:
                log_event("lease_lost", job_id=job_id, worker=self.worker_id)
        except PermanentError as exc:
            self._finish_failed(claimed, exc.code, exc.title, current_stage, exc.at_us)
        except (TransientError, OSError) as exc:
            code = exc.code if isinstance(exc, TransientError) else "io_error"
            title = exc.title if isinstance(exc, TransientError) else "영상을 처리하는 중 일시적인 오류가 발생했습니다"
            self._retry_or_fail(claimed, code, title, current_stage)
        except Exception:
            logger.exception("unexpected worker error")  # 스택은 로그에만 남긴다
            self._finish_failed(claimed, "internal_error", "처리 중 알 수 없는 오류가 발생했습니다", current_stage, None)
        finally:
            heartbeat.stop()

    # --- 종료 처리 ---------------------------------------------------------------

    def _error(self, code: str, title: str, stage: str | None, retryable: bool, at_us: int | None = None) -> dict:
        error = {"code": code, "title": title, "stage": stage, "retryable": retryable, "trace_id": uuid.uuid4().hex}
        if at_us is not None:
            error["at_us"] = at_us
        return error

    def _finish_failed(self, claimed: ClaimedJob, code: str, title: str, stage: str | None, at_us: int | None) -> None:
        error = self._error(code, title, stage, False, at_us)
        try:
            if stage:
                self.jobs.set_stage(claimed.job_id, claimed.token, stage, status="failed", message=title, reason_code=code)
            self.jobs.complete(claimed.job_id, claimed.token, status="failed", error=error)
            if claimed.record.video_id:
                self.videos.set_status(claimed.record.video_id, "failed")
            self._cleanup_outputs(claimed.record.video_id)
            log_event("job_finished", job_id=claimed.job_id, status="failed", code=code, stage=stage, trace_id=error["trace_id"])
        except LeaseLost:
            log_event("lease_lost", job_id=claimed.job_id, worker=self.worker_id)

    def _retry_or_fail(self, claimed: ClaimedJob, code: str, title: str, stage: str | None) -> None:
        error = self._error(code, title, stage, True)
        try:
            outcome = self.jobs.release_for_retry(claimed.job_id, claimed.token, error)
        except LeaseLost:
            log_event("lease_lost", job_id=claimed.job_id, worker=self.worker_id)
            return
        if outcome == "failed" and claimed.record.video_id:
            self.videos.set_status(claimed.record.video_id, "failed")
            self._cleanup_outputs(claimed.record.video_id)
        log_event("job_retry", job_id=claimed.job_id, outcome=outcome, code=code, attempt=claimed.attempts, trace_id=error["trace_id"])

    def _cleanup_outputs(self, video_id: str | None) -> None:
        """미완성 출력(임시 사본)을 지운다. 원본과 완성한 사본은 삭제 기능(DeletionService)이 정리한다."""
        if not video_id:
            return
        tmp = self.settings.playback_dir / f"{video_id}.mp4.tmp"
        tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="분석 워커")
    parser.add_argument("--once", action="store_true", help="처리할 작업이 없을 때까지 처리하고 끝낸다")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    worker = Worker(Settings())
    if args.once:
        log_event("worker_once", processed=worker.run_until_idle())
        return
    stop = threading.Event()
    log_event("worker_started", worker=worker.worker_id)
    try:
        worker.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
