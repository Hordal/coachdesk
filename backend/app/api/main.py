"""FastAPI 앱 구성."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..auth.service import AuthService
from ..jobs.store import JobStore
from ..media.store import MediaStore, UrlSigner
from ..pipeline.impl.fixture import load_fixtures
from ..services.deletion import DeletionService
from ..services.designation import DesignationRepo
from ..services.observations import ObservationRepo
from ..services.poses import PoseRepo
from ..services.uploads import UploadService
from ..settings import Settings
from ..storage.db import Database
from ..storage.memory import RunStore
from ..storage.videos import VideoRepo
from .errors import install_error_handlers
from .v1 import auth, corrections, intake, observations, poses, runs, uploads, videos, workspace, teams, analysis


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate_runtime()
    app = FastAPI(
        title="격투기 영상 복기 도구 API",
        version="0.3.0",
        description="영상 업로드·준비, 분석 산출물 가져오기, 선수 지정, 근거 복기와 계정 인증. 자동 추론 모델은 별도 연결이 필요합니다.",
    )
    db = Database(settings.db_path)
    (settings.data_dir / "deletion-ledger.jsonl").touch(exist_ok=True)
    app.state.settings = settings
    app.state.db = db
    app.state.store = RunStore(db)
    app.state.videos = VideoRepo(db)
    app.state.jobs = JobStore(db, lease_seconds=settings.lease_seconds)
    app.state.uploads = UploadService(db, settings, app.state.jobs)
    app.state.deletion = DeletionService(db, settings, app.state.videos, app.state.jobs)
    app.state.auth = AuthService(db, settings)
    app.state.observations = ObservationRepo(db, settings)
    app.state.poses = PoseRepo(db, settings)
    app.state.designations = DesignationRepo(db, app.state.observations)
    app.state.media = MediaStore(settings.media_dirs)
    app.state.signer = UrlSigner(settings.media_secret, settings.media_url_ttl_seconds)
    load_fixtures(app.state.store, settings.resolved_fixture_runs(), settings.dev_user_id)

    install_error_handlers(app)

    @app.get("/api/v1/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(corrections.router, prefix="/api/v1")
    app.include_router(observations.router, prefix="/api/v1")
    app.include_router(poses.router, prefix="/api/v1")
    app.include_router(videos.router, prefix="/api/v1")
    app.include_router(uploads.router, prefix="/api/v1")
    app.include_router(intake.router, prefix="/api/v1")
    app.include_router(workspace.router, prefix="/api/v1")
    app.include_router(teams.router, prefix="/api/v1")
    app.include_router(analysis.router, prefix="/api/v1")

    @app.middleware("http")
    async def response_protection(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if "text/html" in response.headers.get("content-type", "") and request.url.path not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # 로컬 패키지는 빌드된 화면과 API를 같은 주소에서 제공한다.
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if (frontend / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend_page(path: str):
            if path == "api" or path.startswith("api/"):
                from .errors import ProblemError
                raise ProblemError(404, "not_found", "API를 찾을 수 없음")
            return FileResponse(frontend / "index.html")
    return app
