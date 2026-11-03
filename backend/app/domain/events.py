"""사건(팀 동작 검출 모델 형식) 보조 함수.

사건 필드는 팀 출력 형식을 그대로 따르므로 분류·행위자 같은 서버 쪽 해석은 여기서 한다.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .models import GROUND_EVENT_TYPES

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


BLOCK_LABELS = frozenset({"BLOCK", "PARRY"})  # 임시: 팀 방어 라벨 확정 전
EVADE_LABELS = frozenset({"SLIP", "DUCK", "LEAN_BACK", "STEP_BACK", "EVADE"})  # 임시
DEFENSE_LABELS = BLOCK_LABELS | EVADE_LABELS


def category_of(event_label: str) -> str:
    """화면 묶음용 분류: ground(그라운드 사건) · contact(접촉) · action(그 밖의 행동·후보)."""
    if event_label in GROUND_EVENT_TYPES:
        return "ground"
    if "CONTACT" in event_label.upper():
        return "contact"
    return "action"


def actor_of(event: Mapping[str, Any]) -> str | None:
    return event.get("participant_id")


def target_of(event: Mapping[str, Any]) -> str | None:
    return event.get("target_participant_id")


def _slug(text: str) -> str:
    return _SAFE.sub("-", text).strip("-") or "x"


def assign_event_ids(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """event_id가 없는 사건에 결정적 ID를 붙인다. 팀 출력에는 ID가 없다.

    형식: ev_{시작 마이크로초}_{라벨}_{선수}. 같은 값이 겹치면 번호를 덧붙인다. 입력은 바꾸지 않고 복사본을 돌려준다.
    """
    used = {e["event_id"] for e in events if e.get("event_id")}
    out: list[dict[str, Any]] = []
    for e in events:
        e = dict(e)
        if not e.get("event_id"):
            base = f"ev_{e['start_timestamp_us']}_{_slug(e['event_label'])}_{_slug(e.get('participant_id') or 'none')}"
            candidate, n = base, 2
            while candidate in used:
                candidate, n = f"{base}_{n}", n + 1
            e["event_id"] = candidate
            used.add(candidate)
        out.append(e)
    return out
