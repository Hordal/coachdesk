# 데이터 계약 검사 도구

상세 설계 14(모듈 간 계약과 공통 데이터)의 규칙으로 02A 가상 예제를 검사한다. 검사 로직은 `backend/app/domain/contract_check.py`에 있고, 이 디렉터리는 저장소 루트에서 실행하는 진입점과 회귀 테스트를 둔다.

## 실행

저장소 루트에서:

```bash
.venv\Scripts\python.exe tools\contract_check\check_contract.py Notion\02A_가상_사건_데이터.json Notion\02A_시나리오6_가상_데이터.json
```

```bash
.venv\Scripts\python.exe -m unittest discover -s tools\contract_check -v
```

백엔드 전체 테스트(모델, JSON Schema, 문장 생성, API)는 `backend/README.md`를 따른다.

## 검사 항목

| 구분 | 내용 |
|---|---|
| 참조 | 모든 ID 참조가 존재하고 올바른 객체 종류를 가리키는지, ID 중복 |
| 시간 | 정수 마이크로초, [start, end), 영상 길이 범위, phase_marks가 사건 구간 안인지 |
| 판정 | value·status 값, present/absent는 VALID, unknown/not_applicable은 reason_code 필수, contact_region 값과 사용 조건 |
| 집계 | 분자 ⊆ 분모, 분모·판단 불가·제외가 겹치지 않고 합이 후보와 같음, counts와 ID 수 일치, 분모 0이면 ratio=null, 판정에서 다시 계산한 결과와 기록값 일치, 조건 참조(예: AG01.numerator_refs) 일치 |
| 주장 | 보류에는 사유, valid 주장에는 근거 장면, 원인·교정 주장은 검토된 규칙 없이 valid 불가 |

집계 재계산 규칙(`backend/app/domain/aggregation.py`): present는 분자·분모(부위 조건이 있으면 같은 부위만 분자, 부위 불명확은 판단 불가, 다른 부위는 분모만), absent는 분모, unknown은 판단 불가, not_applicable·판정 없음은 제외.

회귀 테스트(`test_fixtures.py`)는 상세 설계 18의 ‘당장 가능한 계약 검사’를 구현한다.
