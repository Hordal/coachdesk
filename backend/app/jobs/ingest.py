"""업로드한 영상의 준비 작업(상세 설계 05, 12, 13).

단계: 영상 전체 읽기 검사 → 재생용 사본 만들기 → 프레임 시각 확인 → 결과 등록.
분석 단계(검출·추적·포즈·판정)는 아직 없으므로 ‘분석’ 단계는 미지원으로 표시하고, 작업은 부분 완료로 끝난다.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..domain.contract_check import check_document
from ..domain.models import ResultBundle
from ..media import probe
from ..media.admission import quality_note
from ..pipeline.registry import RunMode
from ..settings import Settings
from ..storage.memory import RunStore, StoredRun
from ..storage.videos import VideoRecord, VideoRepo
from .errors import JobCancelled, PermanentError, TransientError
from .runner import MediaDecodeError, MediaRunner, StopRequested
from .store import JobStore, LeaseLost

WEIGHTED_STAGES: list[tuple[str, str, float]] = [
    ("decode_check", "영상 전체 읽기 검사", 0.25),
    ("playback_copy", "재생용 사본 만들기", 0.60),
    ("frame_table", "프레임 시각 확인", 0.10),
    ("register", "결과 등록", 0.05),
]

# 재생용 사본의 프레임 시각이 원본과 이 값(µs)을 넘게 다르면 시간 대응을 믿을 수 없다고 본다.
MAX_TIME_MAPPING_ERROR_US = 1_000


def initial_stages() -> list[dict]:
    stages = [
        {"name": name, "label": label, "weight": weight, "status": "pending", "progress": 0.0, "message": None, "reason_code": None}
        for name, label, weight in WEIGHTED_STAGES
    ]
    stages.append(
        {
            "name": "analysis",
            "label": "분석(선수 검출·추적·포즈·판정)",
            "weight": 0.0,
            "status": "unsupported",
            "progress": 0.0,
            "message": "분석 단계는 아직 구현되지 않아 영상 준비까지만 진행합니다",
            "reason_code": "stage_not_implemented",
        }
    )
    return stages


@dataclass
class IngestContext:
    settings: Settings
    jobs: JobStore
    videos: VideoRepo
    runs: RunStore
    runner: MediaRunner
    job_id: str
    token: str
    video: VideoRecord
    check: Callable[[], None]  # 취소면 JobCancelled, 소유권을 잃었으면 LeaseLost
    should_stop: Callable[[], bool]
    run_id: str | None = None
    _last_write: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def source(self) -> Path:
        return Path(self.video.source_path)

    @property
    def playback_path(self) -> Path:
        return self.settings.playback_dir / f"{self.video.video_id}.mp4"

    def progress_cb(self, stage: str) -> Callable[[float | None, int | None], None]:
        """진행률을 기록한다. 데이터베이스 쓰기는 0.5초에 한 번, 1% 이상 변했을 때만 한다."""

        def report(fraction: float | None, _done_us: int | None) -> None:
            if fraction is None:
                return
            last_time, last_value = self._last_write.get(stage, (0.0, -1.0))
            now = time.monotonic()
            if fraction < 1.0 and (now - last_time < 0.5 or fraction - last_value < 0.01):
                return
            self._last_write[stage] = (now, fraction)
            self.jobs.set_stage(self.job_id, self.token, stage, progress=fraction)

        return report


def _stopped(ctx: IngestContext) -> Exception:
    """하위 프로세스가 멈춘 이유를 구별한다. 소유권을 잃었으면 LeaseLost, 취소면 JobCancelled가 난다."""
    try:
        ctx.check()
    except (JobCancelled, LeaseLost) as exc:
        return exc
    return JobCancelled()


def run_decode_check(ctx: IngestContext) -> str:
    info = probe.inspect_source(ctx.source)
    try:
        ctx.runner.decode_check(ctx.source, info.duration_us, ctx.progress_cb("decode_check"), ctx.should_stop)
    except StopRequested:
        raise _stopped(ctx)
    except MediaDecodeError as exc:
        raise PermanentError(
            "decode_error",
            "영상을 끝까지 읽을 수 없습니다. 파일이 손상되었거나 일부만 올라갔을 수 있습니다.",
            at_us=exc.at_us,
        ) from exc
    except OSError as exc:
        raise TransientError("io_error", "영상을 처리하는 중 일시적인 오류가 발생했습니다") from exc
    return "영상 전체를 읽을 수 있습니다"


def run_playback_copy(ctx: IngestContext) -> str:
    final = ctx.playback_path
    tmp = final.with_name(final.name + ".tmp")
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.is_file():  # 이전 시도에서 이미 완성된 사본(체크포인트 이후 재시작)
        try:
            probe.probe_video(final)
            return "이전에 만든 재생용 사본을 사용합니다"
        except probe.MediaError:
            final.unlink(missing_ok=True)

    info = probe.inspect_source(ctx.source)
    try:
        ctx.runner.transcode(ctx.source, tmp, info.duration_us, ctx.progress_cb("playback_copy"), ctx.should_stop)
        probe.probe_video(tmp)  # 검증한 뒤에만 등록한다
        os.replace(tmp, final)  # 같은 저장소 안의 원자적 이름 변경
    except StopRequested:
        tmp.unlink(missing_ok=True)
        raise _stopped(ctx)
    except MediaDecodeError as exc:
        tmp.unlink(missing_ok=True)
        raise PermanentError("transcode_failed", "재생용 영상을 만들 수 없습니다", at_us=exc.at_us) from exc
    except probe.MediaError as exc:
        tmp.unlink(missing_ok=True)
        raise PermanentError("transcode_failed", "재생용 영상을 만들 수 없습니다") from exc
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise TransientError("io_error", "영상을 처리하는 중 일시적인 오류가 발생했습니다") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return "H.264 재생용 사본을 만들었습니다(프레임 시각 유지)"


def run_frame_table(ctx: IngestContext) -> str:
    """원본과 재생용 사본의 프레임 표시 시각이 대응하는지 확인한다. 시간 대응을 믿을 수 없으면 분석하지 않는다(05)."""
    try:
        source = probe.probe_video(ctx.source)
        playback = probe.probe_video(ctx.playback_path)
    except probe.MediaError as exc:
        raise PermanentError("invalid_timestamp", "영상의 시간 정보가 올바르지 않아 시간 기반 분석을 할 수 없습니다. 영상을 다시 내려받거나 다시 촬영해 올려 주세요.") from exc

    if source.frame_count != playback.frame_count:
        raise PermanentError("time_mapping_failed", "재생용 영상과 원본의 프레임 수가 달라 시간을 맞출 수 없습니다")
    offset = playback.frames[0].pts_us - source.frames[0].pts_us
    worst = max((abs((p.pts_us - offset) - s.pts_us) for s, p in zip(source.frames, playback.frames)), default=0)
    if worst > MAX_TIME_MAPPING_ERROR_US:
        raise PermanentError("time_mapping_failed", "재생용 영상과 원본의 프레임 시각이 맞지 않아 시간을 맞출 수 없습니다")

    kind = "가변" if source.is_vfr else "고정"
    fps = source.average_fps or source.nominal_fps
    note = quality_note(source.width, source.height, fps)
    return f"프레임 {source.frame_count:,}개, {kind} 프레임률, 원본과 시각 대응 확인(최대 차이 {worst}µs). {note}"


def run_register(ctx: IngestContext) -> str:
    """분석 결과가 없는 실행을 등록한다. 화면이 영상 재생과 타임라인을 같은 방식으로 열 수 있게 한다."""
    playback = probe.probe_video(ctx.playback_path)
    existing = ctx.videos.latest_run_id(ctx.video.video_id)
    if existing:  # 이전 시도에서 등록한 뒤 중단된 경우. 같은 실행을 두 번 만들지 않는다.
        ctx.run_id = existing
        ctx.videos.set_ready(ctx.video.video_id, duration_us=playback.duration_us, width=playback.width, height=playback.height)
        return "영상 준비를 마쳤습니다"
    run_id = uuid.uuid4().hex
    bundle = {
        "schema_version": "0.1",
        "dataset_kind": "unanalyzed",
        "annotation_source": "none",
        "expert_validated": False,
        "definition_version": "none",
        "video_id": ctx.video.video_id,
        "duration_us": playback.duration_us,
        "sport": ctx.video.sport,
        "time_unit": "us",
        "interval_convention": "[start,end)",
        "notes": ["영상 준비만 끝났고 분석 결과는 없습니다"],
        "participants": [],
    }
    model = ResultBundle.model_validate(bundle)
    report, _ = check_document(bundle, run_id)
    if not report.ok:
        raise PermanentError("invalid_result", "결과를 등록할 수 없습니다")
    ctx.runs.add_persistent(StoredRun(run_id=run_id, owner_id=ctx.video.owner_id, mode=RunMode.FULL, status="partial", bundle=model, raw=bundle))
    ctx.videos.set_ready(ctx.video.video_id, duration_us=playback.duration_us, width=playback.width, height=playback.height)
    ctx.run_id = run_id
    return "영상 준비를 마쳤습니다"


STAGE_FUNCTIONS: dict[str, Callable[[IngestContext], str]] = {
    "decode_check": run_decode_check,
    "playback_copy": run_playback_copy,
    "frame_table": run_frame_table,
    "register": run_register,
}
