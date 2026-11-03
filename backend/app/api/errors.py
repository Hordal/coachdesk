"""HTTP 오류 응답: RFC 9457 Problem Details(상세 설계 14).

사용자에게 스택, 비밀키, 로컬 경로를 반환하지 않는다.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None, **extra: object) -> None:
        super().__init__(title)
        self.extra = extra
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def _problem(request: Request, status: int, code: str, title: str, detail: str | None, **extra: object) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:fight-review:problem:{code}",
        "title": title,
        "status": status,
        "instance": request.url.path,
        "code": code,
        "trace_id": uuid.uuid4().hex,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem_error(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem(request, exc.status, exc.code, exc.title, exc.detail, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _problem(request, exc.status_code, code, str(exc.detail), None)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _problem(request, 422, "invalid_request", "요청 값이 올바르지 않음", None, errors=errors)
