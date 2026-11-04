"""요청 공통 의존성. 인증은 구현 순서 9단계에서 교체한다(상세 설계 27)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from ..auth.service import AuthService
from ..jobs.store import JobStore
from ..media.store import MediaStore, UrlSigner
from ..services.deletion import DeletionService
from ..services.designation import DesignationRepo
from ..services.observations import ObservationRepo
from ..services.poses import PoseRepo
from ..services.uploads import UploadService
from ..storage.memory import RunStore, StoredRun
from ..storage.videos import VideoRepo
from .errors import ProblemError


@dataclass(frozen=True)
class User:
    user_id: str
    role: str = "participant"
    email: str | None = None
    display_name: str | None = None
    csrf_token: str | None = None
    scope_id: str | None = None
    workspace_role: str = "owner"

    @property
    def owner_id(self) -> str:
        return self.scope_id or self.user_id


def get_store(request: Request) -> RunStore:
    return request.app.state.store


def get_media(request: Request) -> MediaStore:
    return request.app.state.media


def get_signer(request: Request) -> UrlSigner:
    return request.app.state.signer


def get_jobs(request: Request) -> JobStore:
    return request.app.state.jobs


def get_videos(request: Request) -> VideoRepo:
    return request.app.state.videos


def get_uploads(request: Request) -> UploadService:
    return request.app.state.uploads


def get_designations(request: Request) -> DesignationRepo:
    return request.app.state.designations


def get_observations(request: Request) -> ObservationRepo:
    return request.app.state.observations


def get_poses(request: Request) -> PoseRepo:
    return request.app.state.poses


def get_deletion(request: Request) -> DeletionService:
    return request.app.state.deletion


SESSION_COOKIE = "session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def workspace_cookie(request: Request) -> str:
    # 쿠키는 포트로 격리되지 않으므로 같은 PC의 QA/운영 주소를 구분한다.
    return "workspace_team" + (f"_{request.url.port}" if request.url.port is not None else "")


def get_auth(request: Request) -> AuthService:
    return request.app.state.auth


def authenticated_user(request: Request) -> User:
    """dev 모드는 고정 계정(로그인 없음), session 모드는 쿠키 세션의 사용자(21).

    쿠키로 인증된 변경 요청(POST·PUT·DELETE)은 세션에 묶인 CSRF 토큰 헤더가 맞아야 한다.
    테스트는 dependency_overrides로 다른 계정을 흉내 낸다.
    """
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        if settings.public_demo:
            return User(user_id="public-demo", role="participant", display_name="공개 체험 공간")
        return User(user_id=settings.dev_user_id, role="admin", display_name="로컬 코치" if settings.app_profile == "workstation" else "개발 계정")
    session = request.app.state.auth.session_user(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise ProblemError(401, "unauthenticated", "로그인이 필요함")
    if request.method not in SAFE_METHODS and not AuthService.csrf_matches(session.csrf_token, request.headers.get(CSRF_HEADER)):
        raise ProblemError(403, "csrf_failed", "요청을 확인할 수 없음. 화면을 새로 고친 뒤 다시 시도해야 함")
    return User(user_id=session.user_id, role=session.role, email=session.email, display_name=session.display_name, csrf_token=session.csrf_token)


def current_user(request: Request, user: User = Depends(authenticated_user)) -> User:
    from dataclasses import replace
    team_id = None if request.app.state.settings.public_demo else request.cookies.get(workspace_cookie(request))
    if team_id:
        with request.app.state.db.tx() as conn:
            row = conn.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, user.user_id)).fetchone()
        if row is None:
            raise ProblemError(403, "workspace_unavailable", "팀 접근 권한이 변경되었습니다. 팀 관리에서 개인 공간으로 전환해 주세요.")
        user = replace(user, scope_id=f"team:{team_id}", workspace_role=row["role"])
    if user.workspace_role == "viewer" and request.method not in SAFE_METHODS:
        raise ProblemError(403, "workspace_read_only", "열람 권한으로는 기록을 변경할 수 없습니다")
    return user


def require_admin(user: User = Depends(authenticated_user)) -> User:
    if user.role != "admin":
        raise ProblemError(403, "forbidden", "권한이 없음")
    return user


def owned_run(run_id: str, user: User = Depends(current_user), store: RunStore = Depends(get_store)) -> StoredRun:
    run = store.get_for_owner(run_id, user.owner_id)
    if run is None:
        raise ProblemError(404, "run_not_found", "분석 실행을 찾을 수 없음", f"run_id={run_id}")
    return run


def owned_video_id(video_id: str, user: User = Depends(current_user), store: RunStore = Depends(get_store)) -> str:
    """사용자의 분석 실행 또는 업로드에 속한 영상만 허용한다. 없는 영상과 남의 영상을 구별하지 않는다."""
    if not store.owns_video(video_id, user.owner_id):
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음", f"video_id={video_id}")
    return video_id
