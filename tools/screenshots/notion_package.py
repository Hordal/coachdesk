"""노션 가져오기용 ZIP을 만든다(README 내용 + 화면 설명 이미지).

노션: 사이드바 가져오기(Import) → 텍스트·마크다운(Text & Markdown) → 이 ZIP 선택.
이미지는 ASCII 이름으로 바꿔 images/ 아래에 넣는다. 표 안의 이미지와 저장소 상대 링크는 노션에서 깨지므로 고쳐 쓴다.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "Notion" / "화면_예시"
OUT = SHOTS / "Notion_업로드.zip"
TITLE = "격투기 영상 복기"

# 화면_예시/README.md의 표에서 번호, 이름, 설명을 읽는다.
rows = []
for line in (SHOTS / "README.md").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^\| (\d\d)_(\S+) \| (.+) \|$", line)
    if m:
        rows.append((m.group(1), m.group(2), m.group(3)))
assert len(rows) == 19, len(rows)
files = {n: next((SHOTS / "설명").glob(f"{n}_*.png")) for n, _, _ in rows}
ascii_name = {n: f"screen_{n}.png" for n, _, _ in rows}


def img(n, alt):
    return f"![{alt}](images/{ascii_name[n]})"


flow = img("00", "화면 흐름")
md = f"""# {TITLE}

복싱·킥복싱·무에타이·MMA 경기 후 영상에서 두 선수의 공방을 근거 장면과 함께 정리하는 복기 웹앱이다. 근거가 부족하면 보류를 표시하고 0이나 추정값으로 채우지 않는다.

현재는 서버·화면(영상 올리기, 영상 준비, 결과 열람과 장면 복기, 선수 지정, 정정, 삭제, 초대 계정)이 구현되어 있다. 분석 단계(포즈·상태·행동·결과 판정)는 아직 없어서 결과는 가상 예제나 사람이 입력한 사건·판정으로 보여 준다. 구현이 끝났다는 것이 정확도 검증을 마쳤다는 뜻은 아니다.

코드와 설계 문서는 비공개 GitHub 저장소(MMA-Coach-Agent-SoftWare)에 있다.

{flow}

## 화면 미리보기

선수 지정

{img("06", "선수 지정")}

결과 요약

{img("08", "결과 요약")}

장면 복기

{img("11", "장면 복기")}

모든 화면의 설명 이미지는 아래 ‘화면 설명’에 있다.

## 구성

| 경로 | 내용 |
|---|---|
| `backend/` | 서버(Python, FastAPI, SQLite)와 영상 처리 워커. 사용법은 `backend/README.md` |
| `frontend/` | 화면(TypeScript, React, Vite). 사용법은 `frontend/README.md` |
| `tools/contract_check/` | 가상 예제의 데이터 계약 검사 도구 |
| `tools/screenshots/` | 화면 스크린샷과 설명 이미지를 만드는 스크립트 |
| `Notion/` | 설계 문서(00~33)와 가상 예제 데이터. 시작은 `00_목차.md`, 현재 상태는 `공유본/`의 문서 |

## 실행

필요한 것: Python 3.11 이상, Node.js 24, FFmpeg·FFprobe(PATH 또는 환경 변수 `FFMPEG_PATH`, `FFPROBE_PATH`). 아래는 Windows PowerShell 기준이다.

저장소 루트에서 서버 의존성을 설치한다.

```
python -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.lock.txt
```

서버와 워커를 각각 별도 터미널에서 `backend` 디렉터리로 이동해 실행한다.

```
..\\.venv\\Scripts\\python.exe -m uvicorn app.api.asgi:app --reload
```

```
..\\.venv\\Scripts\\python.exe -m app.jobs.worker
```

화면은 `frontend` 디렉터리에서 실행하고 http://localhost:5173 을 연다.

```
npm install
npm run dev
```

기본값 `AUTH_MODE=dev`는 로그인 없이 쓰는 개발·시험 전용이다. 배포할 때는 `AUTH_MODE=session`과 TLS 위의 `COOKIE_SECURE=1`로 실행해야 한다(`backend/README.md`의 인증 절).

## 시험

`backend`에서 서버 시험을 실행한다.

```
..\\.venv\\Scripts\\python.exe -m pytest
```

`frontend`에서 화면 시험과 타입 검사를 실행한다.

```
npm test
npm run typecheck
```

## 기타

- 영상, 시험 데이터(`backend/data`, `backend/dev_media`), 외부 복사본(`_external/`)은 저장소에 포함하지 않는다. 시험용 영상은 `backend/scripts/make_demo_media.py`로 만든다.
- 서버 시험이 `Notion/02A_*.json` 예제를 읽으므로 그 폴더를 옮기지 않는다.
- 스크린샷 스크립트는 Windows 글꼴(맑은 고딕)과 설치된 Chrome을 사용한다.

## 화면 설명

번호 표시는 화면의 요소와 오른쪽 설명이 서로 대응한다. 모두 개발 모드에서 실제 서버와 실제 스파링 영상으로 촬영했다. 결과 화면의 사건·판정은 설명용 가상 예제를 사람이 입력한 것으로 가져온 것이며 실제 분석 결과가 아니다.
"""
for n, name, desc in rows:
    title = name.replace("_", " ")
    md += f"\n### {n} {title}\n\n{desc}\n\n{img(n, title)}\n"

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr(f"{TITLE}.md", md)
    for n, p in files.items():
        z.write(p, f"images/{ascii_name[n]}")
print("만듦:", OUT, "%.1f MB" % (OUT.stat().st_size / 1e6), "이미지", len(files), "개")
