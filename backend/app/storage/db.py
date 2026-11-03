"""SQLite 접근(상세 설계 12, 14, 27의 3.4).

짧은 트랜잭션을 쓰고 요청마다 연결을 새로 연다. 여러 프로세스(API, 워커)가 같은 파일을 쓰므로
WAL 모드와 대기 시간을 설정한다. 네트워크 공유 파일로 운영하지 않는다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# 버전별 스키마. 필수 필드·의미를 바꿀 때는 새 버전을 추가하고 이전 버전을 고치지 않는다.
MIGRATIONS: dict[int, str] = {
    12: """
    ALTER TABLE analysis_tasks ADD COLUMN engine TEXT NOT NULL DEFAULT 'external';
    ALTER TABLE analysis_keys ADD COLUMN internal INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE local_engine_presence (worker_id TEXT PRIMARY KEY,seen_at REAL NOT NULL,version TEXT NOT NULL);
    """,
    11: """
    CREATE TABLE upload_locks (upload_id TEXT PRIMARY KEY REFERENCES uploads(upload_id) ON DELETE CASCADE,
        token TEXT NOT NULL, expires_at REAL NOT NULL);
    """,
    10: """
    CREATE TABLE teams (team_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE team_members (team_id TEXT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
        user_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
        created_at TEXT NOT NULL, PRIMARY KEY(team_id,user_id));
    CREATE INDEX team_members_user ON team_members(user_id);
    CREATE TABLE analysis_keys (key_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, label TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked_at TEXT, last_seen REAL);
    CREATE TABLE analysis_tasks (task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('tracking','actions')), status TEXT NOT NULL,
        request_key TEXT NOT NULL, assignment_json TEXT NOT NULL, observation_set_id TEXT,
        lease_hash TEXT, lease_until REAL, key_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', error TEXT,
        result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(owner_id,request_key));
    CREATE INDEX analysis_tasks_queue ON analysis_tasks(owner_id,status,created_at);
    CREATE TABLE run_provenance (run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
        observation_set_id TEXT, pose_set_id TEXT, assignment_json TEXT NOT NULL);
    """,

    9: """
    CREATE TABLE coach_review_history (
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL,
        reviewer TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(run_id,revision)
    );
    INSERT INTO coach_review_history SELECT run_id,owner_id,state,note,reviewer,revision,updated_at FROM coach_reviews;
    """,
    8: """
    CREATE TABLE athletes (
        athlete_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
        discipline TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX athletes_owner ON athletes(owner_id, name);
    CREATE TABLE session_details (
        video_id TEXT PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL, title TEXT NOT NULL, athlete_id TEXT REFERENCES athletes(athlete_id),
        opponent TEXT NOT NULL DEFAULT '', session_date TEXT, session_type TEXT NOT NULL DEFAULT 'sparring',
        notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
    );
    CREATE TABLE coach_reviews (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
        owner_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','in_review','reviewed','changes_requested')),
        note TEXT NOT NULL, reviewer TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE workspace_activity (
        activity_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, target_id TEXT NOT NULL, at TEXT NOT NULL
    );
    CREATE INDEX workspace_activity_owner ON workspace_activity(owner_id, activity_id DESC);
    CREATE TABLE worker_presence (worker_id TEXT PRIMARY KEY, seen_at REAL NOT NULL);
    CREATE INDEX videos_owner_created ON videos(owner_id, created_at DESC);
    CREATE INDEX runs_video_created ON runs(video_id, created_at DESC, run_id DESC);
    """,
    1: """
    CREATE TABLE uploads (
        upload_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        part_size INTEGER NOT NULL,
        part_count INTEGER NOT NULL,
        sport TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('receiving', 'completed', 'aborted', 'failed')),
        idempotency_key TEXT,
        video_id TEXT,
        job_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (owner_id, idempotency_key)
    );
    CREATE TABLE upload_parts (
        upload_id TEXT NOT NULL REFERENCES uploads(upload_id) ON DELETE CASCADE,
        part_number INTEGER NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        PRIMARY KEY (upload_id, part_number)
    );
    CREATE TABLE videos (
        video_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        sport TEXT NOT NULL,
        source_path TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        duration_us INTEGER,
        width INTEGER,
        height INTEGER,
        status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed', 'cancelled')),
        created_at TEXT NOT NULL
    );
    CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        video_id TEXT REFERENCES videos(video_id),
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled')),
        stage TEXT,
        progress REAL NOT NULL DEFAULT 0,
        stages_json TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL,
        lease_token TEXT,
        lease_expires_at REAL,
        heartbeat_at REAL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        error_json TEXT,
        run_id TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX jobs_by_status ON jobs (status, created_at);
    CREATE TABLE job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        stage TEXT,
        event TEXT NOT NULL,
        detail_json TEXT
    );
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        parent_run_id TEXT,
        bundle_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    7: """
    -- 가져온 팀 연구 포즈(27의 9절). 프레임별 대량 데이터는 파일에 두고 요약만 기록한다.
    CREATE TABLE pose_sets (
        set_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        source TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (video_id, content_sha256)
    );
    """,
    6: """
    -- 사건 형식을 팀 동작 검출 모델 형식(phase1b_v1)으로 교체했다. 이전 형식(actor_id 등)의 실행은 읽을 수 없어 관련 기록과 함께 지운다.
    DELETE FROM corrections WHERE run_id IN (SELECT run_id FROM runs WHERE bundle_json LIKE '%"actor_id"%')
        OR parent_run_id IN (SELECT run_id FROM runs WHERE bundle_json LIKE '%"actor_id"%');
    DELETE FROM reports WHERE run_id IN (SELECT run_id FROM runs WHERE bundle_json LIKE '%"actor_id"%');
    DELETE FROM context_notes WHERE run_id IN (SELECT run_id FROM runs WHERE bundle_json LIKE '%"actor_id"%');
    DELETE FROM runs WHERE bundle_json LIKE '%"actor_id"%';
    """,
    5: """
    -- 사용자가 고른 선수와 추적 번호의 대응(11). 지정을 바꾸면 새 행을 추가하고 이전 행은 이력으로 남긴다.
    CREATE TABLE designations (
        designation_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        set_id TEXT NOT NULL,
        assignment_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    4: """
    -- 가져온 선수 검출·추적 결과(27의 9절). 프레임별 대량 데이터는 파일에 두고 요약만 기록한다.
    CREATE TABLE observation_sets (
        set_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        frame_count INTEGER NOT NULL,
        detection_count INTEGER NOT NULL,
        track_count INTEGER NOT NULL,
        source TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        summaries_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (video_id, content_sha256)
    );
    """,
    3: """
    -- 초대 계정과 세션(21). 토큰은 해시만 저장한다.
    CREATE TABLE users (
        user_id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('participant', 'admin')),
        status TEXT NOT NULL CHECK (status IN ('active', 'blocked')),
        consent_version TEXT,
        consented_at TEXT,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    CREATE TABLE invites (
        invite_id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('participant', 'admin')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at REAL NOT NULL,
        accepted_at TEXT,
        accepted_user_id TEXT,
        revoked_at TEXT
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(user_id),
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at REAL NOT NULL,
        revoked_at TEXT
    );
    CREATE INDEX sessions_by_user ON sessions (user_id);
    CREATE TABLE login_attempts (
        key TEXT NOT NULL,
        at REAL NOT NULL
    );
    CREATE INDEX login_attempts_by_key ON login_attempts (key, at);
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        actor TEXT,
        event TEXT NOT NULL,
        detail_json TEXT
    );
    """,
    2: """
    -- 영상 파일의 상태와 삭제 진행(15). deleting=1이면 조회를 막고 정리가 끝날 때까지 다시 시도한다.
    ALTER TABLE videos ADD COLUMN media_state TEXT NOT NULL DEFAULT 'present' CHECK (media_state IN ('present', 'expired', 'deleted'));
    ALTER TABLE videos ADD COLUMN media_removed_at TEXT;
    ALTER TABLE videos ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE videos ADD COLUMN ready_at TEXT;
    CREATE TABLE corrections (
        correction_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        parent_run_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (parent_run_id)
    );
    CREATE TABLE reports (
        report_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        category TEXT NOT NULL,
        subject_ref TEXT,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE context_notes (
        note_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        participant_id TEXT,
        start_us INTEGER,
        end_us INTEGER,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
}


def now_rfc3339() -> str:
    """절대 시각은 RFC 3339 형식으로 기록한다(05)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # 명시적 BEGIN/COMMIT
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _migrate(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version in sorted(MIGRATIONS):
                if version in applied:
                    continue
                conn.executescript(
                    f"BEGIN IMMEDIATE;\n{MIGRATIONS[version]}\n"
                    f"INSERT INTO schema_migrations (version, applied_at) VALUES ({version}, '{now_rfc3339()}');\nCOMMIT;"
                )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def tx(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """짧은 트랜잭션. immediate=True이면 시작 시점에 쓰기 잠금을 잡는다(작업 소유권 확보 등)."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
