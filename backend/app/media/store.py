"""영상·관절 파일 저장소(개발용 로컬 디렉터리)와 서명된 접근 주소(상세 설계 21, 27).

영상 파일은 공개 경로에 두지 않는다. 서명 주소는 소유자 확인 후에만 발급하며 짧은 시간 뒤에 만료된다.
디렉터리를 여러 개 받아 앞의 것부터 찾는다(업로드한 영상의 재생용 사본이 개발용 영상보다 먼저).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from pathlib import Path

from ..domain.models import PoseSet

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MediaStore:
    def __init__(self, directories: Path | list[Path]) -> None:
        self._dirs = [directories] if isinstance(directories, Path) else list(directories)

    def _file(self, video_id: str, suffix: str) -> Path | None:
        # video_id를 파일 이름에 쓰므로 경로 조작을 막는다.
        if not _SAFE_ID.match(video_id):
            return None
        for directory in self._dirs:
            path = (directory / f"{video_id}{suffix}").resolve()
            if directory.resolve() in path.parents and path.is_file():
                return path
        return None

    def video_path(self, video_id: str) -> Path | None:
        return self._file(video_id, ".mp4")

    def pose_set(self, video_id: str) -> PoseSet | None:
        path = self._file(video_id, ".poses.json")
        if path is None:
            return None
        return PoseSet.model_validate_json(path.read_text(encoding="utf-8"))


class UrlSigner:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret.encode()
        self._ttl = ttl_seconds

    def _sig(self, video_id: str, expires: int) -> str:
        return hmac.new(self._secret, f"{video_id}|{expires}".encode(), hashlib.sha256).hexdigest()

    def sign(self, video_id: str, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + self._ttl)
        return f"/api/v1/media/{video_id}?exp={expires}&sig={self._sig(video_id, expires)}"

    def verify(self, video_id: str, expires: int, signature: str, now: float | None = None) -> bool:
        if (time.time() if now is None else now) > expires:
            return False
        return hmac.compare_digest(self._sig(video_id, expires), signature)
