"""DB 외부의 추가 전용 삭제 기록. 복구 시 최신 기록을 다시 적용한다."""
import json
import os
import re
from pathlib import Path

from ..storage.db import now_rfc3339


def record(directory: Path, video_id: str, scope: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", video_id) or scope not in {"all", "media"}:
        raise ValueError("invalid deletion record")
    directory.mkdir(parents=True, exist_ok=True)
    # O_APPEND 한 번의 write. 삭제 요청은 기록을 디스크에 동기화한 후에만 진행한다.
    value = (json.dumps({"video_id":video_id,"scope":scope,"at":now_rfc3339()},separators=(",",":"))+"\n").encode()
    fd = os.open(directory / "deletion-ledger.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd,value) != len(value):
            raise OSError("삭제 기록을 완전히 저장할 수 없습니다")
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path: Path) -> list[dict]:
    rows=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        row=json.loads(line)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",row.get("video_id","")) or row.get("scope") not in {"all","media"}:
            raise ValueError("삭제 기록 파일의 형식이 잘못되었습니다")
        rows.append(row)
    return rows
