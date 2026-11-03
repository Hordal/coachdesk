"""사용자 정정(상세 설계 11, 16).

정정은 이전 결과를 고치지 않고 새 실행을 만들어 parent_run_id로 연결한다. 최초 결과는 그대로 남는다.
지금 구현한 정정은 본인(선수 지정)뿐이다. 선수 연결·라운드 경계 정정은 선수 추적과 라운드 구조가
생긴 뒤에 추가한다.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass

from ..domain.contract_check import check_document
from ..domain.models import ResultBundle
from ..storage.db import Database, now_rfc3339
from ..storage.memory import RunStore, StoredRun


class CorrectionError(Exception):
    def __init__(self, status: int, code: str, title: str, **extra: object) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.extra = status, code, title, extra


@dataclass(frozen=True)
class RoleChange:
    participant_id: str
    role_before: str
    role_after: str


def plan_designation(run: StoredRun, user_participant_id: str) -> list[RoleChange]:
    """본인을 지정했을 때 바뀌는 역할. 선수가 정확히 두 명일 때만 상대를 나머지로 정한다."""
    participants = run.bundle.participants
    if len(participants) != 2:
        raise CorrectionError(422, "nothing_to_designate", "지정할 선수가 두 명 있는 결과에서만 본인을 지정할 수 있음")
    ids = [p.participant_id for p in participants]
    if user_participant_id not in ids:
        raise CorrectionError(422, "unknown_participant", "이 결과에 없는 선수", participant_id=user_participant_id)
    changes = []
    for p in participants:
        after = "USER" if p.participant_id == user_participant_id else "OPPONENT"
        if p.role != after or p.user_designated != (after == "USER"):
            changes.append(RoleChange(p.participant_id, p.role, after))
    return changes


class CorrectionService:
    def __init__(self, db: Database, store: RunStore) -> None:
        self._db = db
        self._store = store

    def apply_designation(self, run: StoredRun, user_participant_id: str) -> StoredRun:
        changes = plan_designation(run, user_participant_id)
        if not changes:
            raise CorrectionError(409, "no_change", "이미 같은 선수가 본인으로 지정되어 있음")
        latest = self._store.child_of(run.run_id)
        if latest is not None:
            # 다른 곳에서 먼저 정정했다. 오래된 결과를 기준으로 덮어쓰지 않고 최신 실행을 알려 준다.
            raise CorrectionError(409, "stale_run", "이 결과는 이미 정정되어 최신이 아님", latest_run_id=latest)

        raw = copy.deepcopy(run.raw)
        for p in raw["participants"]:
            is_user = p["participant_id"] == user_participant_id
            p["role"] = "USER" if is_user else "OPPONENT"
            p["user_designated"] = is_user
        roles = {p["participant_id"]: p["role"] for p in raw["participants"]}
        for event in raw.get("events", []):
            if event.get("participant_id") in roles and event.get("role") is not None:
                event["role"] = roles[event["participant_id"]]
        raw.setdefault("notes", []).append("사용자가 본인을 지정해 선수 역할을 정정함")
        model = ResultBundle.model_validate(raw)
        report, _ = check_document(raw, run.run_id)
        if not report.ok:
            raise CorrectionError(500, "correction_invalid", "정정한 결과가 데이터 계약에 맞지 않음")

        new = StoredRun(run_id=uuid.uuid4().hex, owner_id=run.owner_id, mode=run.mode, status=run.status, bundle=model, raw=raw)
        def persist(conn):
            conn.execute(
                "INSERT INTO corrections (correction_id, owner_id, video_id, parent_run_id, run_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?, 'designation', ?, ?)",
                (uuid.uuid4().hex, run.owner_id, model.video_id, run.run_id, new.run_id, json.dumps({"user_participant_id": user_participant_id}), now_rfc3339()),
            )
            original=conn.execute("SELECT * FROM run_provenance WHERE run_id=?", (run.run_id,)).fetchone()
            if original:
                assignment=json.loads(original["assignment_json"])
                for assigned in assignment["assignments"]:
                    assigned["role"]=roles.get(assigned["participant_id"],assigned["role"])
                conn.execute("INSERT INTO run_provenance VALUES(?,?,?,?)", (new.run_id,original["observation_set_id"],original["pose_set_id"],json.dumps(assignment)))
        # 실행·정정 이력·표시 좌표 정보를 같은 트랜잭션에서 저장한다.
        try:
            self._store.add_persistent(new,parent_run_id=run.run_id,extra=persist)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise CorrectionError(409, "stale_run", "이 결과는 이미 정정되어 최신이 아님", latest_run_id=self._store.child_of(run.run_id)) from exc
            raise
        return new
