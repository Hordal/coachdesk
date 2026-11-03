"""실행 결과 저장소(상세 설계 27의 3.4).

가상 예제(fixture)는 메모리에 두고, 업로드한 영상의 실행 결과는 SQLite에 저장한다. 두 곳을 같은 방식으로 조회한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ..domain.models import ResultBundle
from ..pipeline.registry import RunMode
from .db import Database, now_rfc3339


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    owner_id: str
    mode: RunMode
    status: str  # 작업 상태: queued | running | completed | partial | failed | cancelled
    bundle: ResultBundle
    raw: dict[str, Any]  # 검사·집계 재계산용 원본 dict


class RunStore:
    def __init__(self, db: Database | None = None) -> None:
        self._runs: dict[str, StoredRun] = {}
        self._db = db

    # --- 쓰기 ------------------------------------------------------------------

    def add(self, run: StoredRun) -> None:
        """메모리에만 둔다(가상 예제)."""
        if run.run_id in self._runs:
            raise ValueError(f"run_id 중복: {run.run_id}")
        self._runs[run.run_id] = run

    def add_persistent(self, run: StoredRun, *, parent_run_id: str | None = None, extra: Callable[[Any], None] | None = None) -> None:
        """SQLite에 저장한다(업로드한 영상의 결과). 계산 산출물은 덮어쓰지 않는다(16)."""
        if self._db is None:
            raise RuntimeError("DB가 연결되지 않음")
        with self._db.tx(immediate=True) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, owner_id, video_id, mode, status, parent_run_id, bundle_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, run.owner_id, run.bundle.video_id, str(run.mode), run.status, parent_run_id, json.dumps(run.raw, ensure_ascii=False), now_rfc3339()),
            )
            if extra is not None:  # 같은 트랜잭션에서 함께 저장할 기록(정정 등)
                extra(conn)

    # --- 읽기 ------------------------------------------------------------------

    def _from_row(self, row: Any) -> StoredRun:
        raw = json.loads(row["bundle_json"])
        return StoredRun(
            run_id=row["run_id"],
            owner_id=row["owner_id"],
            mode=RunMode(row["mode"]),
            status=row["status"],
            bundle=ResultBundle.model_validate(raw),
            raw=raw,
        )

    def get_for_owner(self, run_id: str, owner_id: str) -> StoredRun | None:
        """소유자가 다르면 없는 것과 같이 취급한다(존재 여부를 알리지 않음, 21)."""
        run = self._runs.get(run_id)
        if run is None and self._db is not None:
            with self._db.tx() as conn:
                row = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ? AND NOT EXISTS (SELECT 1 FROM videos v WHERE v.video_id = runs.video_id AND v.deleting = 1)",
                    (run_id,),
                ).fetchone()
            run = self._from_row(row) if row else None
        if run is None or run.owner_id != owner_id:
            return None
        return run

    def owns_video(self, video_id: str, owner_id: str) -> bool:
        """이 사용자의 영상인지(가상 예제의 실행 또는 업로드한 영상)."""
        if any(r.owner_id == owner_id and r.bundle.video_id == video_id for r in self._runs.values()):
            return True
        if self._db is None:
            return False
        with self._db.tx() as conn:
            row = conn.execute("SELECT 1 FROM videos WHERE video_id = ? AND owner_id = ? AND deleting = 0", (video_id, owner_id)).fetchone()
        return row is not None

    def parent_of(self, run_id: str) -> str | None:
        """정정으로 만들어진 실행이면 정정 전 실행."""
        if self._db is None:
            return None
        with self._db.tx() as conn:
            row = conn.execute("SELECT parent_run_id FROM corrections WHERE run_id = ?", (run_id,)).fetchone()
        return row["parent_run_id"] if row else None

    def child_of(self, run_id: str) -> str | None:
        """이 실행을 정정한 새 실행. 있으면 이 실행은 최신이 아니다."""
        if self._db is None:
            return None
        with self._db.tx() as conn:
            row = conn.execute("SELECT run_id FROM corrections WHERE parent_run_id = ?", (run_id,)).fetchone()
        return row["run_id"] if row else None

    def list_for_owner(self, owner_id: str) -> list[StoredRun]:
        runs = [r for r in self._runs.values() if r.owner_id == owner_id]
        if self._db is not None:
            with self._db.tx() as conn:
                rows = conn.execute("SELECT * FROM runs WHERE owner_id = ? AND NOT EXISTS (SELECT 1 FROM videos v WHERE v.video_id = runs.video_id AND v.deleting = 1) ORDER BY created_at DESC, run_id", (owner_id,)).fetchall()
            runs += [self._from_row(r) for r in rows]
        return runs
