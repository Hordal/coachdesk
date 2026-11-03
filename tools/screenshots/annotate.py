"""화면 설명 이미지 만들기.

개발 서버가 떠 있고 capture.py로 데이터가 준비된 상태에서 실행한다.
각 화면을 다시 열어 요소의 위치를 브라우저에서 읽고, 스크린샷 위에 번호 표시를 그린 뒤 오른쪽에 번호별 설명을 붙인다.
결과: OUT/설명/NN_이름.png
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]  # 저장소 루트
OUT = ROOT / "Notion" / "화면_예시"
RAW = OUT / "원본"
DST = OUT / "설명"
DST.mkdir(parents=True, exist_ok=True)
BASE = "http://localhost:5173"
IDS = json.loads((RAW / "_ids.json").read_text(encoding="utf-8"))
A, B, R1, R2 = IDS["video_a"], IDS["video_b"], IDS["run1"], IDS["run2"]
LONG = ROOT / "_external" / "testvideos" / "derived_long_10min.mp4"
TMP = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_tmp_shots"
BAD_IMPORT = TMP / "import_wrong_video.json"

DPR = 2
FONT = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 34)
FONT_B = ImageFont.truetype(r"C:\Windows\Fonts\malgunbd.ttf", 38)
FONT_H = ImageFont.truetype(r"C:\Windows\Fonts\malgunbd.ttf", 54)
FONT_S = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 28)
FONT_N = ImageFont.truetype(r"C:\Windows\Fonts\malgunbd.ttf", 34)
RED = (225, 29, 72)
INK = (24, 28, 36)
GRAY = (95, 104, 118)
BG = (244, 245, 247)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def goto(page: Page, path: str, wait_text: str | None = None, ms: int = 800):
    page.goto(BASE + path)
    if wait_text:
        page.get_by_text(wait_text).first.wait_for(timeout=30_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


JS_BOX = "e => { const r = e.getBoundingClientRect(); return {x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height}; }"


def box_of(loc):
    return loc.first.evaluate(JS_BOX)


def union(*boxes):
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def heading_section(page: Page, name: str, level: int = 3):
    """제목과 그 바로 뒤 요소를 한 덩어리로 본다."""
    h = page.get_by_role("heading", name=name, level=level).first
    nxt = h.locator("xpath=following-sibling::*[1]")
    return union(box_of(h), box_of(nxt))


def wrap(draw, text, font, width):
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if draw.textlength(trial, font=font) > width and cur and ch not in ".,)”’":
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def compose(png: Path, boxes: list[dict], items: list[tuple[str, str]], title: str, route: str, out: Path, crop: tuple[float, float] | None = None):
    img = Image.open(png).convert("RGB")
    oy = 0
    if crop:
        top = max(int(crop[0] * DPR) - 24 * DPR, 0)
        bot = min(int(crop[1] * DPR) + 24 * DPR, img.height)
        img = img.crop((0, top, img.width, bot))
        oy = top
    d = ImageDraw.Draw(img)
    # 번호 표시
    for i, b in enumerate(boxes, 1):
        if b is None:
            continue
        x0, y0 = int(b["x"] * DPR) - 8, int(b["y"] * DPR) - oy - 8
        x1, y1 = int((b["x"] + b["w"]) * DPR) + 8, int((b["y"] + b["h"]) * DPR) - oy + 8
        d.rounded_rectangle((x0, y0, x1, y1), radius=14, outline=RED, width=6)
        cx, cy, r = max(x0, 34), max(y0, 34), 30
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED, outline=(255, 255, 255), width=4)
        s = str(i)
        d.text((cx, cy), s, font=FONT_N, fill=(255, 255, 255), anchor="mm")
    # 설명 패널
    pw, pad = 1000, 48
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    blocks = []
    h_total = pad
    head_lines = wrap(tmp, title, FONT_H, pw - 2 * pad)
    h_total += len(head_lines) * 70 + 6
    h_total += 44 + 30
    for head, desc in items:
        dl = wrap(tmp, desc, FONT, pw - 2 * pad - 90)
        blocks.append((head, dl))
        h_total += 52 + len(dl) * 46 + 34
    h_total += pad
    H = max(img.height, h_total)
    canvas = Image.new("RGB", (img.width + pw, H), BG)
    canvas.paste(img, (0, 0))
    c = ImageDraw.Draw(canvas)
    px = img.width
    c.rectangle((px, 0, px + pw, H), fill=(255, 255, 255))
    c.line((px, 0, px, H), fill=(210, 214, 222), width=3)
    y = pad
    for ln in head_lines:
        c.text((px + pad, y), ln, font=FONT_H, fill=INK)
        y += 70
    y += 6
    c.text((px + pad, y), route, font=FONT_S, fill=GRAY)
    y += 44 + 30
    for i, (head, dl) in enumerate(blocks, 1):
        r = 30
        cy = y + 26
        c.ellipse((px + pad, cy - r, px + pad + 2 * r, cy + r), fill=RED)
        c.text((px + pad + r, cy), str(i), font=FONT_N, fill=(255, 255, 255), anchor="mm")
        c.text((px + pad + 90, y), head, font=FONT_B, fill=INK)
        y += 52
        for ln in dl:
            c.text((px + pad + 90, y), ln, font=FONT, fill=(52, 58, 70))
            y += 46
        y += 34
    scale = min(1.0, 2400 / canvas.width)
    if scale < 1:
        canvas = canvas.resize((int(canvas.width * scale), int(canvas.height * scale)), Image.LANCZOS)
    canvas.save(out, optimize=True)
    log("설명 이미지", out.name, canvas.size)


def make(page: Page, name: str, title: str, route: str, specs: list[tuple[str, str, object]], full: bool = True, crop=None):
    """specs: (제목, 설명, page→box 함수)."""
    png = DST / f"_{name}.png"
    page.screenshot(path=str(png), full_page=full)
    boxes, items = [], []
    for head, desc, fn in specs:
        try:
            b = fn(page)
        except Exception as e:  # 요소를 못 찾으면 번호만 빼고 설명은 남긴다
            log("  요소 없음:", head, str(e)[:60])
            b = None
        boxes.append(b)
        items.append((head, desc))
    c = None
    if crop:
        c = (crop[0](page)["y"], (lambda b: b["y"] + b["h"])(crop[1](page)))
    compose(png, boxes, items, title, route, DST / f"{name}.png", c)
    png.unlink()


def loc(fn):
    return lambda page: box_of(fn(page))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=DPR, locale="ko-KR")
        page = ctx.new_page()

        # 01 영상 목록
        goto(page, "/", "내 영상")
        make(page, "01_영상목록", "영상 목록", "/", [
            ("개발 모드 표시", "로그인 없이 쓰는 개발용 상태다. 배포에서는 로그인 화면이 먼저 나온다.", loc(lambda p: p.get_by_text("개발 모드").first)),
            ("영상 올리기", "새 영상을 올리는 화면으로 이동한다.", loc(lambda p: p.locator("main").get_by_role("link", name="영상 올리기"))),
            ("내 영상", "올린 영상의 파일 이름, 종목, 준비 상태. 이름을 누르면 처리 진행 화면으로 이동한다.", loc(lambda p: p.locator("main ul").first.locator("li").first)),
            ("가상 예제", "영상 없이 결과 화면을 보여 주는 설명용 예제다. 실제 분석 결과가 아니라는 표시가 붙는다.", loc(lambda p: p.locator("main li", has_text="가상 예제").first)),
            ("수동 입력 결과", "사람이 입력한 사건·판정으로 만든 결과. 집계와 문장은 서버가 판정에서 다시 계산한다.", loc(lambda p: p.locator("main li", has_text="수동 입력").first)),
            ("분석 전", "영상 준비만 끝난 실행. 분석 단계는 아직 없어서 영상 전체 재생만 가능하다.", loc(lambda p: p.locator("main li", has_text="분석 전").first)),
        ])

        # 02 영상 올리기: 파일 선택, 진행
        goto(page, "/upload")
        page.set_input_files("input[type=file]", str(LONG))
        page.wait_for_timeout(500)
        make(page, "02_영상올리기_선택", "영상 올리기: 파일 선택", "/upload", [
            ("영상 파일", "mp4·mov 파일을 고른다. 올리다 끊기면 같은 파일을 다시 고르면 이어서 올린다.", loc(lambda p: p.locator("input[type=file]"))),
            ("종목", "복싱, 킥복싱, 무에타이 중 선택한다.", loc(lambda p: p.get_by_role("combobox", name="종목"))),
            ("올리기", "파일을 조각으로 나눠 보낸다. 조각마다 해시를 확인하고 서버 오류는 자동으로 다시 시도한다.", loc(lambda p: p.get_by_role("button", name="올리기"))),
        ], full=False)
        page.get_by_role("button", name="올리기").click()
        page.get_by_text("조각").first.wait_for(timeout=30_000)
        page.wait_for_timeout(2500)
        make(page, "03_영상올리기_진행", "영상 올리기: 진행 중", "/upload", [
            ("진행률", "전체 진행률을 보여 준다. 완료 예정 시간은 근거가 없어 표시하지 않는다.", loc(lambda p: p.get_by_role("progressbar").first)),
            ("조각 진행", "올린 용량과 전체 조각 중 몇 번째인지 보여 준다.", loc(lambda p: p.get_by_text("조각").first)),
            ("취소", "올리기를 멈추고 임시 조각을 지운다.", loc(lambda p: p.get_by_role("button", name="취소"))),
        ], full=False)
        page.get_by_role("button", name="취소").click()
        page.wait_for_timeout(1000)

        # 04 영상 준비
        goto(page, f"/videos/{A}", "상태:")
        make(page, "04_영상준비", "영상 준비 결과", f"/videos/:영상번호", [
            ("영상 정보", "파일 이름, 종목, 용량, 길이, 해상도.", loc(lambda p: p.locator("main p").first)),
            ("상태", "처리 중, 준비됨, 실패 중 하나. 처리 중에는 1.5초마다 자동으로 갱신한다.", loc(lambda p: p.locator("main p", has_text="상태:").first)),
            ("전체 진행률", "단계별 가중치로 계산한 비율이다.", loc(lambda p: p.get_by_role("progressbar").first)),
            ("처리 단계", "전체 읽기 검사, 재생용 사본 만들기, 프레임 시각 확인, 결과 등록 순서로 처리한다. 실패하면 안전한 사유와 영상 시각, 확인 번호만 보여 준다.", loc(lambda p: p.get_by_role("table", name="처리 단계"))),
            ("규격 안내", "프레임 수, 프레임률, 원본과 시각 대응을 확인하고 기본 지원(720p·30fps 이상)과 권장(1080p·60fps) 규격 충족 여부를 알린다. 규격 미만이어도 거절하지 않는다.", loc(lambda p: p.get_by_role("row", name=re.compile("프레임 시각 확인")))),
            ("분석 단계", "분석(선수 검출·추적·포즈·판정)은 아직 구현되지 않아 ‘미지원’으로 표시한다.", loc(lambda p: p.get_by_role("row", name=re.compile("분석\\(선수")))),
            ("이동 링크", "영상 보기, 결과 화면, 사건·판정 가져오기, 검출·추적 결과 가져오기로 이동한다.", loc(lambda p: p.locator("main p", has=p.get_by_role("link", name="영상 보기")))),
            ("삭제", "원본만 삭제하거나 영상과 결과를 모두 삭제한다. 원본은 7일 뒤 자동으로 지워진다.", loc(lambda p: p.get_by_role("region", name="삭제"))),
        ])

        # 05 검출·추적 가져오기 / 후보 트랙
        goto(page, f"/videos/{A}/observations", "후보 트랙")
        make(page, "05_검출추적_가져오기", "검출·추적 결과 가져오기", "/videos/:영상번호/observations", [
            ("결과 파일 3개", "metadata.json, tracking_frames.jsonl, detection_frames.jsonl(선택)을 고른다. 영상의 프레임 수와 모든 프레임 시각이 맞지 않으면 아무것도 저장하지 않는다.", loc(lambda p: p.locator(".card").first)),
            ("가져온 결과 요약", "프레임, 트랙, 검출 수와 선수 지정 상태.", loc(lambda p: p.get_by_text("프레임 407개").first)),
            ("트랙은 선수가 아님", "추적기가 붙인 임시 번호일 뿐이며 어느 트랙이 선수인지는 사용자가 직접 정한다.", loc(lambda p: p.get_by_text("트랙은 추적기가 붙인 임시 번호").first)),
            ("후보 트랙 진단", "트랙별 보이는 구간, 프레임 수, 끊긴 프레임, 가장 긴 공백, 화면 면적. 후보를 고를 때 참고하는 값이다.", loc(lambda p: p.locator("table").first)),
        ], crop=(lambda p: box_of(p.locator(".card").first), lambda p: box_of(p.locator("table").first)))

        # 06 선수 지정
        make(page, "06_선수지정", "선수 지정", "/videos/:영상번호/observations", [
            ("후보 프레임", "트랙 상자와 번호를 영상 위에 겹쳐 보여 준다. 고른 트랙은 ‘나’·‘상대’ 표시와 상자 색·점선으로 구분한다.", loc(lambda p: p.locator(".designation-frames"))),
            ("지정 상태", "선택 전 또는 지정됨.", loc(lambda p: p.locator("p", has_text="지정 상태:").first)),
            ("트랙별 선택", "트랙마다 나, 상대, 지정 안 함을 고른다. 한 선수에 트랙을 여러 개 연결할 수 있고, 같은 선수의 트랙이 동시에 보이면 거절한다.", loc(lambda p: p.locator("select[aria-label='트랙 1']"))),
            ("나와 상대 바꾸기", "고른 역할을 서로 바꾼다.", loc(lambda p: p.get_by_role("button", name="나와 상대 바꾸기"))),
            ("확정", "나와 상대를 하나 이상씩 고르면 확정할 수 있다. 미리 정해 두지 않으며 이전 지정은 이력으로 남는다.", loc(lambda p: p.get_by_role("button", name="이 선택으로 확정"))),
            ("확정 결과", "확정된 선수와 트랙을 표시한다.", loc(lambda p: p.get_by_text("확정됨").first)),
        ], crop=(lambda p: box_of(p.get_by_role("heading", name="선수 지정")), lambda p: box_of(p.get_by_text("확정됨").first)))

        # 07 사건·판정 가져오기 오류
        goto(page, f"/videos/{A}/import")
        page.set_input_files("input[type=file]", str(BAD_IMPORT))
        page.get_by_role("button", name="가져오기").click()
        page.get_by_text("데이터 계약에 맞지 않음").first.wait_for(timeout=30_000)
        page.wait_for_timeout(800)
        make(page, "07_사건판정_가져오기_오류", "사건·판정 가져오기: 검증 실패", "/videos/:영상번호/import", [
            ("입력 파일", "사람이 입력한 사건·판정 JSON. 영상 번호, 길이, 종목은 서버가 채우고 집계의 분자·분모는 서버가 판정에서 계산한다.", loc(lambda p: p.locator("input[type=file]"))),
            ("가져오기", "서버로 보내 검증한다. 통과하면 새 실행이 만들어지고 이전 실행은 그대로 남는다.", loc(lambda p: p.get_by_role("button", name="가져오기"))),
            ("검증 실패 안내", "형식, 참조, 시간 범위, 판정, 집계, 영상과의 일치를 검사한다. 하나라도 어긋나면 아무것도 저장하지 않는다.", loc(lambda p: p.get_by_text("데이터 계약에 맞지 않음").first)),
            ("오류 목록", "어긋난 항목을 최대 20건까지 그대로 보여 준다.", loc(lambda p: p.locator("main ul").first)),
            ("확인 번호", "문의할 때 알려 주는 번호. 내부 경로나 FFmpeg 출력은 보여 주지 않는다.", loc(lambda p: p.get_by_text("확인 번호").first)),
        ], full=False)

        # 08 결과 요약
        goto(page, f"/runs/{R2}", "확인된 발차기")
        make(page, "08_결과요약", "결과 요약", "/runs/:실행번호", [
            ("결과 제목", "파일 이름, 종목, 길이, 상태.", loc(lambda p: p.get_by_role("heading", level=1))),
            ("정정 안내", "정정으로 만들어진 결과인지, 이후 정정되어 최신이 아닌지 알리고 이전·최신 결과로 이동하는 링크를 준다.", loc(lambda p: p.locator(".notice", has_text="정정").first)),
            ("결과 출처 안내", "가상 예제, 수동 입력 등 결과의 출처와 전문가 검수 여부를 항상 표시한다.", loc(lambda p: p.locator(".notice", has_text="사람이 입력한").first)),
            ("선수", "A 사용자, B 상대. 정정에서 바꿀 수 있다.", loc(lambda p: p.locator(".chips"))),
            ("화면 탭", "요약, 장면 복기, 정정·기록.", loc(lambda p: p.locator(".tabs"))),
            ("집계 문장", "서버가 템플릿으로 만든 문장이다. 분모와 판단 불가 건수를 함께 말하고 ‘제공’ 배지와 근거 집계로 이어진다.", loc(lambda p: p.locator(".finding").first)),
            ("근거 구간", "누르면 장면 복기로 이동한다. ‘재생 가능’은 원본 영상이 남아 있다는 뜻이다.", loc(lambda p: p.locator(".evidence-link").first)),
            ("보류된 주장", "근거가 부족하면 숨기지 않고 ‘제공하지 않음’과 사유를 표시한다. 교정이 없다는 것은 문제가 없다는 뜻이 아니다.", loc(lambda p: p.locator(".finding--withheld").first)),
            ("근거 장면 목록", "이 결과가 사용한 모든 근거 구간.", loc(lambda p: p.locator(".evidence-list").last)),
        ])

        # 09 집계 상세
        goto(page, f"/runs/{R2}/aggregates/AG01", "집계")
        make(page, "09_집계상세", "집계 상세", "/runs/:실행번호/aggregates/:집계번호", [
            ("비율", "판정 가능한 건 중 해당 결과가 발생한 건. 분모가 0이면 비율을 만들지 않고 ‘비율 없음’으로 표시한다.", loc(lambda p: p.locator(".count-breakdown__ratio"))),
            ("구성 막대", "발생, 발생하지 않음, 판단 불가를 색과 무늬로 구분한다. 색만으로 구별하지 않는다.", loc(lambda p: p.locator(".stackbar"))),
            ("건수 목록", "확인된 전체, 판정 가능, 발생, 판단 불가, 제외를 모두 보여 준다.", loc(lambda p: p.locator(".count-list"))),
            ("구성 사건", "각 건수에 해당하는 사건과 판정. 사건 이름을 누르면 사건 상세로 이동한다.", loc(lambda p: p.locator(".member-list").first)),
            ("판단 불가", "가림 등으로 판정하지 못한 사건. 0회나 실패로 바꾸지 않고 사유를 보여 준다.", loc(lambda p: p.locator(".member-list").nth(2))),
        ])

        # 10 사건 상세
        goto(page, f"/runs/{R2}/events/E02", "사건")
        make(page, "10_사건상세", "사건 상세", "/runs/:실행번호/events/:사건번호", [
            ("사건 정보", "사건 이름, 시각, 수행 선수와 대상 선수.", loc(lambda p: p.get_by_role("heading", level=2).first)),
            ("판정", "접촉 여부, 부위 등 질문별 판정. 판정은 사건과 따로 기록한다.", lambda p: heading_section(p, "판정")),
            ("관계", "다른 사건과의 관계(반격, 접촉 사건)와 그 근거.", lambda p: heading_section(p, "관계")),
            ("공방", "이 사건이 속한 공방과 시각.", lambda p: heading_section(p, "공방")),
            ("근거 장면", "누르면 그 구간의 장면 복기로 이동한다.", lambda p: heading_section(p, "근거 장면")),
        ])

        # 11 장면 복기(실제 영상)
        goto(page, f"/runs/{R2}/scene?from_us=69000000&to_us=74000000", None, 3500)
        make(page, "11_장면복기", "장면 복기: 실제 영상", "/runs/:실행번호/scene", [
            ("구간 선택", "근거 구간을 고르거나 ‘전체 길이’로 영상 전체를 본다.", loc(lambda p: p.locator(".range-picker"))),
            ("영상", "서명된 주소로만 재생되는 재생용 사본. 구간 끝에서 멈춘다.", loc(lambda p: p.locator("video").first)),
            ("프레임 이동", "이전·다음 프레임은 실제 프레임 시각 표를 따른다. 브라우저가 프레임을 건너뛰면 디코더가 식별한 정지 프레임으로 자동 전환한다.", loc(lambda p: p.locator(".player__controls"))),
            ("표시 프레임 정보", "지금 표시된 프레임 번호와 시각, 시각을 확인한 방식, 영상 형식, 관절 관측 여부.", loc(lambda p: p.locator(".player__readout"))),
            ("타임라인", "선수별 행동·접촉 구간. 누르면 그 시각의 첫 프레임으로 이동한다. 빈 구간은 ‘기록 없음’이며 ‘없음’이 아니다.", loc(lambda p: p.locator(".timeline"))),
            ("이 구간의 판정", "구간 안 사건의 판정과 사유. 판단 불가는 점선 배지로 구별한다.", loc(lambda p: p.locator(".member-list").first)),
        ])

        # 12 장면 복기(관절 오버레이, 가상 예제)
        goto(page, "/runs/fixture-counter/scene?from_us=9000000&to_us=14000000", None, 3500)
        make(page, "12_장면복기_관절", "장면 복기: 관절 오버레이(가상 예제)", "/runs/fixture-counter/scene", [
            ("가상 예제 표시", "영상 없이 만든 설명용 예제임을 알린다.", loc(lambda p: p.locator(".notice").first)),
            ("관절 오버레이", "서버가 표시 좌표로 되돌려 준 관절을 영상과 같은 좌표계로 그린다. 좌표가 없는 관절은 그리지 않는다.", loc(lambda p: p.locator(".player").first)),
            ("관절 관측", "선수별 관절 데이터가 있는지 표시한다.", loc(lambda p: p.locator(".player__readout"))),
            ("접촉 타임라인", "행동과 접촉을 같은 시간 기준으로 표시한다. 영상, 관절, 타임라인이 같은 프레임 시각을 쓴다.", loc(lambda p: p.locator(".timeline"))),
        ])

        # 13 정정·기록
        goto(page, f"/runs/{R2}/corrections", "본인 지정")
        page.locator("input[name=me]").nth(0).click()
        page.get_by_text("적용 내용 미리 보기").wait_for(timeout=15_000)
        page.wait_for_timeout(500)
        make(page, "13_정정기록", "정정·기록", "/runs/:실행번호/corrections", [
            ("본인 지정", "본인인 선수를 바꾼다. 정정하면 새 결과가 만들어지고 이전 결과는 그대로 남는다.", loc(lambda p: p.get_by_role("group", name="본인인 선수"))),
            ("미리 보기", "바뀌는 역할과 재계산 영향을 확인한다. 집계·판정·문장은 선수 번호 기준이라 바뀌지 않는다.", loc(lambda p: p.get_by_text("적용 내용 미리 보기").first.locator("xpath=.."))),
            ("정정 확정", "미리 보기를 확인하면 활성화된다.", loc(lambda p: p.get_by_role("button", name="이 내용으로 정정"))),
            ("정정 이력", "최초 결과부터 최신 결과까지. 각 결과로 이동할 수 있다.", loc(lambda p: p.get_by_role("region", name="정정 이력"))),
            ("오류 신고", "위치, 기술, 수치, 근거 불일치 등을 신고한다. 접수만 하며 결과를 바꾸지 않는다.", loc(lambda p: p.get_by_role("region", name="분석 오류 신고"))),
            ("의도 설명", "선수의 의도를 직접 적는다. AI 관측과 섞지 않고 따로 보관한다.", loc(lambda p: p.get_by_role("region", name="의도 설명"))),
        ])

        # 14 삭제 확인
        goto(page, f"/videos/{A}", "상태:")
        page.get_by_role("button", name="영상과 결과 모두 삭제").click()
        page.wait_for_timeout(600)
        make(page, "14_삭제_확인", "삭제: 확인 단계", "/videos/:영상번호", [
            ("삭제 영역", "‘원본 영상만 삭제’(원본과 재생용 사본만 삭제, 요약과 수치는 남고 근거 영상은 삭제됨으로 표시)와 ‘영상과 결과 모두 삭제’ 중 고르면 확인 단계로 바뀐다.", loc(lambda p: p.get_by_role("region", name="삭제"))),
            ("확인 문구", "무엇이 지워지는지와 되돌릴 수 없다는 안내를 보여 준다.", loc(lambda p: p.get_by_text("되돌릴 수 없습니다").first)),
            ("삭제", "눌러야 실행된다. 즉시 조회가 막히고, 정리가 끝나기 전에는 ‘삭제 완료’라고 하지 않고 정리 중으로 안내한다.", loc(lambda p: p.get_by_role("button", name="삭제", exact=True))),
            ("취소", "아무것도 지우지 않고 돌아간다.", loc(lambda p: p.get_by_role("button", name="취소", exact=True))),
        ], crop=(lambda p: box_of(p.get_by_role("region", name="삭제")), lambda p: box_of(p.get_by_role("region", name="삭제"))))
        page.get_by_role("button", name="취소").click()
        browser.close()
    log("완료")


if __name__ == "__main__":
    main()
