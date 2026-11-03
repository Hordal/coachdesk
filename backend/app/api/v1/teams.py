"""개인 공간과 팀 공간의 명시적 전환. 기존 개인 기록은 자동으로 공유하지 않는다."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from ...domain.models import Strict
from ...storage.db import now_rfc3339
from ..deps import User, authenticated_user, workspace_cookie
from ..errors import ProblemError

router = APIRouter(prefix="/teams", tags=["teams"])


class NewTeam(Strict):
    name: str = Field(min_length=1, max_length=80)


class Selection(Strict):
    team_id: str | None = None


class MemberChange(Strict):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["editor", "viewer"]


def membership(request: Request, team_id: str, user: User, *, owner: bool = False):
    with request.app.state.db.tx() as conn:
        row = conn.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, user.user_id)).fetchone()
    if row is None or (owner and row["role"] != "owner"):
        raise ProblemError(403, "team_forbidden", "팀 관리 권한이 없습니다")
    return row["role"]


@router.get("")
def list_teams(request: Request, user: User = Depends(authenticated_user)) -> dict:
    with request.app.state.db.tx() as conn:
        rows = conn.execute("SELECT t.*,m.role FROM teams t JOIN team_members m USING(team_id) WHERE m.user_id=? ORDER BY t.created_at", (user.user_id,)).fetchall()
    selected = request.cookies.get(workspace_cookie(request))
    return {"selected_team_id": selected, "items": [dict(r) for r in rows],
            "auth_mode": request.app.state.settings.auth_mode}


@router.post("", status_code=201)
def create_team(body: NewTeam, request: Request, user: User = Depends(authenticated_user)) -> dict:
    name = body.name.strip()
    if not name:
        raise ProblemError(422, "invalid_name", "팀 이름을 입력해 주세요")
    tid, now = uuid.uuid4().hex, now_rfc3339()
    with request.app.state.db.tx(immediate=True) as conn:
        if conn.execute("SELECT COUNT(*) FROM team_members WHERE user_id=? AND role='owner'", (user.user_id,)).fetchone()[0] >= 20:
            raise ProblemError(409, "team_limit", "팀 생성 한도에 도달했습니다")
        conn.execute("INSERT INTO teams VALUES(?,?,?)", (tid, name, now))
        conn.execute("INSERT INTO team_members VALUES(?,?,'owner',?)", (tid, user.user_id, now))
    return {"team_id": tid, "name": name, "role": "owner"}


@router.put("/selection")
def select_team(body: Selection, request: Request, response: Response, user: User = Depends(authenticated_user)) -> dict:
    if body.team_id:
        membership(request, body.team_id, user)
        response.set_cookie(workspace_cookie(request), body.team_id, httponly=True, samesite="lax", secure=request.app.state.settings.cookie_secure, path="/")
    else:
        response.delete_cookie(workspace_cookie(request), path="/")
    return {"team_id": body.team_id}


@router.get("/{team_id}/members")
def members(team_id: str, request: Request, user: User = Depends(authenticated_user)) -> list[dict]:
    membership(request, team_id, user)
    with request.app.state.db.tx() as conn:
        rows = conn.execute("SELECT m.user_id,m.role,COALESCE(u.display_name,'로컬 코치') display_name,u.email FROM team_members m LEFT JOIN users u USING(user_id) WHERE m.team_id=? ORDER BY m.created_at", (team_id,)).fetchall()
    return [dict(r) for r in rows]


@router.put("/{team_id}/members")
def add_member(team_id: str, body: MemberChange, request: Request, user: User = Depends(authenticated_user)) -> dict:
    # 소유권 검사와 변경은 같은 쓰기 트랜잭션에서 처리한다.
    with request.app.state.db.tx(immediate=True) as conn:
        own = conn.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, user.user_id)).fetchone()
        if own is None or own["role"] != "owner":
            raise ProblemError(403, "team_forbidden", "팀 소유자만 구성원을 변경할 수 있습니다")
        member = conn.execute("SELECT user_id FROM users WHERE email=? COLLATE NOCASE AND status='active'", (body.email.strip(),)).fetchone()
        if member is None:
            raise ProblemError(404, "account_required", "먼저 계정 관리에서 초대하고 가입을 완료해 주세요")
        existing = conn.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, member["user_id"])).fetchone()
        if existing and existing["role"] == "owner":
            raise ProblemError(409, "owner_protected", "소유자 권한은 변경할 수 없습니다")
        conn.execute("INSERT INTO team_members VALUES(?,?,?,?) ON CONFLICT(team_id,user_id) DO UPDATE SET role=excluded.role", (team_id, member["user_id"], body.role, now_rfc3339()))
        conn.execute("INSERT INTO workspace_activity(owner_id,actor,action,target_id,at) VALUES(?,?,?,?,?)", (f"team:{team_id}", user.display_name or user.user_id, "member_updated", member["user_id"], now_rfc3339()))
    return {"user_id": member["user_id"], "role": body.role}


@router.delete("/{team_id}/members/{member_id}", status_code=204)
def remove_member(team_id: str, member_id: str, request: Request, user: User = Depends(authenticated_user)) -> None:
    with request.app.state.db.tx(immediate=True) as conn:
        own = conn.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, user.user_id)).fetchone()
        if own is None or own["role"] != "owner":
            raise ProblemError(403, "team_forbidden", "팀 소유자만 구성원을 변경할 수 있습니다")
        target = conn.execute("SELECT role FROM team_members WHERE team_id=? AND user_id=?", (team_id, member_id)).fetchone()
        if target and target["role"] == "owner":
            raise ProblemError(409, "owner_protected", "팀 소유자는 제거할 수 없습니다")
        conn.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, member_id))
        # 멤버의 접속은 다음 요청부터 차단된다. 이미 발급한 외부 분석 키도 소유자가 별도로 폐기할 수 있다.
        conn.execute("INSERT INTO workspace_activity(owner_id,actor,action,target_id,at) VALUES(?,?,?,?,?)", (f"team:{team_id}", user.display_name or user.user_id, "member_removed", member_id, now_rfc3339()))
