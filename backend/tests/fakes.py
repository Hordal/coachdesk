"""가짜 영상 처리기. 실제 FFmpeg 없이 재시도·취소·중단을 결정적으로 시험한다."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from app.jobs.runner import MediaDecodeError, StopRequested


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.decode_error: MediaDecodeError | None = None
        self.unexpected: Exception | None = None
        self.transcode_failures = 0  # 이 횟수만큼 일시적 오류(OSError)를 낸다
        self.transcode_started = threading.Event()
        self.release = threading.Event()
        self.block_transcode = False  # True이면 release가 올 때까지 기다린다(중간에 취소·소유권 상실을 시험)
        self.block_seconds = 0.0  # 정해진 시간 동안 기다린다(heartbeat 시험)
        self.saw_stop = False
        self.progress_seen: list[float] = []

    def decode_check(self, source: Path, duration_us: int | None, on_progress: Callable, should_stop: Callable) -> None:
        self.calls.append("decode")
        if self.unexpected:
            raise self.unexpected
        if self.decode_error:
            raise self.decode_error
        for i in range(1, 5):
            if should_stop():
                raise StopRequested()
            on_progress(i / 4, i * 1_000_000)

    def transcode(self, source: Path, dest: Path, duration_us: int | None, on_progress: Callable, should_stop: Callable) -> None:
        self.calls.append("transcode")
        self.transcode_started.set()
        if self.transcode_failures > 0:
            self.transcode_failures -= 1
            raise OSError("디스크 오류(시험)")
        deadline = time.monotonic() + self.block_seconds
        while (self.block_transcode and not self.release.is_set()) or time.monotonic() < deadline:
            if should_stop():
                self.saw_stop = True
                raise StopRequested()
            time.sleep(0.02)
        for i in range(1, 5):
            if should_stop():
                self.saw_stop = True
                raise StopRequested()
            self.progress_seen.append(i / 4)
            on_progress(i / 4, i * 1_000_000)
        shutil.copyfile(source, dest)  # 원본과 같은 프레임 시각의 사본
