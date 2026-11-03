"""로그인 모드(AUTH_MODE=session) 화면 설명 이미지.

API를 session 모드의 빈 DATA_DIR로 띄우고(환경 변수 DATA_DIR 필요) 화면 개발 서버가 떠 있는 상태에서 실행한다.
첫 관리자는 이 스크립트가 CLI로 만든다. 시험용 비밀번호는 실행할 때마다 무작위로 만들며 어디에도 저장하지 않는다.
"""
from __future__ import annotations

import secrets
import subprocess
import sys

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

from annotate import DPR, box_of, goto, loc, make, union  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ADMIN_EMAIL = "admin@example.test"
MEMBER_EMAIL = "member@example.test"


def main():
    admin_pw = secrets.token_urlsafe(14)
    member_pw = secrets.token_urlsafe(14)
    r = subprocess.run(
        [sys.executable, "-m", "app.auth.cli", "create-admin", "--email", ADMIN_EMAIL, "--password-stdin"],
        input=admin_pw, text=True, cwd=str(REPO / "backend"), capture_output=True, encoding="utf-8",
    )
    print("관리자 생성:", r.returncode, r.stdout.strip()[:60])
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=DPR, locale="ko-KR")
        page = ctx.new_page()

        # 로그인: 잘못된 비밀번호를 한 번 넣어 오류 문구까지 보여 준다
        goto(page, "/", "로그인")
        page.get_by_label("이메일").fill(ADMIN_EMAIL)
        page.get_by_label("비밀번호").fill("wrong-" + secrets.token_hex(4))
        page.get_by_role("button", name="로그인").click()
        page.get_by_text("올바르지 않습니다").wait_for(timeout=15_000)
        page.wait_for_timeout(500)
        make(page, "16_로그인", "로그인", "/login", [
            ("이메일·비밀번호", "초대받은 계정으로 로그인한다. 공개 가입은 없다.", loc(lambda p: p.locator("form").first)),
            ("로그인", "15분 동안 5회(IP는 20회) 틀리면 올바른 비밀번호도 잠시 막는다. 없는 이메일도 같은 시간이 걸리고 같은 문구로 응답한다.", loc(lambda p: p.get_by_role("button", name="로그인"))),
            ("오류 문구", "이메일이 없는지 비밀번호가 틀렸는지 구별해 알려 주지 않는다.", loc(lambda p: p.get_by_text("올바르지 않습니다").first)),
            ("안내", "계정은 초대를 받은 사람만 만들 수 있다.", loc(lambda p: p.get_by_text("초대를 받은 사람만").first)),
        ], full=False)

        # 관리자: 로그인 후 초대 만들기
        page.get_by_label("비밀번호").fill(admin_pw)
        page.get_by_role("button", name="로그인").click()
        page.get_by_text("내 영상").first.wait_for(timeout=15_000)
        goto(page, "/admin", "계정 관리")
        page.get_by_label("이메일").fill(MEMBER_EMAIL)
        page.get_by_role("button", name="초대 만들기").click()
        page.get_by_text("초대 링크는 지금만 볼 수 있습니다").wait_for(timeout=15_000)
        page.wait_for_timeout(500)
        link = page.locator("main").inner_text()
        invite_path = "/invite/" + link.split("/invite/")[1].split()[0]
        make(page, "17_계정관리", "계정 관리(관리자)", "/admin", [
            ("상단 메뉴", "로그인하면 사용자 이름과 로그아웃이 나온다. 관리자에게는 ‘계정 관리’가 보인다.", loc(lambda p: p.locator("header"))),
            ("초대 만들기", "이메일과 역할(참여자·관리자)로 초대를 만든다. 유효 시간은 72시간(임시)이고 한 번만 쓸 수 있다.", loc(lambda p: p.get_by_role("button", name="초대 만들기"))),
            ("초대 링크", "만든 직후에만 보인다. 서버에는 토큰의 해시만 저장하므로 다시 볼 수 없다.", loc(lambda p: p.get_by_text("초대 링크는 지금만 볼 수 있습니다").first.locator("xpath=.."))),
            ("초대 목록", "대기 중인 초대를 폐기할 수 있다.", loc(lambda p: p.locator("main li", has_text=MEMBER_EMAIL).first)),
            ("계정 목록", "사용 중인 계정과 차단·해제. 차단하면 열려 있는 세션도 바로 끊긴다. 관리자도 다른 사람의 영상은 볼 수 없다.", loc(lambda p: p.get_by_role("heading", name="계정", exact=True).locator("xpath=following-sibling::*[1]"))),
        ], full=False)

        # 초대 수락
        page.get_by_role("button", name="로그아웃").click()
        page.get_by_text("로그인").first.wait_for(timeout=15_000)
        goto(page, invite_path, "주소로 초대되었습니다")
        page.get_by_label("이름").fill("시험 멤버")
        page.get_by_label("비밀번호(10자 이상)").fill(member_pw)
        page.get_by_role("checkbox").check()
        page.wait_for_timeout(300)
        make(page, "18_초대수락", "계정 만들기(초대 수락)", "/invite/:토큰", [
            ("초대 확인", "초대된 이메일 주소를 보여 준다. 없는 토큰, 만료, 폐기, 사용 완료는 구별하지 않고 같은 화면이 나온다.", loc(lambda p: p.get_by_text("주소로 초대되었습니다").first)),
            ("이름·비밀번호", "비밀번호는 10~128자만 검사하고 구성 규칙은 강요하지 않는다. 서버에는 argon2 해시만 저장한다.", lambda p: union(box_of(p.get_by_label("이름")), box_of(p.get_by_label("비밀번호(10자 이상)")))),
            ("이용 동의", "영상 보관 기간과 삭제 방법을 포함한 이용 안내에 동의해야 만들 수 있다. 동의 버전과 시각을 기록한다(문구는 검토 전).", loc(lambda p: p.get_by_role("checkbox").locator("xpath=.."))),
            ("계정 만들기", "동의하기 전에는 눌러지지 않는다.", loc(lambda p: p.get_by_role("button", name="계정 만들기"))),
        ], full=False)
        browser.close()
    print("완료")


if __name__ == "__main__":
    main()
