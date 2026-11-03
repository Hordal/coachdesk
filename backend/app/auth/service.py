"""초대 계정, 로그인, 세션(상세 설계 21).

비밀번호는 검증된 라이브러리(argon2-cffi)로만 해시하고, 직접 만든 암호 방식을 쓰지 않는다.
초대 토큰과 세션 토큰은 무작위 값이며 DB에는 SHA-256 해시만 저장한다. 구체 인증 제공자는 배포 환경을
정할 때 바꿀 수 있도록 이 서비스 뒤에 둔다.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from ..settings import Settings
from ..storage.db import Database, now_rfc3339

CONSENT_VERSION = "unreviewed-0"  # 이용 안내·동의 문구는 검토 전이다. 문구가 확정되면 버전을 올린다.
ROLES = ("participant", "admin")


class AuthError(Exception):
    def __init__(self, status: int, code: str, title: str, **extra: object) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.extra = status, code, title, extra


@dataclass(frozen=True)
class SessionUser:
    user_id: str
    email: str
    display_name: str
    role: str
    csrf_token: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(self, db: Database, settings: Settings, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._settings = settings
        self._clock = clock
        self._hasher = PasswordHasher()
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))  # 없는 계정도 같은 시간을 쓰게 한다

    # --- 감사 ----------------------------------------------------------------------

    def audit(self, actor: str | None, event: str, detail: dict[str, Any] | None = None) -> None:
        """누가 무엇을 했는지만 남긴다. 비밀번호·토큰·이메일 본문은 넣지 않는다."""
        with self._db.tx(immediate=True) as conn:
            conn.execute("INSERT INTO audit_log (at, actor, event, detail_json) VALUES (?, ?, ?, ?)", (now_rfc3339(), actor, event, json.dumps(detail) if detail else None))

    # --- 비밀번호 --------------------------------------------------------------------

    def _check_password_policy(self, password: str) -> None:
        # 길이 기준만 둔다(구성 규칙은 강요하지 않는다). 너무 긴 값은 해시 비용을 키우므로 막는다.
        if len(password) < self._settings.password_min_length:
            raise AuthError(422, "weak_password", f"비밀번호는 {self._settings.password_min_length}자 이상이어야 함")
        if len(password) > 128:
            raise AuthError(422, "weak_password", "비밀번호는 128자 이하여야 함")

    def _verify(self, stored: str, password: str) -> bool:
        try:
            return self._hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False

    # --- 사용자 ----------------------------------------------------------------------

    def create_user(self, *, email: str, password: str, display_name: str, role: str = "participant", consented: bool = False) -> str:
        """관리자 계정 만들기(명령줄)와 초대 수락이 쓴다."""
        if role not in ROLES:
            raise AuthError(422, "invalid_role", "알 수 없는 역할")
        self._check_password_policy(password)
        user_id = secrets.token_hex(16)
        email = normalize_email(email)
        with self._db.tx(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise AuthError(409, "email_taken", "이미 계정이 있는 이메일")
            conn.execute(
                "INSERT INTO users (user_id, email, display_name, password_hash, role, status, consent_version, consented_at, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (user_id, email, display_name.strip(), self._hasher.hash(password), role, CONSENT_VERSION if consented else None, now_rfc3339() if consented else None, now_rfc3339()),
            )
        return user_id

    def list_users(self) -> list[dict[str, Any]]:
        with self._db.tx() as conn:
            rows = conn.execute("SELECT user_id, email, display_name, role, status, created_at, last_login_at FROM users ORDER BY created_at, user_id").fetchall()
        return [dict(r) for r in rows]

    def set_blocked(self, actor: str, user_id: str, blocked: bool) -> None:
        with self._db.tx(immediate=True) as conn:
            row = conn.execute("SELECT role, status FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthError(404, "user_not_found", "계정을 찾을 수 없음")
            if blocked and user_id == actor:
                raise AuthError(409, "cannot_block_self", "자기 자신은 차단할 수 없음")
            conn.execute("UPDATE users SET status = ? WHERE user_id = ?", ("blocked" if blocked else "active", user_id))
            if blocked:  # 차단하면 열려 있는 세션도 바로 끊는다
                conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now_rfc3339(), user_id))
        self.audit(actor, "user_blocked" if blocked else "user_unblocked", {"user_id": user_id})

    # --- 초대 ------------------------------------------------------------------------

    def create_invite(self, actor: str, email: str, role: str = "participant", ttl_hours: int | None = None) -> tuple[str, str, float]:
        """초대를 만들고 (invite_id, 토큰, 만료 시각)을 돌려준다. 토큰은 이때만 볼 수 있다."""
        if role not in ROLES:
            raise AuthError(422, "invalid_role", "알 수 없는 역할")
        email = normalize_email(email)
        invite_id, token = secrets.token_hex(8), secrets.token_urlsafe(32)
        expires = self._clock() + (ttl_hours or self._settings.invite_ttl_hours) * 3600
        with self._db.tx(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise AuthError(409, "email_taken", "이미 계정이 있는 이메일")
            conn.execute(
                "INSERT INTO invites (invite_id, token_hash, email, role, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (invite_id, _digest(token), email, role, actor, now_rfc3339(), expires),
            )
        self.audit(actor, "invite_created", {"invite_id": invite_id, "role": role})
        return invite_id, token, expires

    def list_invites(self) -> list[dict[str, Any]]:
        with self._db.tx() as conn:
            rows = conn.execute("SELECT invite_id, email, role, created_at, expires_at, accepted_at, revoked_at FROM invites ORDER BY created_at DESC").fetchall()
        now = self._clock()
        result = []
        for r in rows:
            state = "accepted" if r["accepted_at"] else "revoked" if r["revoked_at"] else "expired" if r["expires_at"] <= now else "pending"
            result.append({**dict(r), "state": state})
        return result

    def revoke_invite(self, actor: str, invite_id: str) -> None:
        with self._db.tx(immediate=True) as conn:
            row = conn.execute("SELECT accepted_at FROM invites WHERE invite_id = ?", (invite_id,)).fetchone()
            if row is None:
                raise AuthError(404, "invite_not_found", "초대를 찾을 수 없음")
            if row["accepted_at"]:
                raise AuthError(409, "invite_used", "이미 수락된 초대")
            conn.execute("UPDATE invites SET revoked_at = COALESCE(revoked_at, ?) WHERE invite_id = ?", (now_rfc3339(), invite_id))
        self.audit(actor, "invite_revoked", {"invite_id": invite_id})

    def _open_invite(self, conn: Any, token: str) -> Any:
        row = conn.execute("SELECT * FROM invites WHERE token_hash = ?", (_digest(token),)).fetchone()
        # 없는 토큰·만료·폐기·사용 완료를 구별하지 않는다. 어떤 토큰이 한때 유효했는지 알리지 않기 위해서다.
        if row is None or row["accepted_at"] or row["revoked_at"] or row["expires_at"] <= self._clock():
            raise AuthError(404, "invite_invalid", "유효하지 않거나 만료된 초대")
        return row

    def invite_info(self, token: str) -> dict[str, str]:
        with self._db.tx() as conn:
            row = self._open_invite(conn, token)
        return {"email": row["email"], "role": row["role"]}

    def accept_invite(self, token: str, password: str, display_name: str, consent: bool) -> str:
        if not consent:
            raise AuthError(422, "consent_required", "이용 안내와 영상 이용에 동의해야 계정을 만들 수 있음")
        if not display_name.strip():
            raise AuthError(422, "invalid_name", "이름을 입력해야 함")
        self._check_password_policy(password)
        user_id = secrets.token_hex(16)
        with self._db.tx(immediate=True) as conn:
            invite = self._open_invite(conn, token)
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (invite["email"],)).fetchone():
                raise AuthError(409, "email_taken", "이미 계정이 있는 이메일")
            conn.execute(
                "INSERT INTO users (user_id, email, display_name, password_hash, role, status, consent_version, consented_at, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (user_id, invite["email"], display_name.strip(), self._hasher.hash(password), invite["role"], CONSENT_VERSION, now_rfc3339(), now_rfc3339()),
            )
            conn.execute("UPDATE invites SET accepted_at = ?, accepted_user_id = ? WHERE invite_id = ?", (now_rfc3339(), user_id, invite["invite_id"]))
        self.audit(user_id, "invite_accepted", {"invite_id": invite["invite_id"]})
        return user_id

    # --- 로그인·세션 -----------------------------------------------------------------

    def _failures(self, conn: Any, key: str) -> int:
        since = self._clock() - self._settings.login_window_seconds
        return conn.execute("SELECT COUNT(*) FROM login_attempts WHERE key = ? AND at > ?", (key, since)).fetchone()[0]

    def login(self, email: str, password: str, client: str = "") -> tuple[str, SessionUser]:
        email = normalize_email(email)
        keys = [f"email:{email}"] + ([f"ip:{client}"] if client else [])
        with self._db.tx(immediate=True) as conn:
            conn.execute("DELETE FROM login_attempts WHERE at <= ?", (self._clock() - self._settings.login_window_seconds,))
            if any(self._failures(conn, k) >= self._settings.login_max_failures * (4 if k.startswith("ip:") else 1) for k in keys):
                raise AuthError(429, "too_many_attempts", "로그인 시도가 너무 많음. 잠시 뒤 다시 시도해야 함", retry_after=self._settings.login_window_seconds)
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        ok = self._verify(row["password_hash"] if row else self._dummy_hash, password) and row is not None
        if not ok:
            with self._db.tx(immediate=True) as conn:
                conn.executemany("INSERT INTO login_attempts (key, at) VALUES (?, ?)", [(k, self._clock()) for k in keys])
            self.audit(None, "login_failed")
            raise AuthError(401, "invalid_credentials", "이메일 또는 비밀번호가 올바르지 않음")
        assert row is not None
        if row["status"] != "active":
            self.audit(row["user_id"], "login_blocked")
            raise AuthError(403, "account_blocked", "사용할 수 없는 계정. 관리자에게 문의해야 함")

        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self._db.tx(immediate=True) as conn:
            conn.execute("DELETE FROM login_attempts WHERE key = ?", (keys[0],))
            conn.execute("INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)", (_digest(token), row["user_id"], csrf, now_rfc3339(), self._clock() + self._settings.session_ttl_seconds))
            conn.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?", (now_rfc3339(), row["user_id"]))
        self.audit(row["user_id"], "login")
        return token, SessionUser(row["user_id"], row["email"], row["display_name"], row["role"], csrf)

    def session_user(self, token: str | None) -> SessionUser | None:
        if not token:
            return None
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT u.user_id, u.email, u.display_name, u.role, s.csrf_token FROM sessions s JOIN users u ON u.user_id = s.user_id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.status = 'active'",
                (_digest(token), self._clock()),
            ).fetchone()
        return SessionUser(row["user_id"], row["email"], row["display_name"], row["role"], row["csrf_token"]) if row else None

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._db.tx(immediate=True) as conn:
            conn.execute("UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE token_hash = ?", (now_rfc3339(), _digest(token)))

    @staticmethod
    def csrf_matches(expected: str, given: str | None) -> bool:
        return given is not None and secrets.compare_digest(expected, given)
