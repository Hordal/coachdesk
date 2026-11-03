"""웹앱 화면 스크린샷 촬영.

개발 서버(API 8000, 화면 5173, 워커)가 떠 있고 AUTH_MODE=dev인 빈 DATA_DIR에서 실행한다.
시험 영상을 올리고 검출·추적 결과 가져오기, 선수 지정, 사건·판정 가져오기, 정정을 화면에서 직접 하면서 촬영한다.
결과는 OUT/원본/*.png, 설명 이미지용 요소 위치는 OUT/원본/*.json으로 저장한다(annotate.py가 사용).

설치: pip install playwright pillow httpx  (브라우저는 설치된 Chrome을 쓴다)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]  # 저장소 루트
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "Notion" / "화면_예시"
RAW = OUT / "원본"
BASE = "http://localhost:5173"
API = "http://127.0.0.1:8000/api/v1"

SPARRING = ROOT / "_external" / "MMACoachAgent" / "data" / "uploads" / "boxing_sparring_01.mp4"
RESULT = ROOT / "_external" / "MMACoachAgent" / "data" / "results" / "analysis_0760bccb963f"
LONG = ROOT / "_external" / "testvideos" / "derived_long_10min.mp4"
TMP = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_tmp_shots"
TMP.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wait_idle(page: Page, ms: int = 600):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def goto(page: Page, path: str, text: str | None = None, ms: int = 600):
    page.goto(BASE + path)
    if text:
        page.get_by_text(text).first.wait_for(timeout=30_000)
    wait_idle(page, ms)


def shot(page: Page, name: str, full: bool = True, boxes: dict[str, str] | None = None):
    """스크린샷을 저장한다. boxes는 {번호 또는 이름: CSS/텍스트 선택자}이며 해당 요소의 위치를 JSON으로 남긴다."""
    path = RAW / f"{name}.png"
    page.screenshot(path=str(path), full_page=full)
    info = {"image": path.name, "viewport": page.viewport_size, "boxes": {}}
    for key, selector in (boxes or {}).items():
        loc = page.locator(selector).first
        try:
            b = loc.bounding_box(timeout=3000)
        except Exception:
            b = None
        info["boxes"][key] = b
    (RAW / f"{name}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    log("촬영", name)


def wait_ready(video_id: str, timeout: int = 600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = httpx.get(f"{API}/videos/{video_id}/summary").json()
        if s["status"] != "processing":
            return s
        time.sleep(2)
    raise TimeoutError(video_id)


def api_upload(path: Path, sport: str = "boxing") -> str:
    import hashlib
    import uuid

    c = httpx.Client(timeout=600)
    size = path.stat().st_size
    up = c.post(f"{API}/uploads", json={"filename": path.name, "size_bytes": size, "sport": sport}, headers={"Idempotency-Key": uuid.uuid4().hex}).json()
    with path.open("rb") as f:
        for n in range(up["part_count"]):
            data = f.read(up["part_size"])
            r = c.put(f"{API}/uploads/{up['upload_id']}/parts/{n}", content=data, headers={"X-Part-Sha256": hashlib.sha256(data).hexdigest()})
            assert r.status_code == 200, r.text
    done = c.post(f"{API}/uploads/{up['upload_id']}/complete").json()
    return done["video_id"]


def make_manual_import(video_id: str, out: Path) -> Path:
    from tests.test_manual_import import payload

    out.write_text(json.dumps(payload(video_id), ensure_ascii=False), encoding="utf-8")
    return out


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=2, locale="ko-KR")
        page = ctx.new_page()

        # ---- 영상 준비: 짧은 스파링 영상(API로 올려 둔다) ----
        log("스파링 영상 업로드")
        vid_a = api_upload(SPARRING)
        wait_ready(vid_a)
        log("영상 A", vid_a)

        # ---- 검출·추적 결과 가져오기 ----
        goto(page, f"/videos/{vid_a}/observations")
        page.set_input_files("input[type=file] >> nth=0", str(RESULT / "metadata.json"))
        page.set_input_files("input[type=file] >> nth=1", str(RESULT / "tracking_frames.jsonl"))
        page.set_input_files("input[type=file] >> nth=2", str(RESULT / "detection_frames.jsonl"))
        wait_idle(page)
        shot(page, "s06_검출추적_가져오기", full=False)
        page.get_by_role("button", name="가져오기").click()
        page.get_by_text("후보 트랙").first.wait_for(timeout=30_000)
        wait_idle(page, 1500)
        shot(page, "s07_후보트랙_진단")

        # ---- 선수 지정 ----
        page.get_by_role("heading", name="선수 지정").scroll_into_view_if_needed()
        wait_idle(page, 800)
        shot(page, "s08_선수지정", full=True)
        page.locator('select[aria-label="트랙 1"]').select_option("user")
        page.locator('select[aria-label="트랙 0"]').select_option("opponent")
        wait_idle(page, 300)
        page.get_by_role("button", name="이 선택으로 확정").click()
        page.get_by_text("확정됨").first.wait_for(timeout=15_000)
        wait_idle(page, 800)
        shot(page, "s09_선수지정_확정")

        # ---- 영상 올리기(긴 영상은 화면에서 직접) ----
        goto(page, "/upload")
        page.set_input_files("input[type=file]", str(LONG))
        wait_idle(page, 300)
        shot(page, "s02_영상올리기_선택", full=False)
        page.get_by_role("button", name="올리기").click()
        page.get_by_text("%").first.wait_for(timeout=30_000)
        page.wait_for_timeout(2500)
        shot(page, "s03_영상올리기_진행", full=False)
        page.wait_for_url("**/videos/*", timeout=180_000)
        vid_b = page.url.rsplit("/", 1)[-1]
        page.wait_for_timeout(15_000)
        shot(page, "s04_영상준비_진행")
        wait_ready(vid_b)
        goto(page, f"/videos/{vid_b}", "상태: 준비됨")
        shot(page, "s05_영상준비_완료")

        # ---- 사건·판정 가져오기: 오류와 성공 ----
        bad = make_manual_import(vid_a, TMP / "import_wrong_video.json")
        goto(page, f"/videos/{vid_a}/import")
        page.set_input_files("input[type=file]", str(bad))
        page.get_by_role("button", name="가져오기").click()
        page.get_by_text("데이터 계약에 맞지 않음").first.wait_for(timeout=30_000)
        wait_idle(page, 600)
        shot(page, "s11_사건판정_오류")

        good = make_manual_import(vid_b, TMP / "import_ok.json")
        goto(page, f"/videos/{vid_b}/import")
        page.set_input_files("input[type=file]", str(good))
        wait_idle(page, 300)
        shot(page, "s10_사건판정_가져오기", full=False)
        page.get_by_role("button", name="가져오기").click()
        page.wait_for_url("**/runs/*", timeout=60_000)
        run1 = page.url.rsplit("/", 1)[-1]
        page.get_by_text("확인된 발차기").first.wait_for(timeout=30_000)
        wait_idle(page, 1000)
        shot(page, "s12_결과요약")
        log("실행 1", run1)

        # ---- 집계·사건 상세 ----
        goto(page, f"/runs/{run1}/aggregates/AG01", "집계")
        shot(page, "s13_집계상세")
        goto(page, f"/runs/{run1}/events/E02", "사건")
        shot(page, "s14_사건상세")

        # ---- 장면 복기: 실제 영상, 가상 관절 영상 ----
        goto(page, f"/runs/{run1}/scene?from_us=69000000&to_us=74000000", None, 3500)
        shot(page, "s15_장면복기_실제영상")
        goto(page, "/runs/fixture-counter/scene?from_us=9000000&to_us=14000000", None, 3500)
        shot(page, "s16_장면복기_관절")

        # ---- 정정 ----
        goto(page, f"/runs/{run1}/corrections", "본인 지정")
        shot(page, "s17_정정")
        page.locator("input[name=me]").nth(1).click()
        page.get_by_text("적용 내용 미리 보기").wait_for(timeout=15_000)
        shot(page, "s17b_정정_미리보기")
        page.get_by_role("button", name="이 내용으로 정정").click()
        page.wait_for_url("**/runs/*/corrections", timeout=30_000)
        page.get_by_text("정정 1").first.wait_for(timeout=15_000)
        run2 = page.url.split("/runs/")[1].split("/")[0]
        wait_idle(page, 800)
        shot(page, "s18_정정_이력")
        log("실행 2", run2)

        # ---- 삭제 영역(확인 단계까지만) ----
        goto(page, f"/videos/{vid_a}", "상태: 준비됨")
        page.get_by_role("button", name="영상과 결과 모두 삭제").click()
        wait_idle(page, 500)
        shot(page, "s19_삭제_확인")
        page.get_by_role("button", name="취소").click()

        # ---- 목록 ----
        goto(page, "/", "내 영상")
        shot(page, "s01_영상목록")

        # ---- 휴대폰 너비 ----
        mctx = browser.new_context(viewport=PHONE, device_scale_factor=3, locale="ko-KR", is_mobile=True, has_touch=True)
        m = mctx.new_page()
        goto(m, "/", "내 영상")
        shot(m, "m01_영상목록")
        goto(m, f"/runs/{run2}", "확인된 발차기")
        shot(m, "m02_결과요약")
        goto(m, f"/runs/{run2}/scene?from_us=69000000&to_us=74000000", None, 3500)
        shot(m, "m03_장면복기")
        mctx.close()

        (RAW / "_ids.json").write_text(json.dumps({"video_a": vid_a, "video_b": vid_b, "run1": run1, "run2": run2}), encoding="utf-8")
        browser.close()
    log("완료")


if __name__ == "__main__":
    main()
