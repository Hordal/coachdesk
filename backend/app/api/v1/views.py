"""저장소 기록을 API 응답으로 바꾼다."""

from __future__ import annotations

from datetime import datetime, timedelta

from ...jobs.store import JobRecord
from ...services.uploads import UploadRecord
from ...storage.videos import VideoRecord
from ..schemas import JobErrorView, JobStageView, JobView, UploadView, VideoSummary


def job_view(job: JobRecord) -> JobView:
    error = None
    if job.error:
        error = JobErrorView(
            code=job.error.get("code", "unknown"),
            title=job.error.get("title", "처리하지 못했습니다"),
            stage=job.error.get("stage"),
            retryable=bool(job.error.get("retryable", False)),
            trace_id=job.error.get("trace_id"),
            at_us=job.error.get("at_us"),
        )
    return JobView(
        job_id=job.job_id,
        kind=job.kind,
        status=job.status,  # type: ignore[arg-type]
        stage=job.stage,
        progress=job.progress,
        stages=[
            JobStageView(
                name=s["name"], label=s["label"], status=s["status"], progress=s.get("progress", 0.0),
                message=s.get("message"), reason_code=s.get("reason_code"),
            )
            for s in job.stages
        ],
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        error=error,
        run_id=job.run_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def upload_view(upload: UploadRecord) -> UploadView:
    return UploadView(
        upload_id=upload.upload_id, filename=upload.filename, size_bytes=upload.size_bytes, part_size=upload.part_size,
        part_count=upload.part_count, sport=upload.sport,  # type: ignore[arg-type]
        status=upload.status,  # type: ignore[arg-type]
        received_parts=upload.received_parts, video_id=upload.video_id, job_id=upload.job_id,
    )


def _expires_at(video: VideoRecord, retention_days: int) -> str | None:
    """원본·재생용 사본의 자동 삭제 예정 시각. 영상 준비가 끝난 때부터 센다."""
    if retention_days <= 0 or video.media_state != "present" or video.status != "ready":
        return None
    start = datetime.fromisoformat(video.ready_at or video.created_at)
    return (start + timedelta(days=retention_days)).isoformat(timespec="milliseconds")


def video_summary(video: VideoRecord, job: JobRecord | None, run_id: str | None, retention_days: int = 0) -> VideoSummary:
    return VideoSummary(
        video_id=video.video_id, filename=video.filename, sport=video.sport,  # type: ignore[arg-type]
        size_bytes=video.size_bytes, status=video.status,  # type: ignore[arg-type]
        duration_us=video.duration_us, width=video.width, height=video.height, created_at=video.created_at,
        job=job_view(job) if job else None, run_id=run_id,
        media_state=video.media_state,  # type: ignore[arg-type]
        media_expires_at=_expires_at(video, retention_days),
    )
