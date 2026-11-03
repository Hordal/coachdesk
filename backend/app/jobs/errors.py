"""작업 처리 중의 오류 구분(상세 설계 12).

- PermanentError: 잘못된 입력·미지원. 자동 재시도하지 않는다.
- TransientError: 일시적 오류(디스크·입출력 등). 최대 2회까지 다시 시도한다.
- JobCancelled: 사용자가 취소를 요청했다.
메시지(title)는 사용자에게 보여줄 안전한 문구이며 경로·스택을 담지 않는다.
"""

from __future__ import annotations


class PermanentError(Exception):
    def __init__(self, code: str, title: str, *, at_us: int | None = None) -> None:
        super().__init__(title)
        self.code = code
        self.title = title
        self.at_us = at_us


class TransientError(Exception):
    def __init__(self, code: str, title: str) -> None:
        super().__init__(title)
        self.code = code
        self.title = title


class JobCancelled(Exception):
    pass
