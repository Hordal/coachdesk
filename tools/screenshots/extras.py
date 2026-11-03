"""화면 흐름도와 휴대폰 화면 모음 이미지(capture.py의 원본 스크린샷을 사용)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[2] / "Notion" / "화면_예시"
RAW = OUT / "원본"
DST = OUT / "설명"
F = lambda size, bold=False: ImageFont.truetype(r"C:\Windows\Fonts\malgunbd.ttf" if bold else r"C:\Windows\Fonts\malgun.ttf", size)
INK, GRAY, RED, BG = (24, 28, 36), (95, 104, 118), (225, 29, 72), (244, 245, 247)


def thumb(name, w, crop_h, dpr=2, left=0):
    im = Image.open(RAW / f"{name}.png").convert("RGB")
    im = im.crop((left, 0, im.width, min(crop_h * dpr, im.height)))
    h = int(im.height * w / im.width)
    return im.resize((w, h), Image.LANCZOS)


def arrow(d, p0, p1, color=GRAY, w=8):
    d.line((p0, p1), fill=color, width=w)
    x0, y0 = p0
    x1, y1 = p1
    if x1 != x0:
        s = 1 if x1 > x0 else -1
        d.polygon([(x1, y1), (x1 - s * 26, y1 - 16), (x1 - s * 26, y1 + 16)], fill=color)
    else:
        s = 1 if y1 > y0 else -1
        d.polygon([(x1, y1), (x1 - 16, y1 - s * 26), (x1 + 16, y1 - s * 26)], fill=color)


def flow():
    steps = [
        ("영상 올리기", "파일을 고르면 조각으로 나눠 올린다", "s02_영상올리기_선택", 740),
        ("영상 준비", "읽기 검사, 재생용 사본, 프레임 시각 표", "s05_영상준비_완료", 1000),
        ("검출·추적 가져오기", "파이프라인 출력을 프레임 시각과 대조", "s07_후보트랙_진단", 800),
        ("선수 지정", "사용자가 두 선수의 트랙을 직접 선택", "s09_선수지정_확정", 800),
        ("사건·판정 가져오기", "사람이 입력한 사건·판정을 가져와 새 실행 생성", "s10_사건판정_가져오기", 740),
        ("결과 요약", "집계 문장, 근거 구간, 보류 표시", "s12_결과요약", 900),
        ("장면 복기", "근거 구간의 영상, 타임라인, 판정", "s15_장면복기_실제영상", 1100),
        ("정정·신고·삭제", "본인 지정 정정, 오류 신고, 삭제", "s18_정정_이력", 800),
    ]
    tw, card_w, card_h, gap, margin = 760, 800, 610, 90, 70
    W = 4 * card_w + 3 * gap + 2 * margin
    H = 230 + 2 * card_h + 90 + 130
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((margin, 40), "화면 흐름", font=F(70, True), fill=INK)
    d.text((margin, 128), "영상을 올려 결과를 보고 정정·삭제하기까지. ‘검출·추적 가져오기 → 선수 지정’과 ‘사건·판정 가져오기’는 서로 독립이다.", font=F(34), fill=GRAY)
    pos = []
    for i, (title, cap, name, crop_h) in enumerate(steps):
        r, c = divmod(i, 4)
        x = margin + c * (card_w + gap)
        y = 230 + r * (card_h + 90)
        pos.append((x, y))
        d.rounded_rectangle((x, y, x + card_w, y + card_h), radius=22, fill=(255, 255, 255), outline=(208, 213, 222), width=3)
        d.ellipse((x + 24, y + 22, x + 24 + 60, y + 22 + 60), fill=RED)
        d.text((x + 54, y + 52), str(i + 1), font=F(36, True), fill=(255, 255, 255), anchor="mm")
        d.text((x + 104, y + 28), title, font=F(42, True), fill=INK)
        t = thumb(name, tw, crop_h)
        t = t.crop((0, 0, tw, 420))
        img.paste(t, (x + 20, y + 100))
        d.rectangle((x + 20, y + 100, x + 20 + tw, y + 100 + 420), outline=(220, 224, 232), width=2)
        d.text((x + 24, y + 540), cap, font=F(30), fill=GRAY)
    # 화살표
    for r in range(2):
        for c in range(3):
            x = pos[r * 4 + c][0] + card_w + 12
            y = pos[r * 4 + c][1] + card_h // 2
            arrow(d, (x, y), (x + gap - 24, y))
    # 4 → 5 꺾어서 연결
    x4, y4 = pos[3][0] + card_w // 2, pos[3][1] + card_h + 8
    x5, y5 = pos[4][0] + card_w // 2, pos[4][1] - 8
    ym = (y4 + y5) // 2
    d.line((x4, y4, x4, ym), fill=GRAY, width=8)
    d.line((x4, ym, x5, ym), fill=GRAY, width=8)
    arrow(d, (x5, ym), (x5, y5))
    d.text((margin, H - 60), "분석 단계(포즈·상태·행동·결과 판정)는 아직 구현되지 않아 지정한 선수는 아직 분석에 연결되지 않는다.", font=F(30), fill=GRAY)
    s = 2400 / img.width
    img = img.resize((2400, int(img.height * s)), Image.LANCZOS)
    img.save(DST / "00_화면_흐름도.png", optimize=True)
    print("흐름도", img.size)


def phone():
    shots = [("목록", "m01_영상목록"), ("결과 요약", "m02_결과요약"), ("장면 복기", "m03_장면복기")]
    pw, ph, gap, margin = 720, 1560, 90, 80
    W = 3 * pw + 2 * gap + 2 * margin
    H = ph + 330
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((margin, 40), "휴대폰 너비(390px)", font=F(70, True), fill=INK)
    d.text((margin, 128), "모든 화면이 휴대폰 너비에서도 쓸 수 있게 만들어져 있다. 체육관에서 휴대폰이나 태블릿으로 열람하는 경우를 위한 것이다.", font=F(34), fill=GRAY)
    for i, (label, name) in enumerate(shots):
        x = margin + i * (pw + gap)
        y = 230
        d.rounded_rectangle((x - 14, y - 14, x + pw + 14, y + ph + 14), radius=56, fill=(30, 34, 44))
        im = Image.open(RAW / f"{name}.png").convert("RGB")
        im = im.crop((0, 0, im.width, min(im.height, int(im.width * ph / pw))))
        im = im.resize((pw, int(im.height * pw / im.width)), Image.LANCZOS)
        mask = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw, ph), radius=44, fill=255)
        canvas = Image.new("RGB", (pw, ph), (255, 255, 255))
        canvas.paste(im, (0, 0))
        img.paste(canvas, (x, y), mask)
        d.text((x + pw // 2, y + ph + 60), label, font=F(40, True), fill=INK, anchor="mm")
    s = 2400 / img.width
    img = img.resize((2400, int(img.height * s)), Image.LANCZOS)
    img.save(DST / "15_휴대폰_화면.png", optimize=True)
    print("휴대폰", img.size)


if __name__ == "__main__":
    flow()
    phone()
