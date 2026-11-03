"""재개 가능한 분할 업로드와 작업 조회·취소(상세 설계 12, 27의 3.6)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.concurrency import run_in_threadpool

from ...jobs.store import JobStore
from ...services.uploads import UploadError, UploadService
from ..deps import User, current_user, get_jobs, get_uploads
from ..errors import ProblemError
from ..schemas import JobView, UploadCreateRequest, UploadView
from .views import job_view, upload_view

router = APIRouter(tags=["uploads"])


def _problem(exc: UploadError) -> ProblemError:
    return ProblemError(exc.status, exc.code, exc.title, exc.detail)


def _display_name(filename: str) -> str:
    """경로 구분자를 떼어 파일 이름만 남긴다. 이 값은 서버 경로로 쓰지 않는다(21)."""
    return re.split(r"[\\/]", filename)[-1].strip() or "video"


@router.post("/uploads", response_model=UploadView, status_code=201)
def create_upload(
    body: UploadCreateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    user: User = Depends(current_user),
    service: UploadService = Depends(get_uploads),
) -> UploadView:
    """업로드를 시작한다. 같은 Idempotency-Key로 다시 부르면 만들어 둔 업로드를 200으로 돌려준다."""
    try:
        record, created = service.create(user.owner_id, _display_name(body.filename), body.size_bytes, body.sport, idempotency_key)
    except UploadError as exc:
        raise _problem(exc) from exc
    if not created:
        response.status_code = 200
    return upload_view(record)


@router.get("/uploads/{upload_id}", response_model=UploadView)
def get_upload(upload_id: str, user: User = Depends(current_user), service: UploadService = Depends(get_uploads)) -> UploadView:
    """이미 받은 조각을 알려 준다. 끊긴 업로드를 이어 올릴 때 쓴다."""
    record = service.get(user.owner_id, upload_id)
    if record is None:
        raise ProblemError(404, "upload_not_found", "업로드를 찾을 수 없습니다")
    return upload_view(record)


@router.put("/uploads/{upload_id}/parts/{part_number}", response_model=UploadView)
async def put_part(
    upload_id: str,
    part_number: int,
    request: Request,
    x_part_sha256: str | None = Header(default=None, alias="X-Part-Sha256"),
    user: User = Depends(current_user),
    service: UploadService = Depends(get_uploads),
) -> UploadView:
    """조각 하나를 받는다. 같은 조각을 다시 보내면 덮어쓴다."""
    limit = request.app.state.settings.upload_part_size
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise ProblemError(413, "part_too_large", "조각이 너무 큽니다")
    data = await request.body()
    try:
        record = await run_in_threadpool(service.put_part, user.owner_id, upload_id, part_number, data, x_part_sha256)
    except UploadError as exc:
        raise _problem(exc) from exc
    return upload_view(record)


@router.post("/uploads/{upload_id}/complete", response_model=UploadView)
def complete_upload(upload_id: str, user: User = Depends(current_user), service: UploadService = Depends(get_uploads)) -> UploadView:
    """조각을 이어 붙여 실제 영상인지 검사하고, 원본으로 등록한 뒤 처리 작업을 만든다."""
    try:
        return upload_view(service.complete(user.owner_id, upload_id))
    except UploadError as exc:
        raise _problem(exc) from exc


@router.delete("/uploads/{upload_id}", status_code=204)
def abort_upload(upload_id: str, user: User = Depends(current_user), service: UploadService = Depends(get_uploads)) -> Response:
    try:
        service.abort(user.owner_id, upload_id)
    except UploadError as exc:
        raise _problem(exc) from exc
    return Response(status_code=204)


@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, user: User = Depends(current_user), jobs: JobStore = Depends(get_jobs)) -> JobView:
    job = jobs.get_for_owner(job_id, user.owner_id)
    if job is None:
        raise ProblemError(404, "job_not_found", "작업을 찾을 수 없습니다")
    return job_view(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobView)
def cancel_job(job_id: str, user: User = Depends(current_user), jobs: JobStore = Depends(get_jobs)) -> JobView:
    """대기 중이면 바로 취소하고, 처리 중이면 취소를 요청한다. 이미 끝난 작업은 그대로 돌려준다."""
    job = jobs.request_cancel(job_id, user.owner_id)
    if job is None:
        raise ProblemError(404, "job_not_found", "작업을 찾을 수 없습니다")
    return job_view(job)
