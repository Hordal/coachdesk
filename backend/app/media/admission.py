"""업로드 입장 검사(상세 설계 05, 13).

확장자만이 아니라 실제 스트림(컨테이너, 코덱)을 검사한다. 한도는 settings의 초기값이며
짧은·중간·최대 목표 영상으로 처리량을 측정해 조정한다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..settings import Settings
from .probe import SourceInfo

ALLOWED_EXTENSIONS = {".mp4", ".mov"}
ALLOWED_CONTAINERS = {"mov", "mp4"}  # ffprobe format_name은 "mov,mp4,m4a,3gp,3g2,mj2"처럼 나온다
ALLOWED_CODECS = {"h264", "hevc"}


MIN_SHORT_SIDE, MIN_FPS = 720, 30  # 기본 지원: 720p, 30fps 이상
REC_SHORT_SIDE, REC_FPS = 1080, 60  # 권장: 1080p, 60fps 이상


def quality_note(width: int, height: int, fps: float | None) -> str:
    """영상이 기본 지원·권장 규격을 충족하는지 알리는 문장. 거절 기준이 아니며 분석 가능 범위의 안내로만 쓴다."""
    short = min(width, height)
    rate = fps or 0.0
    below_min = short < MIN_SHORT_SIDE or rate < MIN_FPS - 0.5  # 29.97fps는 30fps로 본다
    if below_min:
        parts = []
        if short < MIN_SHORT_SIDE:
            parts.append(f"해상도 {width}×{height}")
        if rate < MIN_FPS - 0.5:
            parts.append(f"프레임률 {rate:.1f}fps")
        return f"기본 지원 규격(720p·30fps 이상) 미만({', '.join(parts)}). 분석 결과의 신뢰도가 낮을 수 있음"
    if short < REC_SHORT_SIDE or rate < REC_FPS - 0.5:
        return "기본 지원 규격 충족, 권장 규격(1080p·60fps 이상) 미만"
    return "권장 규격(1080p·60fps 이상) 충족"


class Rejection(Exception):
    """입장 검사를 통과하지 못했다. 사용자에게 보여줄 안전한 제목과 설명을 담는다."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def check_declared(filename: str, size_bytes: int, settings: Settings) -> None:
    """업로드를 시작하기 전에 선언한 이름과 크기를 검사한다."""
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise Rejection(422, "unsupported_extension", "지원하지 않는 파일 형식입니다", "MP4 또는 MOV 파일만 올릴 수 있습니다")
    if size_bytes <= 0:
        raise Rejection(422, "empty_file", "빈 파일입니다")
    if size_bytes > settings.max_upload_bytes:
        raise Rejection(413, "too_large", "파일이 너무 큽니다", f"최대 {settings.max_upload_bytes // (1024 * 1024)}MB까지 올릴 수 있습니다")
    check_disk_space(size_bytes, settings)


def check_disk_space(size_bytes: int, settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(settings.data_dir).free
    if free < size_bytes * settings.disk_free_factor:
        raise Rejection(507, "insufficient_storage", "서버의 저장 공간이 부족합니다", "잠시 후 다시 시도하거나 더 작은 영상을 올려 주세요")


def check_source(info: SourceInfo, settings: Settings) -> None:
    """업로드가 끝난 원본의 실제 스트림을 검사한다."""
    formats = set(info.container.split(","))
    if not formats & ALLOWED_CONTAINERS:
        raise Rejection(422, "unsupported_format", "지원하지 않는 영상 형식입니다", "MP4 또는 MOV 컨테이너의 영상만 지원합니다")
    if info.video_codec not in ALLOWED_CODECS:
        raise Rejection(422, "unsupported_codec", "지원하지 않는 영상 코덱입니다", "H.264 또는 HEVC 영상만 지원합니다")
    if info.width <= 0 or info.height <= 0:
        raise Rejection(422, "invalid_media", "영상 크기를 읽을 수 없습니다")
    if info.width * info.height > settings.max_pixels:
        raise Rejection(422, "resolution_too_high", "해상도가 너무 높습니다", "4K(3840×2160) 이하 영상만 지원합니다")
    if info.duration_us is not None and info.duration_us > settings.max_duration_us:
        raise Rejection(422, "too_long", "영상이 너무 깁니다", f"{settings.max_duration_us // 60_000_000}분 이하 영상만 지원합니다")
