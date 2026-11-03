"""첫 관리자 계정 만들기. 공개 가입이 없으므로 서버를 처음 띄울 때 한 번 실행한다.

    python -m app.auth.cli create-admin --email you@example.com [--name 이름]

비밀번호는 터미널에서 입력하거나 --password-stdin으로 표준 입력에서 읽는다. 명령줄 인자로 받지 않는다.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from ..settings import Settings
from ..storage.db import Database
from .service import AuthError, AuthService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="계정 관리")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="관리자 계정 만들기")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="관리자")
    admin.add_argument("--password-stdin", action="store_true", help="비밀번호를 표준 입력에서 읽는다")
    args = parser.parse_args(argv)

    password = sys.stdin.readline().lstrip("\ufeff").rstrip("\r\n") if args.password_stdin else getpass.getpass("비밀번호: ")
    settings = Settings()
    service = AuthService(Database(settings.db_path), settings)
    try:
        user_id = service.create_user(email=args.email, password=password, display_name=args.name, role="admin", consented=True)
    except AuthError as exc:
        print(f"실패: {exc.title}", file=sys.stderr)
        return 1
    service.audit(None, "admin_created", {"user_id": user_id})
    print("관리자 계정을 만들었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
