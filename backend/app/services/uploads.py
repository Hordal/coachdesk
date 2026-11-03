"""재개 가능한 분할 업로드(상세 설계 12, 27의 3.6).

조각마다 크기와 sha256을 확인하고, 완료할 때 조각을 이어 붙여 원본으로 등록한 뒤 작업을 만든다.
파일은 임시로 만들고 검증한 뒤 같은 저장소 안에서 이름을 바꿔 등록하며, DB와 파일이 하나의 트랜잭션이라고 가정하지 않는다.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import sqlite3
import uuid
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path

from ..jobs.ingest import initial_stages
from ..jobs.store import JobStore
from ..media import admission, probe
from ..media.binaries import BinaryNotFound
from ..settings import Settings
from ..storage.db import Database, now_rfc3339

SPORTS = ("boxing", "kickboxing", "muay_thai", "mma")
_COPY_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class UploadRecord:
    upload_id: str
    owner_id: str
    filename: str
    size_bytes: int
    part_size: int
    part_count: int
    sport: str
    status: str
    video_id: str | None
    job_id: str | None
    received_parts: list[int]


class UploadError(Exception):
    """업로드 규칙 위반. status, code, 사용자에게 보여줄 제목과 설명을 담는다."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.detail = status, code, title, detail


def _from_admission(exc: admission.Rejection) -> UploadError:
    return UploadError(exc.status, exc.code, exc.title, exc.detail)


class UploadService:
    def __init__(self, db: Database, settings: Settings, jobs: JobStore) -> None:
        self._db, self._settings, self._jobs = db, settings, jobs

    # --- 경로 ------------------------------------------------------------------

    def _dir(self, upload_id: str) -> Path:
        return self._settings.uploads_dir / upload_id

    def _part_path(self, upload_id: str, number: int) -> Path:
        return self._dir(upload_id) / f"part-{number:06d}"

    @contextmanager
    def _exclusive(self, owner_id: str, upload_id: str):
        token = uuid.uuid4().hex
        with self._db.tx(immediate=True) as conn:
            if not conn.execute("SELECT 1 FROM uploads WHERE upload_id=? AND owner_id=?", (upload_id,owner_id)).fetchone():
                raise UploadError(404, "upload_not_found", "업로드를 찾을 수 없습니다")
            conn.execute("DELETE FROM upload_locks WHERE upload_id=? AND expires_at<?", (upload_id,time.time()))
            if conn.execute("SELECT 1 FROM upload_locks WHERE upload_id=?", (upload_id,)).fetchone():
                raise UploadError(409, "upload_busy", "업로드 요청 처리 중입니다. 잠시 뒤 다시 시도해 주세요")
            conn.execute("INSERT INTO upload_locks VALUES(?,?,?)", (upload_id,token,time.time()+3600))
        try:
            yield
        finally:
            with self._db.tx(immediate=True) as conn:
                conn.execute("DELETE FROM upload_locks WHERE upload_id=? AND token=?", (upload_id,token))

    def sweep_stale(self, now: datetime | None = None, hours: int = 24) -> int:
        """하루 동안 변경되지 않은 임시 업로드만 정리한다. 실행 중인 전송·완료 요청은 제외한다."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now-timedelta(hours=hours)).isoformat(timespec="milliseconds")
        cleaned = 0
        with self._db.tx(immediate=True) as conn:
            rows = conn.execute("SELECT upload_id FROM uploads u WHERE status IN ('receiving','failed','aborted') AND updated_at<? AND NOT EXISTS(SELECT 1 FROM upload_locks l WHERE l.upload_id=u.upload_id AND l.expires_at>?)", (cutoff,now.timestamp())).fetchall()
            for row in rows:
                path = self._dir(row["upload_id"]).resolve()
                if path.parent != self._settings.uploads_dir.resolve():
                    continue
                # 잠금 중에 파일을 지워 새 전송이 끼어들지 못하게 한다. 원본/완료된 영상은 포함되지 않는다.
                shutil.rmtree(path, ignore_errors=False) if path.exists() else None
                conn.execute("UPDATE uploads SET status='aborted',updated_at=? WHERE upload_id=?", (now.isoformat(timespec="milliseconds"),row["upload_id"]))
                conn.execute("DELETE FROM upload_parts WHERE upload_id=?", (row["upload_id"],))
                cleaned += 1
        return cleaned

    # --- 조회 ------------------------------------------------------------------

    def _record(self, conn: sqlite3.Connection, row: sqlite3.Row) -> UploadRecord:
        parts = [r["part_number"] for r in conn.execute("SELECT part_number FROM upload_parts WHERE upload_id = ? ORDER BY part_number", (row["upload_id"],))]
        return UploadRecord(
            upload_id=row["upload_id"], owner_id=row["owner_id"], filename=row["filename"], size_bytes=row["size_bytes"],
            part_size=row["part_size"], part_count=row["part_count"], sport=row["sport"], status=row["status"],
            video_id=row["video_id"], job_id=row["job_id"], received_parts=parts,
        )

    def get(self, owner_id: str, upload_id: str) -> UploadRecord | None:
        """소유자가 다르면 없는 것과 같이 취급한다(21)."""
        with self._db.tx() as conn:
            row = conn.execute("SELECT * FROM uploads WHERE upload_id = ? AND owner_id = ?", (upload_id, owner_id)).fetchone()
            return self._record(conn, row) if row else None

    # --- 시작 ------------------------------------------------------------------

    def create(self, owner_id: str, filename: str, size_bytes: int, sport: str, idempotency_key: str | None) -> tuple[UploadRecord, bool]:
        """업로드를 시작한다. 같은 Idempotency-Key로 다시 부르면 만들어 둔 업로드를 돌려준다. 돌려주는 값: (기록, 새로 만들었는지)"""
        if sport not in SPORTS:
            raise UploadError(422, "invalid_sport", "종목을 선택해 주세요")
        try:
            admission.check_declared(filename, size_bytes, self._settings)
        except admission.Rejection as exc:
            raise _from_admission(exc) from exc

        with self._db.tx(immediate=True) as conn:
            if idempotency_key:
                row = conn.execute("SELECT * FROM uploads WHERE owner_id = ? AND idempotency_key = ?", (owner_id, idempotency_key)).fetchone()
                if row is not None:
                    if row["filename"] != filename or row["size_bytes"] != size_bytes:
                        raise UploadError(409, "idempotency_conflict", "같은 요청 키로 다른 내용을 보낼 수 없습니다")
                    return self._record(conn, row), False
            upload_id = uuid.uuid4().hex
            part_size = self._settings.upload_part_size
            conn.execute(
                "INSERT INTO uploads (upload_id, owner_id, filename, size_bytes, part_size, part_count, sport, status, idempotency_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'receiving', ?, ?, ?)",
                (upload_id, owner_id, filename, size_bytes, part_size, math.ceil(size_bytes / part_size), sport, idempotency_key, now_rfc3339(), now_rfc3339()),
            )
            row = conn.execute("SELECT * FROM uploads WHERE upload_id = ?", (upload_id,)).fetchone()
            record = self._record(conn, row)
        self._dir(upload_id).mkdir(parents=True, exist_ok=True)
        return record, True

    # --- 조각 ------------------------------------------------------------------

    def put_part(self, owner_id: str, upload_id: str, number: int, data: bytes, sha256: str | None) -> UploadRecord:
        with self._exclusive(owner_id, upload_id):
            return self._put_part(owner_id, upload_id, number, data, sha256)

    def _put_part(self, owner_id: str, upload_id: str, number: int, data: bytes, sha256: str | None) -> UploadRecord:
        upload = self.get(owner_id, upload_id)
        if upload is None:
            raise UploadError(404, "upload_not_found", "업로드를 찾을 수 없습니다")
        if upload.status != "receiving":
            raise UploadError(409, "upload_closed", "이미 끝났거나 취소된 업로드입니다")
        if not 0 <= number < upload.part_count:
            raise UploadError(422, "invalid_part", "조각 번호가 올바르지 않습니다")
        expected = upload.part_size if number < upload.part_count - 1 else upload.size_bytes - upload.part_size * (upload.part_count - 1)
        if len(data) != expected:
            raise UploadError(422, "invalid_part_size", "조각 크기가 올바르지 않습니다", f"expected={expected}, got={len(data)}")
        digest = hashlib.sha256(data).hexdigest()
        if not sha256:
            raise UploadError(422, "checksum_required", "조각의 sha256 값이 필요합니다")
        if digest != sha256.lower():
            raise UploadError(422, "checksum_mismatch", "조각이 전송 중 손상되었습니다. 다시 보내 주세요")

        path = self._part_path(upload_id, number)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # 같은 조각을 다시 보내면 덮어쓴다(재시도 안전)
        with self._db.tx(immediate=True) as conn:
            conn.execute(
                "INSERT INTO upload_parts (upload_id, part_number, size_bytes, sha256) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (upload_id, part_number) DO UPDATE SET size_bytes = excluded.size_bytes, sha256 = excluded.sha256",
                (upload_id, number, len(data), digest),
            )
            conn.execute("UPDATE uploads SET updated_at = ? WHERE upload_id = ?", (now_rfc3339(), upload_id))
        return self.get(owner_id, upload_id)  # type: ignore[return-value]

    # --- 완료 ------------------------------------------------------------------

    def complete(self, owner_id: str, upload_id: str) -> UploadRecord:
        with self._exclusive(owner_id, upload_id):
            return self._complete(owner_id, upload_id)

    def _complete(self, owner_id: str, upload_id: str) -> UploadRecord:
        upload = self.get(owner_id, upload_id)
        if upload is None:
            raise UploadError(404, "upload_not_found", "업로드를 찾을 수 없습니다")
        if upload.status == "completed":
            return upload  # 같은 요청을 다시 받으면 처음 결과를 돌려준다
        if upload.status != "receiving":
            raise UploadError(409, "upload_closed", "이미 끝났거나 취소된 업로드입니다")
        missing = sorted(set(range(upload.part_count)) - set(upload.received_parts))
        if missing:
            raise UploadError(409, "incomplete_upload", "아직 받지 못한 조각이 있습니다", f"missing={len(missing)}")
        try:
            admission.check_disk_space(upload.size_bytes, self._settings)
        except admission.Rejection as exc:
            raise _from_admission(exc) from exc

        directory = self._dir(upload_id)
        assembled = directory / "assembled.tmp"
        try:
            sha256 = self._assemble(upload, assembled)
            source = self._validate(assembled)
        except UploadError:
            self._close(upload_id, "failed")
            shutil.rmtree(directory, ignore_errors=True)
            raise

        video_id = uuid.uuid4().hex
        destination = self._settings.originals_dir / video_id / f"source{Path(upload.filename).suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(assembled, destination)  # 검증한 파일을 같은 저장소 안에서 원자적으로 등록한다
        try:
            with self._db.tx(immediate=True) as conn:
                now = now_rfc3339()
                conn.execute(
                    "INSERT INTO videos (video_id, owner_id, filename, sport, source_path, size_bytes, sha256, duration_us, width, height, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?)",
                    (video_id, owner_id, upload.filename, upload.sport, str(destination), upload.size_bytes, sha256, source.duration_us, source.width, source.height, now),
                )
                job_id = self._jobs.create(conn, owner_id=owner_id, kind="ingest", video_id=video_id, stages=initial_stages(), max_attempts=self._settings.max_attempts)
                conn.execute("UPDATE uploads SET status = 'completed', video_id = ?, job_id = ?, updated_at = ? WHERE upload_id = ?", (video_id, job_id, now, upload_id))
        except BaseException:
            destination.unlink(missing_ok=True)  # DB 등록에 실패하면 고아 파일을 남기지 않는다
            raise
        shutil.rmtree(directory, ignore_errors=True)
        return self.get(owner_id, upload_id)  # type: ignore[return-value]

    def _assemble(self, upload: UploadRecord, target: Path) -> str:
        """조각을 순서대로 이어 붙이며 전체 sha256을 계산한다. 영상을 한꺼번에 메모리에 올리지 않는다."""
        digest = hashlib.sha256()
        written = 0
        with target.open("wb") as out:
            for number in range(upload.part_count):
                with self._part_path(upload.upload_id, number).open("rb") as part:
                    while chunk := part.read(_COPY_CHUNK):
                        out.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
        if written != upload.size_bytes:
            raise UploadError(422, "size_mismatch", "받은 파일 크기가 선언한 크기와 다릅니다")
        return digest.hexdigest()

    def _validate(self, path: Path) -> probe.SourceInfo:
        """실제 스트림을 검사한다(확장자만 믿지 않는다)."""
        try:
            info = probe.inspect_source(path)
        except BinaryNotFound as exc:
            raise UploadError(503, "media_tool_unavailable", "영상 검사 도구를 사용할 수 없습니다") from exc
        except probe.MediaError as exc:
            raise UploadError(422, "invalid_media", "영상 파일을 읽을 수 없습니다", "손상되었거나 영상이 아닌 파일일 수 있습니다") from exc
        try:
            admission.check_source(info, self._settings)
        except admission.Rejection as exc:
            raise _from_admission(exc) from exc
        return info

    # --- 취소 ------------------------------------------------------------------

    def _close(self, upload_id: str, status: str) -> None:
        with self._db.tx(immediate=True) as conn:
            conn.execute("UPDATE uploads SET status = ?, updated_at = ? WHERE upload_id = ?", (status, now_rfc3339(), upload_id))

    def abort(self, owner_id: str, upload_id: str) -> None:
        with self._exclusive(owner_id, upload_id):
            return self._abort(owner_id, upload_id)

    def _abort(self, owner_id: str, upload_id: str) -> None:
        upload = self.get(owner_id, upload_id)
        if upload is None:
            raise UploadError(404, "upload_not_found", "업로드를 찾을 수 없습니다")
        if upload.status == "receiving":
            self._close(upload_id, "aborted")
            shutil.rmtree(self._dir(upload_id), ignore_errors=True)
