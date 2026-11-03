"""fixture 모드: 가상 예제 JSON을 실행 결과로 공급한다."""

from __future__ import annotations

from pathlib import Path

from ...domain.contract_check import check_document, load
from ...domain.models import ResultBundle
from ...storage.memory import RunStore, StoredRun
from ..registry import RunMode


class FixtureError(Exception):
    pass


def load_fixture_run(run_id: str, path: Path, owner_id: str) -> StoredRun:
    """형식(모델)과 의미(계약 검사)를 모두 통과한 결과만 읽는다."""
    raw = load(path)
    bundle = ResultBundle.model_validate(raw)
    report, _ = check_document(raw, str(path))
    if not report.ok:
        raise FixtureError(f"{path.name}: 계약 검사 실패 {report.errors}")
    return StoredRun(run_id=run_id, owner_id=owner_id, mode=RunMode.FIXTURE, status="completed", bundle=bundle, raw=raw)


def load_fixtures(store: RunStore, runs: dict[str, Path], owner_id: str) -> None:
    for run_id, path in runs.items():
        store.add(load_fixture_run(run_id, path, owner_id))
