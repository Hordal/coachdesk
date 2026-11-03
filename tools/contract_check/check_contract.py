"""데이터 계약 검사 진입점.

구현은 backend/app/domain/contract_check.py에 있다. 이 파일은 저장소 루트에서
`python tools/contract_check/check_contract.py <예제.json>`로 실행하기 위한 얇은 진입점이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.domain.aggregation import AggregateCounts, recompute_aggregate  # noqa: E402,F401
from app.domain.contract_check import (  # noqa: E402,F401
    Report,
    assessment_index,
    check_document,
    load,
    main,
)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
