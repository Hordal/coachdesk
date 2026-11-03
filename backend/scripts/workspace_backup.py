"""중지된 단일 호스트 분석실의 백업·무결성 검사·새 경로 복구. 기존 데이터를 덮어쓰지 않는다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _new_destination(path: Path):
    if path.exists():
        raise ValueError("대상은 존재하지 않는 새 디렉터리여야 합니다. 기존 데이터는 덮어쓰지 않습니다.")
    path.mkdir(parents=True)


def _database_check(path: Path):
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite 무결성 검사 실패")
    finally:
        conn.close()


def backup(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("원본과 백업 경로는 서로 포함될 수 없습니다.")
    db = source / "app.sqlite3"
    if not db.is_file():
        raise ValueError("분석실 데이터베이스가 없습니다.")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro",uri=True)
    try:
        if conn.execute("SELECT COUNT(*) FROM worker_presence WHERE seen_at>?",(time.time()-45,)).fetchone()[0]:
            raise ValueError("작업 워커가 활성 상태입니다. API와 워커를 중지하고 45초 뒤에 백업하세요.")
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='local_engine_presence'").fetchone() and conn.execute("SELECT COUNT(*) FROM local_engine_presence WHERE seen_at>?",(time.time()-45,)).fetchone()[0]:
            raise ValueError("내장 분석이 활성 상태입니다. API와 워커를 중지하고 45초 뒤에 백업하세요.")
        _new_destination(destination)
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError("데이터 폴더의 심볼릭 링크는 지원하지 않습니다.")
            if not path.is_file() or path.name in {"app.sqlite3","app.sqlite3-wal","app.sqlite3-shm"} or path.name.endswith(".tmp"):
                continue
            relative = path.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
        copied = sqlite3.connect(destination / "app.sqlite3")
        try:
            conn.backup(copied)
        finally:
            copied.close()
    finally:
        conn.close()
    _database_check(destination / "app.sqlite3")
    files = {p.relative_to(destination).as_posix():digest(p) for p in destination.rglob("*") if p.is_file()}
    (destination / "manifest.json").write_text(json.dumps({"format":"coachdesk-backup-v1","created_at":time.time(),"files":files},indent=2),encoding="utf-8")


def verify(source: Path) -> dict[str,str]:
    source = source.resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "coachdesk-backup-v1" or "app.sqlite3" not in manifest["files"]:
        raise ValueError("지원하는 분석실 백업 형식이 아닙니다.")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        path = (source / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or source not in path.parents or path.is_symlink():
            raise ValueError("백업의 파일 경로가 올바르지 않습니다.")
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"파일 무결성 검사 실패: {relative.name}")
    _database_check(source / "app.sqlite3")
    return manifest["files"]


def restore(source: Path,destination: Path, deletion_ledger: Path | None = None):
    source,destination = source.resolve(),destination.resolve()
    files = verify(source)
    # 과거 백업만으로는 백업 후 삭제된 영상을 알 수 없다. 최신 운영 기록을 명시적으로 요구한다.
    if deletion_ledger is None or not deletion_ledger.is_file():
        raise ValueError("복구에는 현재 운영 폴더의 최신 --deletion-ledger 파일이 필요합니다. 삭제 기록이 없었다면 빈 파일을 명시하세요.")
    from app.services.deletion_journal import read
    records = read(deletion_ledger)
    if "deletion-ledger.jsonl" in files:
        records += read(source / "deletion-ledger.jsonl")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("백업과 복구 경로는 서로 포함될 수 없습니다.")
    _new_destination(destination)
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source / name,target)
    from app.storage.db import Database
    Database(destination / "app.sqlite3")
    # 백업 당시의 작업 세션을 되살리지 않는다. 복구 후 계정 로그인이 다시 필요하다.
    conn = sqlite3.connect(destination / "app.sqlite3")
    try:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM worker_presence")
        conn.execute("DELETE FROM local_engine_presence")
        # 워커 인증·임대와 백업 당시의 실행을 재사용하지 않는다.
        conn.execute("UPDATE analysis_keys SET revoked_at=COALESCE(revoked_at,?)", (time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),))
        conn.execute("UPDATE analysis_tasks SET status='cancelled',lease_hash=NULL,lease_until=NULL WHERE status IN ('queued','running')")
        conn.execute("DELETE FROM upload_locks")
        conn.execute("UPDATE jobs SET status='cancelled',lease_token=NULL,lease_expires_at=NULL WHERE status IN ('queued','running')")
        conn.execute("UPDATE videos SET status='cancelled' WHERE status='processing'")
        # 원본 경로를 새 복구 폴더에 연결한다. 이전 운영 경로를 읽지 않는다.
        for video_id,old_path in conn.execute("SELECT video_id,source_path FROM videos").fetchall():
            import re
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",video_id):
                raise ValueError("잘못된 영상 식별자")
            conn.execute("UPDATE videos SET source_path=? WHERE video_id=?", (str(destination/'originals'/video_id/Path(old_path).name),video_id))
        conn.commit()
    finally:
        conn.close()
    # 동일한 삭제 서비스를 사용해 DB·결과 파일·영상 사본을 함께 정리한다.
    from app.settings import Settings
    from app.storage.db import Database
    from app.storage.videos import VideoRepo
    from app.jobs.store import JobStore
    from app.services.deletion import DeletionService
    settings=Settings(data_dir=destination,enable_fixtures=False)
    db=Database(settings.db_path)
    videos=VideoRepo(db)
    deletion=DeletionService(db,settings,videos,JobStore(db))
    (destination/"deletion-ledger.jsonl").write_text("".join(json.dumps(r)+"\n" for r in records),encoding="utf-8")
    targets={}
    for r in records:
        targets[r["video_id"]]="all" if r["scope"]=="all" or targets.get(r["video_id"])=="all" else "media"
    for vid,scope in targets.items():
        video=videos.get(vid)
        if video is not None:
            if scope=="all":
                if not deletion.request_delete_all(video,video.owner_id).completed:
                    raise ValueError("삭제 기록을 복구 데이터에 적용할 수 없습니다")
            else:
                deletion.delete_media(video)
    _database_check(destination/"app.sqlite3")


def main():
    parser = argparse.ArgumentParser(description="서비스를 중지한 상태에서 실행하는 분석실 백업 도구")
    parser.add_argument("command",choices=["backup","verify","restore"])
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--destination",type=Path)
    parser.add_argument("--deletion-ledger",type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        verify(args.source)
    else:
        if args.destination is None:
            parser.error("--destination is required")
        if args.command == "backup":
            backup(args.source,args.destination)
        else:
            restore(args.source,args.destination,args.deletion_ledger)
    print("완료: "+args.command)


if __name__ == "__main__":
    main()
