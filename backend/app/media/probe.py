"""영상 정보와 프레임 표(상세 설계 05).

프레임 시각은 ffprobe가 읽은 원래의 표시 시각(PTS)이며, 명목 fps로 만들지 않는다.
프레임 번호는 표시 순서이고 시작 프레임이 0이다.
시각 계산(정수 PTS × time_base를 반올림한 마이크로초)과 가변 프레임률 판정(평균 프레임률과 보고된
프레임률의 비교)은 선수 검출·추적 파이프라인(MMACoachAgent 저장소의 `ai/modules/video`)과 같은 방식이다.
"""

from __future__ import annotations

import json
import math
import subprocess
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .binaries import find_binary


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrameEntry:
    index: int
    pts_us: int


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration_us: int
    frame_count: int
    nominal_fps: float | None  # 스트림이 선언한 값. 시각 계산에는 쓰지 않는다
    average_fps: float | None
    is_vfr: bool
    frames: tuple[FrameEntry, ...]


@dataclass(frozen=True)
class SourceInfo:
    """업로드한 원본의 입장 검사용 정보. 디코딩하지 않고 컨테이너 정보만 읽는다."""

    container: str
    video_codec: str
    width: int
    height: int
    duration_us: int | None
    rotation: int
    has_audio: bool


_cache: dict[tuple[str, int], VideoInfo] = {}


def _run(args: list[str]) -> bytes:
    try:
        result = subprocess.run(args, capture_output=True, check=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise MediaError(f"FFmpeg 실행 실패: {exc.stderr.decode('utf-8', 'replace')[-300:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("FFmpeg 실행 시간 초과") from exc
    return result.stdout


def _fps(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def parse_time_base(value: object) -> tuple[int, int] | None:
    """'1/90000' 같은 time_base. 해석할 수 없으면 None."""
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, denominator = value.split("/", 1)
    try:
        num, den = int(numerator), int(denominator)
    except ValueError:
        return None
    return (num, den) if den != 0 else None


def pts_to_us(pts: int, time_base: tuple[int, int]) -> int:
    """PTS와 time_base를 반올림(0.5는 올림)한 마이크로초로 바꾼다. 실패를 0으로 바꾸지 않는다."""
    num, den = time_base
    product = pts * num * 1_000_000
    if product >= 0:
        return (product + den // 2) // den
    return -((-product + den // 2) // den)


def is_variable_frame_rate(average_fps: float | None, reported_fps: float | None, gaps_us: list[int]) -> bool:
    """평균 프레임률과 보고된 프레임률이 다르면(상대 오차 0.1% 초과) 가변 프레임률이다.

    두 값을 모두 알 수 없을 때만 실제 프레임 간격으로 판단한다. 시각 계산은 이 값과 무관하게 항상 프레임별 PTS를 쓴다.
    """
    if average_fps and reported_fps:
        return not math.isclose(average_fps, reported_fps, rel_tol=1e-3)
    return bool(gaps_us) and (max(gaps_us) - min(gaps_us)) > 2  # 반올림 오차(1µs)는 같은 간격으로 본다


def inspect_source(path: Path) -> SourceInfo:
    """실제 스트림을 읽어 컨테이너·코덱·길이·해상도를 확인한다. 읽을 수 없으면 MediaError."""
    ffprobe = find_binary("ffprobe")
    try:
        data = json.loads(
            _run([ffprobe, "-v", "error", "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,width,height,duration:stream_side_data=rotation", "-of", "json", str(path)])
        )
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe 출력을 해석할 수 없음") from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("영상 스트림이 없음")
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            # ffprobe의 Display Matrix 회전은 반시계 방향이다. 분석 파이프라인과 같이 시계 방향 각도로 기록한다.
            rotation = (-int(round(float(side["rotation"])))) % 360
    duration = (data.get("format") or {}).get("duration") or video.get("duration")
    return SourceInfo(
        container=(data.get("format") or {}).get("format_name", ""),
        video_codec=video.get("codec_name", ""),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_us=round(float(duration) * 1_000_000) if duration not in (None, "N/A") else None,
        rotation=rotation,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def probe_video(path: Path) -> VideoInfo:
    """영상 정보와 전체 프레임 표를 읽는다. 디코딩하지 않고 패킷만 읽으므로 빠르다."""
    key = (str(path), path.stat().st_mtime_ns)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    ffprobe = find_binary("ffprobe")
    stream_json = json.loads(
        _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,r_frame_rate,avg_frame_rate,duration,time_base:stream_side_data=rotation", "-of", "json", str(path)])
    )
    streams = stream_json.get("streams") or []
    if not streams:
        raise MediaError("영상 스트림이 없음")
    stream = streams[0]

    time_base = parse_time_base(stream.get("time_base"))
    if time_base is None:
        raise MediaError("time_base를 읽을 수 없음(시간 정보 누락)")
    packets = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts", "-of", "csv=p=0", str(path)])
    pts_list = sorted(pts_to_us(int(line), time_base) for line in packets.decode().split() if line and line != "N/A")
    if not pts_list:
        raise MediaError("프레임 시각을 읽을 수 없음(시간 정보 누락)")
    if len(set(pts_list)) != len(pts_list):
        raise MediaError("표시 시각이 겹치는 프레임이 있음(시간 정보 이상)")

    gaps = [b - a for a, b in zip(pts_list, pts_list[1:])]
    nominal, average = _fps(stream.get("r_frame_rate")), _fps(stream.get("avg_frame_rate"))
    is_vfr = is_variable_frame_rate(average, nominal, gaps)

    duration_us = round(float(stream["duration"]) * 1_000_000) if stream.get("duration") not in (None, "N/A") else pts_list[-1]
    info = VideoInfo(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration_us=duration_us,
        frame_count=len(pts_list),
        nominal_fps=nominal,
        average_fps=average,
        is_vfr=is_vfr,
        frames=tuple(FrameEntry(i, p) for i, p in enumerate(pts_list)),
    )
    _cache[key] = info
    return info


def frames_in_range(info: VideoInfo, from_us: int, to_us: int) -> list[FrameEntry]:
    """[from_us, to_us) 구간에 표시되는 프레임. 구간 시작 시점에 보이던 프레임을 포함한다."""
    times = [f.pts_us for f in info.frames]
    first = max(bisect_right(times, from_us) - 1, 0)
    return [f for f in info.frames[first:] if f.pts_us < to_us]


def frame_at(info: VideoInfo, time_us: int) -> FrameEntry:
    """time_us에 화면에 표시되는 프레임(표시 시각이 time_us 이하인 마지막 프레임)."""
    times = [f.pts_us for f in info.frames]
    idx = bisect_right(times, time_us) - 1
    return info.frames[max(idx, 0)]


def extract_frame_jpeg(path: Path, info: VideoInfo, frame: FrameEntry) -> bytes:
    """디코더가 식별한 정확한 프레임을 JPEG로 낸다.

    표시 시각을 그대로 탐색 지점으로 쓰면 반올림 때문에 다음 프레임이 나올 수 있으므로,
    이전 프레임과의 중간 지점으로 탐색한다.
    """
    prev_pts = info.frames[frame.index - 1].pts_us if frame.index > 0 else frame.pts_us
    seek_us = frame.pts_us - (frame.pts_us - prev_pts) // 2
    ffmpeg = find_binary("ffmpeg")
    return _run(
        [ffmpeg, "-v", "error", "-ss", f"{seek_us / 1_000_000:.6f}", "-i", str(path),
         "-frames:v", "1", "-an", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    )
