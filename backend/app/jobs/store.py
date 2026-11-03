"""작업 큐(상세 설계 12). SQLite 작업 테이블에서 소유권(lease)을 짧은 트랜잭션으로 확보한다.

- 워커는 작업을 가져올 때 소유권 토큰을 받고, 이후 모든 쓰기에 이 토큰을 확인한다.
  lease가 만료되어 다른 워커가 가져간 작업에 이전 워커가 늦게 쓰는 것을 막는다.
- 프로세스가 중단되면 만료된 lease를 찾아 마지막으로 완성한 단계(체크포인트)부터 다시 시작한다.
- 취소는 플래그로 요청하고 워커가 확인한다. 대기 중인 작업은 바로 취소한다.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from ..storage.db import Database, now_rfc3339

TERMINAL = ("completed", "partial", "failed", "cancelled")


class LeaseLost(Exception):
    """이 워커가 더 이상 작업 소유권을 갖고 있지 않다(만료·취소·다른 워커가 가져감)."""


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    owner_id: str
    kind: str
    video_id: str | None
    status: str
    stage: str | None
    progress: float
    stages: list[dict[str, Any]]
    attempts: int
    max_attempts: int
    cancel_requested: bool
    error: dict[str, Any] | None
    run_id: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    token: str
    attempts: int
    record: JobRecord


def _record(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=row["job_id"],
        owner_id=row["owner_id"],
        kind=row["kind"],
        video_id=row["video_id"],
        status=row["status"],
        stage=row["stage"],
        progress=row["progress"],
        stages=json.loads(row["stages_json"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        cancel_requested=bool(row["cancel_requested"]),
        error=json.loads(row["error_json"]) if row["error_json"] else None,
        run_id=row["run_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def overall_progress(stages: list[dict[str, Any]]) -> float:
    """가중치가 있는 단계의 진행률을 합산한다. 완료 예정 시간은 추정하지 않는다(12)."""
    total = sum(s.get("weight", 0.0) for s in stages)
    if total <= 0:
        return 0.0
    return round(sum(s.get("weight", 0.0) * s.get("progress", 0.0) for s in stages) / total, 4)


class JobStore:
    def __init__(self, db: Database, *, lease_seconds: float = 60.0, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._lease = lease_seconds
        self._clock = clock

    # --- 생성·조회 -------------------------------------------------------------

    def create(
        self,
        conn: sqlite3.Connection,
        *,
        owner_id: str,
        kind: str,
        video_id: str | None,
        stages: list[dict[str, Any]],
        max_attempts: int,
    ) -> str:
        """호출한 쪽의 트랜잭션 안에서 작업을 만든다(영상 등록과 함께 한 번에 기록하기 위해)."""
        job_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO jobs (job_id, owner_id, kind, video_id, status, stages_json, max_attempts, created_at, progress) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (job_id, owner_id, kind, video_id, json.dumps(stages, ensure_ascii=False), max_attempts, now_rfc3339(), overall_progress(stages)),
        )
        self._event(conn, job_id, None, "created", {"kind": kind})
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self._db.tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _record(row) if row else None

    def get_for_owner(self, job_id: str, owner_id: str) -> JobRecord | None:
        """소유자가 다르면 없는 것과 같이 취급한다(21)."""
        job = self.get(job_id)
        return job if job and job.owner_id == owner_id else None

    def latest_for_video(self, video_id: str) -> JobRecord | None:
        with self._db.tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC, job_id DESC LIMIT 1", (video_id,)).fetchone()
        return _record(row) if row else None

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self._db.tx() as conn:
            rows = conn.execute("SELECT at, stage, event, detail_json FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [{"at": r["at"], "stage": r["stage"], "event": r["event"], "detail": json.loads(r["detail_json"]) if r["detail_json"] else None} for r in rows]

    # --- 소유권 --------------------------------------------------------------

    def claim_next(self) -> ClaimedJob | None:
        """대기 중인 가장 오래된 작업의 소유권을 확보한다."""
        with self._db.tx(immediate=True) as conn:
            row = conn.execute("SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at, job_id LIMIT 1").fetchone()
            if row is None:
                return None
            job_id, token, now = row["job_id"], uuid.uuid4().hex, self._clock()
            conn.execute(
                "UPDATE jobs SET status = 'running', lease_token = ?, lease_expires_at = ?, heartbeat_at = ?, "
                "attempts = attempts + 1, started_at = COALESCE(started_at, ?) WHERE job_id = ?",
                (token, now + self._lease, now, now_rfc3339(), job_id),
            )
            record = _record(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())
            self._event(conn, job_id, None, "claimed", {"attempt": record.attempts})
        return ClaimedJob(job_id=job_id, token=token, attempts=record.attempts, record=record)

    def _guard(self, conn: sqlite3.Connection, job_id: str, token: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None or row["status"] != "running" or row["lease_token"] != token:
            raise LeaseLost(job_id)
        return row

    def heartbeat(self, job_id: str, token: str) -> None:
        with self._db.tx(immediate=True) as conn:
            self._guard(conn, job_id, token)
            now = self._clock()
            conn.execute("UPDATE jobs SET lease_expires_at = ?, heartbeat_at = ? WHERE job_id = ?", (now + self._lease, now, job_id))

    def cancel_requested(self, job_id: str) -> bool:
        with self._db.tx() as conn:
            row = conn.execute("SELECT cancel_requested FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    # --- 단계 기록 -------------------------------------------------------------

    def set_stage(
        self,
        job_id: str,
        token: str,
        stage: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        with self._db.tx(immediate=True) as conn:
            row = self._guard(conn, job_id, token)
            stages = json.loads(row["stages_json"])
            entry = next((s for s in stages if s["name"] == stage), None)
            if entry is None:
                raise KeyError(f"알 수 없는 단계: {stage}")
            changed = status is not None and entry["status"] != status
            if status is not None:
                entry["status"] = status
            if progress is not None:
                entry["progress"] = max(0.0, min(1.0, progress))
            if message is not None:
                entry["message"] = message
            if reason_code is not None:
                entry["reason_code"] = reason_code
            conn.execute(
                "UPDATE jobs SET stages_json = ?, stage = ?, progress = ? WHERE job_id = ?",
                (json.dumps(stages, ensure_ascii=False), stage, overall_progress(stages), job_id),
            )
            if changed:
                self._event(conn, job_id, stage, f"stage_{status}", {"message": message})

    # --- 종료·재시도·취소 ---------------------------------------------------------

    def complete(self, job_id: str, token: str, *, status: str, run_id: str | None = None, error: dict[str, Any] | None = None) -> None:
        if status not in TERMINAL:
            raise ValueError(status)
        with self._db.tx(immediate=True) as conn:
            row = self._guard(conn, job_id, token)
            progress = 1.0 if status in ("completed", "partial") else row["progress"]
            conn.execute(
                "UPDATE jobs SET status = ?, run_id = ?, error_json = ?, progress = ?, lease_token = NULL, lease_expires_at = NULL, finished_at = ? WHERE job_id = ?",
                (status, run_id, json.dumps(error, ensure_ascii=False) if error else None, progress, now_rfc3339(), job_id),
            )
            self._event(conn, job_id, row["stage"], f"job_{status}", error)

    def _requeue_or_fail(self, conn: sqlite3.Connection, row: sqlite3.Row, error: dict[str, Any], reason: str) -> str:
        """남은 시도가 있으면 대기 상태로 되돌리고, 없으면 실패로 끝낸다. 진행 중이던 단계는 처음부터 다시 한다."""
        stages = json.loads(row["stages_json"])
        for s in stages:
            if s["status"] == "running":
                s["status"], s["progress"] = "pending", 0.0
        if row["attempts"] >= row["max_attempts"]:
            final_error = {**error, "code": "retries_exhausted", "retryable": False, "last_code": error.get("code")}
            conn.execute(
                "UPDATE jobs SET status = 'failed', stages_json = ?, error_json = ?, progress = ?, lease_token = NULL, lease_expires_at = NULL, finished_at = ? WHERE job_id = ?",
                (json.dumps(stages, ensure_ascii=False), json.dumps(final_error, ensure_ascii=False), overall_progress(stages), now_rfc3339(), row["job_id"]),
            )
            self._event(conn, row["job_id"], row["stage"], "job_failed", final_error)
            return "failed"
        conn.execute(
            "UPDATE jobs SET status = 'queued', stages_json = ?, progress = ?, lease_token = NULL, lease_expires_at = NULL WHERE job_id = ?",
            (json.dumps(stages, ensure_ascii=False), overall_progress(stages), row["job_id"]),
        )
        self._event(conn, row["job_id"], row["stage"], "requeued", {"reason": reason, "error": error})
        return "queued"

    def release_for_retry(self, job_id: str, token: str, error: dict[str, Any]) -> str:
        """일시적 오류. 남은 시도가 있으면 다시 대기시키고, 없으면 실패로 끝낸다. 돌려주는 값은 새 상태."""
        with self._db.tx(immediate=True) as conn:
            row = self._guard(conn, job_id, token)
            return self._requeue_or_fail(conn, row, error, "transient_error")

    def recover_expired(self) -> list[tuple[str, str]]:
        """lease가 만료된 작업을 찾아 마지막 완성 단계부터 다시 시작하게 한다. [(job_id, 새 상태)]"""
        recovered: list[tuple[str, str]] = []
        with self._db.tx(immediate=True) as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE status = 'running' AND lease_expires_at < ?", (self._clock(),)).fetchall()
            for row in rows:
                if row["cancel_requested"]:
                    conn.execute(
                        "UPDATE jobs SET status = 'cancelled', lease_token = NULL, lease_expires_at = NULL, finished_at = ? WHERE job_id = ?",
                        (now_rfc3339(), row["job_id"]),
                    )
                    self._event(conn, row["job_id"], row["stage"], "job_cancelled", {"reason": "lease_expired_after_cancel"})
                    recovered.append((row["job_id"], "cancelled"))
                    continue
                error = {"code": "lease_expired", "title": "처리 중이던 작업이 중단되어 다시 시작합니다", "stage": row["stage"], "retryable": True}
                recovered.append((row["job_id"], self._requeue_or_fail(conn, row, error, "lease_expired")))
        return recovered

    def request_cancel(self, job_id: str, owner_id: str) -> JobRecord | None:
        """대기 중이면 바로 취소하고, 처리 중이면 취소를 요청한다(워커가 확인해 멈춘다). 이미 끝난 작업은 그대로 둔다."""
        with self._db.tx(immediate=True) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ? AND owner_id = ?", (job_id, owner_id)).fetchone()
            if row is None:
                return None
            if row["status"] == "queued":
                conn.execute("UPDATE jobs SET status = 'cancelled', cancel_requested = 1, finished_at = ? WHERE job_id = ?", (now_rfc3339(), job_id))
                self._event(conn, job_id, row["stage"], "job_cancelled", {"while": "queued"})
            elif row["status"] == "running" and not row["cancel_requested"]:
                conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE job_id = ?", (job_id,))
                self._event(conn, job_id, row["stage"], "cancel_requested", None)
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _record(row)

    # --- 내부 ------------------------------------------------------------------

    @staticmethod
    def _event(conn: sqlite3.Connection, job_id: str, stage: str | None, event: str, detail: Any) -> None:
        conn.execute(
            "INSERT INTO job_events (job_id, at, stage, event, detail_json) VALUES (?, ?, ?, ?, ?)",
            (job_id, now_rfc3339(), stage, event, json.dumps(detail, ensure_ascii=False) if detail is not None else None),
        )
