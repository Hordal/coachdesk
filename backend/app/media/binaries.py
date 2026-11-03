"""FFmpeg·FFprobe 실행 파일 위치. 환경 변수 → PATH → winget 설치 위치 순으로 찾는다."""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path


class BinaryNotFound(RuntimeError):
    pass


def find_binary(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_PATH")
    if env and Path(env).exists():
        return env
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pattern = str(Path(local) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg*" / "ffmpeg-*" / "bin" / f"{name}.exe")
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    raise BinaryNotFound(f"{name}을(를) 찾을 수 없음. PATH 또는 {name.upper()}_PATH를 설정할 것")


def available() -> bool:
    try:
        find_binary("ffmpeg")
        find_binary("ffprobe")
        return True
    except BinaryNotFound:
        return False
