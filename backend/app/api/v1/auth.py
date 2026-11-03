"""초대 수락, 로그인·로그아웃, 내 정보, 관리자 기능(상세 설계 21, 27의 3.6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ...auth.service import AuthError, AuthService
from ..deps import SESSION_COOKIE, User, authenticated_user, get_auth, require_admin, workspace_cookie
from ..errors import ProblemError
from ..schemas import (
    InviteAcceptRequest,
    InviteCreated,
    InviteCreateRequest,
    InviteInfo,
    InviteView,
    LoginRequest,
    MeView,
    UserView,
)

router = APIRouter(tags=["auth"])


def _raise(exc: AuthError) -> ProblemError:
    return ProblemError(exc.status, exc.code, exc.title, None, **exc.extra)


def _me(request: Request, user: User) -> MeView:
    return MeView(
        user_id=user.user_id, email=user.email, display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
        csrf_token=user.csrf_token, auth_mode=request.app.state.settings.auth_mode,
    )


@router.get("/me", response_model=MeView)
def me(request: Request, user: User = Depends(authenticated_user)) -> MeView:
    return _me(request, user)


@router.post("/auth/login", response_model=MeView)
def login(request: Request, response: Response, body: LoginRequest, auth: AuthService = Depends(get_auth)) -> MeView:
    settings = request.app.state.settings
    if settings.auth_mode == "dev":
        raise ProblemError(404, "auth_disabled", "dev 모드에서는 로그인이 없음")
    try:
        token, session = auth.login(body.email, body.password, request.client.host if request.client else "")
    except AuthError as exc:
        raise _raise(exc) from exc
    response.delete_cookie(workspace_cookie(request), path="/")
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_seconds, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
    return MeView(
        user_id=session.user_id, email=session.email, display_name=session.display_name,
        role=session.role,  # type: ignore[arg-type]
        csrf_token=session.csrf_token, auth_mode="session",
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, user: User = Depends(authenticated_user), auth: AuthService = Depends(get_auth)) -> None:
    """세션을 서버에서 폐기한다. 쿠키만 지우는 것이 아니라 토큰을 더 이상 쓸 수 없게 한다."""
    auth.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(workspace_cookie(request), path="/")


@router.get("/auth/invites/{token}", response_model=InviteInfo)
def invite_info(token: str, auth: AuthService = Depends(get_auth)) -> InviteInfo:
    try:
        return InviteInfo(**auth.invite_info(token))  # type: ignore[arg-type]
    except AuthError as exc:
        raise _raise(exc) from exc


@router.post("/auth/invites/{token}/accept", response_model=InviteInfo, status_code=status.HTTP_201_CREATED)
def accept_invite(token: str, body: InviteAcceptRequest, auth: AuthService = Depends(get_auth)) -> InviteInfo:
    """초대 수락과 계정 생성. 공개 가입은 없고 초대를 받은 이메일로만 계정을 만든다."""
    try:
        info = auth.invite_info(token)
        auth.accept_invite(token, body.password, body.display_name, body.consent)
    except AuthError as exc:
        raise _raise(exc) from exc
    return InviteInfo(**info)  # type: ignore[arg-type]


# --- 관리자 ---------------------------------------------------------------------------

@router.post("/admin/invites", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
def create_invite(body: InviteCreateRequest, admin: User = Depends(require_admin), auth: AuthService = Depends(get_auth)) -> InviteCreated:
    try:
        invite_id, token, expires = auth.create_invite(admin.user_id, body.email, body.role, body.ttl_hours)
    except AuthError as exc:
        raise _raise(exc) from exc
    return InviteCreated(invite_id=invite_id, accept_path=f"/invite/{token}", expires_at=expires)


@router.get("/admin/invites", response_model=list[InviteView])
def list_invites(_: User = Depends(require_admin), auth: AuthService = Depends(get_auth)) -> list[InviteView]:
    return [InviteView(**row) for row in auth.list_invites()]


@router.delete("/admin/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invite_id: str, admin: User = Depends(require_admin), auth: AuthService = Depends(get_auth)) -> None:
    try:
        auth.revoke_invite(admin.user_id, invite_id)
    except AuthError as exc:
        raise _raise(exc) from exc


@router.get("/admin/users", response_model=list[UserView])
def list_users(_: User = Depends(require_admin), auth: AuthService = Depends(get_auth)) -> list[UserView]:
    return [UserView(**row) for row in auth.list_users()]


@router.post("/admin/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
def block_user(user_id: str, admin: User = Depends(require_admin), auth: AuthService = Depends(get_auth)) -> None:
    try:
        auth.set_blocked(admin.user_id, user_id, True)
    except AuthError as exc:
        raise _raise(exc) from exc


@router.post("/admin/users/{user_id}/unblock", status_code=status.HTTP_204_NO_CONTENT)
def unblock_user(user_id: str, admin: User = Depends(require_admin), auth: AuthService = Depends(get_auth)) -> None:
    try:
        auth.set_blocked(admin.user_id, user_id, False)
    except AuthError as exc:
        raise _raise(exc) from exc
