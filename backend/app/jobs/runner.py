"""영상 처리기: FFmpeg 실행과 진행률(상세 설계 05, 12, 13).

처리 작업은 이 인터페이스로 FFmpeg를 부른다. 시험에서는 가짜 처리기를 넣어 취소·재시도·중단을 결정적으로 시험한다.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol

from ..media.binaries import BinaryNotFound, find_binary

ProgressFn = Callable[[float | None, int | None], None]  # (진행 비율 또는 None, 처리한 영상 시각 µs)
StopFn = Callable[[], bool]


class StopRequested(Exception):
    """취소·소유권 상실로 하위 프로세스를 멈췄다."""


class MediaDecodeError(Exception):
    """영상을 끝까지 읽거나 변환할 수 없다. at_us는 오류 직전까지 처리한 영상 시각이다."""

    def __init__(self, detail: str, at_us: int | None) -> None:
        super().__init__("media decode error")
        self.detail = detail  # 로그용. 사용자에게 돌려주지 않는다(경로 포함 가능)
        self.at_us = at_us


class MediaRunner(Protocol):
    def decode_check(self, source: Path, duration_us: int | None, on_progress: ProgressFn, should_stop: StopFn) -> None: ...

    def transcode(self, source: Path, dest: Path, duration_us: int | None, on_progress: ProgressFn, should_stop: StopFn) -> None: ...


class FfmpegRunner:
    """FFmpeg를 하위 프로세스로 실행한다. 영상은 순차 디코딩하며 프레임을 메모리에 쌓지 않는다(13)."""

    def __init__(self, *, preset: str = "veryfast", crf: int = 23) -> None:
        self._preset = preset
        self._crf = crf

    def decode_check(self, source: Path, duration_us: int | None, on_progress: ProgressFn, should_stop: StopFn) -> None:
        # 영상 스트림을 끝까지 디코딩해 버린다. 오류 수준 메시지가 하나라도 나오면 손상으로 본다.
        args = ["-nostdin", "-i", str(source), "-map", "0:v:0", "-f", "null", "-"]
        self._run(args, duration_us, on_progress, should_stop, fail_on_stderr=True)

    def transcode(self, source: Path, dest: Path, duration_us: int | None, on_progress: ProgressFn, should_stop: StopFn) -> None:
        # 표시 시각은 그대로 둔다(passthrough). 프레임을 복제·삭제해 시간을 바꾸지 않는다(05).
        # 홀수 크기는 H.264(yuv420p)가 받지 못하므로 짝수로 맞춘다.
        args = [
            "-nostdin", "-y", "-i", str(source), "-map", "0:v:0", "-an",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", self._preset, "-crf", str(self._crf), "-pix_fmt", "yuv420p",
            "-fps_mode", "passthrough", "-video_track_timescale", "90000",
            "-movflags", "+faststart", "-f", "mp4", str(dest),
        ]
        self._run(args, duration_us, on_progress, should_stop, fail_on_stderr=False)

    def _run(self, args: list[str], duration_us: int | None, on_progress: ProgressFn, should_stop: StopFn, *, fail_on_stderr: bool) -> None:
        try:
            ffmpeg = find_binary("ffmpeg")
        except BinaryNotFound as exc:
            raise OSError("ffmpeg를 찾을 수 없음") from exc

        command = [ffmpeg, "-v", "error", "-nostats", "-progress", "pipe:1", *args]
        done_us = 0
        with tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file, text=True, bufsize=1)
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    if should_stop():
                        raise StopRequested()
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us" and value.lstrip("-").isdigit():
                        done_us = max(done_us, int(value))
                        on_progress(min(done_us / duration_us, 1.0) if duration_us else None, done_us)
                if should_stop():
                    raise StopRequested()
                returncode = process.wait()
            except BaseException:
                self._terminate(process)
                raise
            stderr_file.seek(0)
            errors = stderr_file.read().decode("utf-8", "replace").strip()

        if returncode != 0 or (fail_on_stderr and errors):
            raise MediaDecodeError(errors[-500:], done_us or None)
        on_progress(1.0, done_us or None)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
