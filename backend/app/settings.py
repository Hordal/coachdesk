"""설정과 한도값. 환경 변수로 덮어쓴다."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MIB = 1024 * 1024
GIB = 1024 * MIB


def _default_fixture_runs(directory: Path) -> dict[str, Path]:
    return {
        "fixture-counter": directory / "02A_가상_사건_데이터.json",
        "fixture-scenario6": directory / "02A_시나리오6_가상_데이터.json",
    }


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Settings:
    app_profile: str = field(default_factory=lambda: os.environ.get("APP_PROFILE", "development"))
    enable_fixtures: bool = field(default_factory=lambda: os.environ.get("ENABLE_FIXTURES", "1") == "1")
    # 인증 방식. dev는 고정 계정(개발·시험 전용, 로그인 없음), session은 초대 계정과 쿠키 세션(21).
    # 배포할 때는 반드시 AUTH_MODE=session과 COOKIE_SECURE=1(TLS)로 실행한다.
    auth_mode: str = field(default_factory=lambda: os.environ.get("AUTH_MODE", "dev"))
    dev_user_id: str = "dev-user"
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("COOKIE_SECURE", "0") == "1")
    session_ttl_seconds: int = 12 * 3600
    invite_ttl_hours: int = 72
    login_max_failures: int = 5  # 창 안에서 이만큼 틀리면 잠시 막는다
    login_window_seconds: int = 900
    password_min_length: int = 10

    # 개발용 영상·관절 파일 위치와 서명 주소 설정(상세 설계 21의 서명 접근)
    media_dir: Path = field(default_factory=lambda: Path(os.environ.get("MEDIA_DIR", REPO_ROOT / "backend" / "dev_media")))
    media_secret: str = field(default_factory=lambda: os.environ.get("MEDIA_SECRET") or secrets.token_hex(32))
    media_url_ttl_seconds: int = 300

    # 업로드·처리 데이터(SQLite, 임시 업로드, 원본, 재생용 사본). 저장소에 포함하지 않는다.
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", REPO_ROOT / "backend" / "data")))

    # 입장 검사 한도(13). 아래 값은 임시이며 짧은·중간·최대 목표 영상으로 측정해 조정한다.
    upload_part_size: int = field(default_factory=lambda: _env_int("UPLOAD_PART_SIZE", 8 * MIB))
    max_upload_bytes: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_BYTES", 4 * GIB))
    max_duration_us: int = field(default_factory=lambda: _env_int("MAX_DURATION_US", 3600 * 1_000_000))
    max_pixels: int = field(default_factory=lambda: _env_int("MAX_PIXELS", 3840 * 2160))
    disk_free_factor: float = 3.0  # 원본 + 재생용 사본 + 여유를 위해 영상 크기의 몇 배가 비어 있어야 하는지

    # 원본·재생용 사본 보관 기간(15). 7일은 임시 값이며 0이면 자동 삭제하지 않는다.
    retention_days: int = field(default_factory=lambda: _env_int("RETENTION_DAYS", 7))

    # 작업 처리(12, 임시): heartbeat 10초, lease 60초, 일시적 오류 최대 2회 재시도(처음 시도 포함 3회)
    heartbeat_seconds: float = 10.0
    lease_seconds: float = 60.0
    max_attempts: int = 3
    worker_poll_seconds: float = 1.0
    transcode_preset: str = "veryfast"
    transcode_crf: int = 23

    fixtures_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("FIXTURES_DIR", REPO_ROOT / "Notion"))
    )
    fixture_runs: dict[str, Path] | None = None

    def resolved_fixture_runs(self) -> dict[str, Path]:
        if not self.enable_fixtures:
            return {}
        return self.fixture_runs if self.fixture_runs is not None else _default_fixture_runs(self.fixtures_dir)

    def validate_runtime(self) -> None:
        if self.app_profile not in {"development", "workstation", "production"}:
            raise ValueError("APP_PROFILE must be development, workstation or production")
        if self.auth_mode not in {"dev", "session"}:
            raise ValueError("AUTH_MODE must be dev or session")
        if self.app_profile == "production":
            if self.auth_mode != "session" or not self.cookie_secure or self.enable_fixtures:
                raise ValueError("Production requires session authentication, secure cookies and disabled fixtures")
            if len(os.environ.get("MEDIA_SECRET", "")) < 32:
                raise ValueError("Production requires a persistent MEDIA_SECRET of at least 32 characters")

    # --- 경로 ---------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.sqlite3"

    @property
    def observations_dir(self) -> Path:
        """가져온 검출·추적 결과(영상 파일이 아닌 구조화된 결과)."""
        return self.data_dir / "observations"

    @property
    def poses_dir(self) -> Path:
        """가져온 팀 연구 포즈(구조화된 결과)."""
        return self.data_dir / "poses"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def playback_dir(self) -> Path:
        """업로드한 영상의 재생용 사본. 개발용 영상(media_dir)보다 먼저 찾는다."""
        return self.data_dir / "media"

    @property
    def media_dirs(self) -> list[Path]:
        return [self.playback_dir, self.media_dir]
