# 백엔드

상세 설계 27의 구현 순서 1~6단계: 데이터 구조 모델, 집계·문장 생성, 결과 조회 API, 근거 영상과 관절, 영상 올리기와 처리 작업. 인증과 분석 실행은 아직 없다. 가상 예제(02A) 두 개를 분석 결과로 제공하고, 올린 영상은 분석 전 상태의 실행으로 등록한다.

## 준비와 실행

저장소 루트의 가상환경(`.venv`)을 사용한다(Python 3.13. 요구 버전은 3.11 이상이며 3.11에서도 시험함). 이 디렉터리(`backend`)에서 실행한다.

```bash
..\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

```bash
..\.venv\Scripts\python.exe -m uvicorn app.api.asgi:app --reload
```

영상 처리 작업을 실행하는 워커는 API 서버와 별도 프로세스로 띄운다. 작업이 없으면 대기하고, `--once`를 주면 대기 중인 작업을 모두 처리하고 끝난다.

```bash
..\.venv\Scripts\python.exe -m app.jobs.worker
```

서버가 뜨면 http://127.0.0.1:8000/docs 에서 API를 확인할 수 있다.

```bash
..\.venv\Scripts\python.exe -m pytest
```

의존성의 확정 버전은 `requirements.lock.txt`에 있다.

## 스키마 내보내기

`app/domain/models.py`의 모델이 단일 정의이며, JSON Schema(2020-12)와 OpenAPI는 여기서 내보낸다. 모델이나 API를 바꾼 뒤에는 다시 내보내야 하고, 테스트가 저장된 파일이 최신인지 확인한다.

```bash
..\.venv\Scripts\python.exe scripts\export_schema.py
```

| 파일 | 용도 |
|---|---|
| `schemas/result_bundle.schema.json` | 분석 결과 묶음의 JSON Schema. 가져오기 파일(manual 모드) 검증 |
| `schemas/openapi.json` | API 명세. 화면의 TypeScript 클라이언트 생성 입력 |

## 구성

| 경로 | 내용 |
|---|---|
| `app/domain/models.py` | 14의 객체(Event, Assessment, Aggregate, Finding 등) |
| `app/domain/aggregation.py` | 판정에서 분자·분모·판단 불가·제외를 다시 계산 |
| `app/domain/contract_check.py` | 참조·시간·판정·집계·보류 규칙 검사 |
| `app/domain/findings.py` | 템플릿 문장 생성. 저장된 집계와 재계산이 다르면 문장을 만들지 않음 |
| `app/pipeline/` | 분석 단계 인터페이스 12개, 실행 모드(fixture·manual·partial·full), fixture 공급 |
| `app/storage/` | SQLite(`db.py`, 업로드·영상·작업·실행 표), 영상 저장소(`videos.py`), 가상 예제용 메모리 저장소(`memory.py`) |
| `app/services/uploads.py` | 분할 업로드 시작·조각 수신·완료(검증 후 원본 등록, 영상과 작업 생성) |
| `app/jobs/` | 작업 큐(`store.py`), 영상 준비 단계(`ingest.py`), FFmpeg 호출(`runner.py`), 워커(`worker.py`) |
| `app/api/` | FastAPI 앱, RFC 9457 오류 응답, 결과 조회·영상 라우터 |
| `app/media/` | FFmpeg·FFprobe 호출, 프레임 표, 서명 주소, 영상·관절 파일 저장소(개발용 로컬 디렉터리) |

## API (읽기 전용)

| 경로 | 설명 |
|---|---|
| `GET /api/v1/runs`, `GET /api/v1/runs/{run_id}` | 실행 목록, 요약. 올린 영상의 결과에는 `filename`이 있고 가상 예제는 null |
| `GET /api/v1/runs/{run_id}/findings` | 주장과 문장. 보류된 주장은 text가 null이고 사유가 있음 |
| `GET /api/v1/runs/{run_id}/aggregates/{aggregate_id}` | 분자·분모·판단 불가·제외의 구성 사건과 판정 |
| `GET /api/v1/runs/{run_id}/timeline?from_us=&to_us=` | 구간과 겹치는 상태·사건·관계·판정. 구간은 [시작, 끝) |
| `GET /api/v1/runs/{run_id}/events/{event_id}` | 사건과 관계·공방·근거 |
| `GET /api/v1/runs/{run_id}/evidence/{evidence_id}` | 근거 구간. 영상이 없으면 playable=false |

다른 계정의 실행은 존재 여부를 알리지 않기 위해 404로 응답한다.

## 근거 영상과 관절 API (구현 순서 5단계)

| 경로 | 설명 |
|---|---|
| `GET /api/v1/runs/{run_id}/evidence` | 근거 구간 목록. 영상이 저장소에 있으면 `playable=true`와 서명 주소(5분 유효), 없으면 사유와 함께 `playable=false` |
| `GET /api/v1/media/{video_id}?exp=&sig=` | 서명된 주소로만 영상 제공(범위 요청 지원). 서명이 틀리거나 만료되면 403 |
| `GET /api/v1/videos/{video_id}` | 해상도, 길이, 프레임 수, 가변 프레임률 여부 |
| `GET /api/v1/videos/{video_id}/frames?from_us=&to_us=` | 구간의 실제 프레임 표시 시각 표(표시 순서 번호 포함). 구간 시작에 보이던 프레임부터 |
| `GET /api/v1/videos/{video_id}/frame-image?time_us=` | 그 시각에 표시되는 프레임을 디코더가 식별한 그대로 JPEG로 제공. 응답 헤더에 프레임 번호와 시각 |
| `GET /api/v1/runs/{run_id}/poses?from_us=&to_us=` | 구간의 관절 관측. 저장된 분석 좌표를 표시 좌표로 되돌려 관절 목록으로 응답. 좌표가 없는 관절은 `state=missing`이며 0으로 채우지 않음. 없으면 `available=false`와 사유 |

- 재생 가능 여부는 저장된 값이 아니라 지금 파일 저장소에 영상이 있는지로 정한다.
- 영상 파일은 공개 경로에 두지 않고, 소유자 확인 후 발급한 서명 주소로만 접근한다. 다른 계정의 영상은 존재 여부를 알리지 않고 404로 응답한다.
- 프레임 시각은 ffprobe가 읽은 표시 시각이며 명목 fps로 만들지 않는다. 표시 시각 정보가 비었거나 겹치면 오류로 처리한다.
- FFmpeg는 환경 변수(`FFMPEG_PATH`, `FFPROBE_PATH`), PATH, winget 설치 위치 순으로 찾는다.

## 영상 올리기와 처리 작업 API (구현 순서 6단계)

| 경로 | 설명 |
|---|---|
| `POST /api/v1/uploads` | 업로드 시작. `Idempotency-Key` 헤더 필수. 확장자·크기·남은 저장 공간을 먼저 검사하고 조각 크기와 개수를 알려 줌 |
| `PUT /api/v1/uploads/{id}/parts/{n}` | 조각 수신. `X-Part-Sha256` 헤더로 조각의 해시를 검증. 같은 조각을 다시 보내도 결과가 같음 |
| `GET /api/v1/uploads/{id}` | 받은 조각 번호. 이어 올리기에 씀 |
| `POST /api/v1/uploads/{id}/complete` | 모든 조각을 합쳐 내용으로 검증(컨테이너·코덱·길이·해상도)한 뒤 원본 등록, 영상과 작업 생성. 같은 요청을 다시 보내도 같은 결과 |
| `DELETE /api/v1/uploads/{id}` | 업로드 취소와 임시 조각 삭제 |
| `GET /api/v1/jobs/{id}`, `POST /api/v1/jobs/{id}/cancel` | 작업 단계별 진행과 취소 |
| `GET /api/v1/videos`, `GET /api/v1/videos/{id}/summary` | 내 영상 목록, 영상 상태와 작업 진행 |
| `POST /api/v1/videos/{id}/observations` | 선수 검출·추적 결과(`metadata.json`, 추적 프레임, 검출 프레임)를 읽기 전용으로 가져옴. 프레임 수와 모든 프레임 시각이 영상과 맞지 않으면 422 `invalid_observations`(오류 목록)이고 아무것도 저장하지 않음. 같은 내용은 200으로 기존 결과를 돌려줌 |
| `GET /api/v1/videos/{id}/observations` | 가장 최근에 가져온 결과와 후보 트랙의 진단 값(보이는 구간, 끊긴 프레임, 가장 긴 공백, 화면 면적 중앙값). 트랙은 후보일 뿐이며 선수 배정 상태는 NOT_RUN |
| `GET /api/v1/videos/{id}/designation-frames` | 후보 트랙이 보기 좋게 보이는 대표 프레임(최대 5개)과 표시 영상 좌표의 상자. 후보가 두 개 미만이면 `candidate_state=INSUFFICIENT` |
| `GET·PUT /api/v1/videos/{id}/designation` | 선수 지정. 선택 전에는 `NOT_RUN`. PUT은 사용자가 고른 `user_track_ids`·`opponent_track_ids`와 기준 `set_id`를 받아 모순(없는 트랙 422, 양쪽 지정 422, 같은 선수의 트랙이 동시에 보임 422, 결과가 바뀜 409)을 거절 |
| `GET /api/v1/videos/{id}/playback` | 분석 결과가 없는 영상 전체의 서명 주소 |
| `DELETE /api/v1/videos/{id}` | 영상과 결과 전체 삭제. 즉시 조회를 막고 정리가 끝나면 `deleted`, 아직이면 `deleting`(워커가 다시 시도) |
| `DELETE /api/v1/videos/{id}/media` | 원본·재생용 사본만 삭제. 결과는 남고 근거 영상은 삭제됨으로 표시. 처리 중이면 409 |
| `POST /api/v1/videos/{id}/imports` | 사람이 입력한 사건·판정 JSON을 가져와 manual 모드의 새 실행으로 저장(구현 순서 7단계) |

- 작업 단계는 전체 읽기 검사, 재생용 사본 만들기, 프레임 시각 확인, 결과 등록이며 분석 단계는 `unsupported`로 표시한다. 끝나면 영상은 `ready`, 작업은 `partial`이 되고 `dataset_kind=unanalyzed`인 실행이 하나 생긴다.
- 진행은 단계 가중치로 계산한 비율만 제공하고 완료 예정 시간은 제공하지 않는다.
- 워커는 10초마다 lease를 연장하고, 60초 동안 연장하지 않으면 다른 워커가 마지막 완료 단계부터 다시 시작한다. 시도는 최대 3회(재시도 2회)이며 영상 손상 같은 영구 오류는 다시 시도하지 않는다. 소유권을 잃은 워커의 늦은 쓰기는 거부한다.
- 오류 응답과 작업 오류에는 내부 경로와 FFmpeg 출력을 넣지 않고 안전한 제목, 영상 시각, `trace_id`만 담는다.
- 작업 폴더 `data/`(환경 변수 `DATA_DIR`)에 SQLite 파일, 임시 조각, 원본, 재생용 사본이 저장되며 저장소에 포함되지 않는다.

입장 검사 한도(환경 변수 또는 `app/settings.py`에서 바꿈): 파일 4 GiB, 영상 길이 1시간, 해상도 3840×2160, 조각 8 MiB(`UPLOAD_PART_SIZE`), 필요한 여유 공간은 파일 크기의 3배. 이 값들은 모두 임시 값이다(상세 설계 27의 10절). 실제 사용 길이를 정하고 길이별 처리 시간을 실측한 뒤 정한다(상세 설계 13).

## 개발용 영상

장면 복기 화면을 시험하는 영상과 관절 데이터를 만든다(FFmpeg 필요, 약 10초). 결과는 `backend/dev_media/`에 저장되며 저장소에 포함되지 않는다.

```bash
..\.venv\Scripts\python.exe scripts\make_demo_media.py
```

| 파일 | 내용 |
|---|---|
| `demo-video-01.mp4` | 30fps 고정 프레임률. 상대 반격 예제(fixture-counter)의 영상 |
| `demo-video-02.mp4` | 일부 프레임을 버린 가변 프레임률. 시나리오 6 예제(fixture-scenario6)의 영상. 브라우저가 일부 프레임(19.0·19.4·19.8·20.2·20.6초)을 표시하지 않는 것이 확인되어 정지 프레임 전환 시험에 쓴다 |
| `*.poses.json` | 두 예제의 근거 구간에서만 만든 가상 관절 데이터. 핵심 관절 13개를 분석 좌표(1280×720, 시험 영상의 2배)로 저장 |

영상은 두 정사각형(A 노랑, B 하늘색)이 표시 시각 t의 함수로 움직이고, 관절 데이터도 같은 식으로 각 프레임의 실제 표시 시각에서 계산한다. 그래서 오버레이가 사각형과 겹치는지로 동기화를 측정할 수 있다.

## 참고

- 응답 JSON은 UTF-8이다. Windows PowerShell 5.1의 `Invoke-RestMethod`는 charset이 없는 JSON을 다른 인코딩으로 읽어 한국어가 깨져 보일 수 있다. `Invoke-WebRequest`의 바이트를 UTF-8로 해석하거나 브라우저·curl을 사용한다.
- 시험 중 Starlette가 `httpx` 대신 `httpx2`를 권장한다는 경고를 낸다. 동작에는 영향이 없고, 라이브러리 정리 시 교체를 검토한다.

## 사건·판정 가져오기 (구현 순서 7단계)

입력 파일의 형식은 `schemas/manual_import.schema.json`이다. 영상 번호, 길이, 종목, 데이터 종류, 전문가 검수 여부는 입력에 넣을 수 없고 서버가 영상에서 채운다. 집계는 정의(후보 사건, 질문, 부위 조건)만 받고 분자·분모·판단 불가·제외·비율은 판정에서 계산한다. 주장은 템플릿과 입력 집계만 받고 문장은 읽을 때 만든다.

- 영상 준비가 끝난 본인의 영상에만 가져올 수 있다(아니면 409, 남의 영상은 404).
- 형식, 데이터 계약 검사(참조·시간·판정·집계·보류 규칙), 근거·집계 범위의 영상 일치, 문장 템플릿 검증을 모두 통과해야 저장한다. 실패하면 422 `invalid_import`와 오류 목록(최대 20건, 전체 건수 `error_count`)을 돌려주고 아무것도 저장하지 않는다.
- 가져올 때마다 새 실행이 생기고 이전 실행(분석 전 실행 포함)은 그대로 남는다. 가상 예제(02A)에서 서버 계산 필드를 뺀 파일을 가져오면 예제와 같은 집계값과 문장이 나온다(`tests/test_manual_import.py`).

## 삭제·정정·신고 (구현 순서 8단계)

- 삭제 절차(15): 조회 차단 → 작업 취소 요청 → 파일 삭제와 확인 → DB 행 삭제. 파일이 남아 있으면 기록을 지우지 않고 `deleting` 상태로 두며 워커가 10초마다 다시 시도한다. 원본·재생용 사본·임시 파일은 `data/originals`, `data/media`에서 지운다(개발용 영상 `dev_media`는 건드리지 않는다).
- 보관 기간: 영상 준비 후 `RETENTION_DAYS`(기본 7, 0이면 끔)일이 지나면 워커가 원본·재생용 사본을 만료 처리한다. 결과 요약은 남고 근거 영상은 `evidence_expired`로 표시한다. 7일은 임시 값이다.
- `POST /api/v1/runs/{id}/corrections/preview`, `POST /api/v1/runs/{id}/corrections`: 본인 지정(`{"kind":"designation","user_participant_id":"B"}`). 새 실행을 만들어 `parent_run_id`로 연결하고 이전 실행은 보존한다. 이미 정정된 실행은 409 `stale_run`과 `latest_run_id`, 같은 지정은 409 `no_change`.
- `GET /api/v1/runs/{id}/corrections`: 최초 실행부터 최신 실행까지의 이력.
- `POST·GET /api/v1/runs/{id}/reports`, `POST·GET /api/v1/runs/{id}/context-notes`: 신고(위치·기술·수치·근거 불일치·기타)와 의도 설명(self_report). 따로 보관하며 계산을 바꾸지 않는다.
- 선수 후보 프레임 제시, 선수 연결·라운드 경계 정정은 선수 추적과 라운드 구조가 필요해 아직 없다.

## 인증 (구현 순서 9단계)

`AUTH_MODE` 환경 변수로 방식을 고른다.

| 값 | 동작 |
|---|---|
| `dev`(기본) | 고정 계정, 로그인 없음. 개발·시험 전용이며 이 상태로 배포하지 않는다 |
| `session` | 초대 계정과 쿠키 세션. 배포는 이 값과 `COOKIE_SECURE=1`(TLS)로 실행한다 |

```bash
..\.venv\Scripts\python.exe -m app.auth.cli create-admin --email you@example.com
```

비밀번호는 입력창에서 받으며 `--password-stdin`으로 표준 입력에서도 읽을 수 있다(명령줄 인자로 받지 않는다). 첫 관리자를 만든 뒤에는 관리자가 `POST /api/v1/admin/invites`로 초대를 만들고, 받은 사람이 `POST /api/v1/auth/invites/{token}/accept`로 계정을 만든다.

| 경로 | 설명 |
|---|---|
| `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/me` | 세션. `GET /me`가 변경 요청용 CSRF 토큰을 줌 |
| `GET /api/v1/auth/invites/{token}`, `POST …/accept` | 초대 확인과 수락(동의 필수) |
| `POST·GET /api/v1/admin/invites`, `DELETE /api/v1/admin/invites/{id}` | 초대 발급(링크는 이때만 보임)·목록·폐기 |
| `GET /api/v1/admin/users`, `POST /api/v1/admin/users/{id}/block·unblock` | 계정 목록, 차단·해제(차단하면 세션도 끊김) |

- 쿠키로 인증된 변경 요청(POST·PUT·DELETE)은 `X-CSRF-Token` 헤더가 필요하다. 쿠키는 HttpOnly·SameSite=Lax이고 유효 시간은 12시간이다.
- 비밀번호는 argon2-cffi로 해시한다. 세션·초대 토큰은 해시만 저장한다.
- 로그인 실패가 15분 동안 5회(IP는 20회)이면 429로 막는다. 한도는 `app/settings.py`에 있다.
- 관리자도 다른 사람의 영상과 결과는 열람할 수 없다. 이전 `dev` 모드의 데이터(소유자 `dev-user`)와 가상 예제는 `session` 모드의 사용자에게 보이지 않는다.
- 이용 안내·동의 문구는 검토 전이며 동의 버전 `unreviewed-0`으로 기록한다.
- Windows PowerShell 5.1에서 비밀번호를 파이프로 넘기면 앞에 BOM이 붙는데 명령줄 도구가 제거한다.

## 관측 데이터 형식 (선수 검출·추적·포즈 파이프라인과의 계약)

`app/domain/models.py`에 선수 검출·추적·포즈 파이프라인(MMACoachAgent 저장소의 `ai/schemas`)과 맞춘 형식이 있다. JSON Schema는 `schemas/observations.schema.json`이다.

| 형식 | 내용 |
|---|---|
| `ResultStatus` | VALID / UNKNOWN / UNSUPPORTED / ERROR. 입력이 없으면 UNKNOWN과 `reason_code` |
| `Participant` | `role` USER/OPPONENT, `track_ids`. 선택 전에는 만들지 않음 |
| `DetectionFrame`, `TrackFrame`, `Track` | `timestamp_us`, `bbox_xyxy`, `score`·`tracking_score`. `track_id`는 선수 ID가 아님 |
| `TrackAssignment`, `TrackAssignmentSet` | 선수와 추적 번호 목록의 대응. 상태 NOT_RUN/ASSIGNED |
| `PoseFrame`, `PoseSet` | 핵심 관절 13개의 이름 사전, 값은 (x, y, 점수). 분석 좌표와 `FrameTransform` |
| `AnalysisWarning`, `ModuleOutcome` | 단계 응답의 경고와 결과 요약 |
| `DistanceState`, `MovementDirection`, `RangeChange` | 거리 상태, 이동 방향, 거리 변화 열거값 |

`FrameTransform`의 변환식은 `analysis = display × scale + pad`이다.

## 검출·추적 결과 가져오기

선수 검출·추적 파이프라인(MMACoachAgent 저장소의 `ai/`)의 출력을 영상에 연결한다. 영상 준비가 끝난 본인의 영상에만 가져올 수 있다.

- 입력은 파이프라인이 쓴 `metadata.json`(`frame_timestamps_us`, `transform` 포함), `tracking_frames.jsonl`, `detection_frames.jsonl`(선택)을 줄 형식 그대로 하나의 JSON(`metadata`, `tracks`, `detections`)으로 묶은 것이다. 화면의 가져오기 페이지가 JSONL을 읽어 묶어 보낸다. 모르는 필드(`timing_ms` 등)도 그대로 보존한다.
- 가져올 때 프레임 수, 모든 프레임 시각, 추적·검출 프레임의 시각, 상자 좌표, 점수, 한 프레임 안의 번호 중복을 검사한다. 프레임 시각은 원본 영상의 프레임 시각과 비교하며(2µs까지), 원본이 없으면 재생용 사본과 비교한다(1ms까지, 영상 준비 단계가 확인한 한도). 시작 시각이 옮겨진 상수 차이는 허용한다. 검출 프레임은 크기가 분석 프레임과 같고 `coordinate_space`가 `analysis_frame_xyxy_px`여야 한다.
- 저장 위치는 `data/observations/{video_id}/{set_id}.json`이며 요약은 DB(`observation_sets`)에 있다. 영상과 결과를 모두 지우면 함께 지우고, 원본 영상만 지울 때는 구조화된 결과로서 남는다.
- 가져온 값은 고치지 않는다. 분석 컴퓨터의 로컬 경로(`derivative_path`)와 모르는 필드는 저장하지 않는다.

## 시간 계산

프레임 시각은 정수 PTS에 time_base를 곱해 반올림(0.5는 올림)한 마이크로초이고, 가변 프레임률 판정은 평균 프레임률과 보고된 프레임률의 상대 오차(0.1%)로 한다. 두 방식 모두 선수 검출·추적 파이프라인과 같다. 프레임 번호와 fps로 시각을 만들지 않는다.

## 형식별 시험

`tests/test_media_formats.py`가 세로 회전 영상, HEVC `.mov`, 가변 프레임률, 잘린 파일, 확장자·해상도 한도를 작은 합성 영상으로 확인한다. 실제 영상(Pexels 등)으로는 `_external/testvideos/`에 받아 두고 업로드부터 재생까지 돌려 보았다(결과는 상세 설계 27의 9절).
