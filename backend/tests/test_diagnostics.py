"""진단 도구(tools/diagnostics): 불변식 검사가 위반을 잡아내고 민감도 비교가 같은 위치끼리 이루어지는지."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.domain.analysis import derive_guard_return

from .test_states import pose

SPEC = importlib.util.spec_from_file_location("flow_diagnostics", Path(__file__).resolve().parents[2] / "tools" / "diagnostics" / "flow_diagnostics.py")
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)

S = 1_000_000


def test_invariants_pass_on_derived_output_and_catch_violations():
    ev = [{"event_id": "e1", "event_label": "CROSS", "status": "VALID", "verified": True, "participant_id": "A", "start_timestamp_us": S, "end_timestamp_us": S + 500_000}]
    out = derive_guard_return(ev, [], video_id="v", duration_us=10 * S)
    assert diag.invariants(out, ev) == []
    bad = {**out, "assessments": [{**out["assessments"][0], "status": "VALID"}]}  # unknown인데 VALID
    assert diag.invariants(bad, ev)
    cand = [{**ev[0], "status": "CANDIDATE", "verified": False}]
    assert diag.invariants(out, cand)  # 확인되지 않은 사건에 판정이 있음


def test_scenario6_sensitivity_has_no_invariant_problems():
    rows = diag.scenario6_sensitivity()
    assert rows and all(r["invariant_problems"] == [] for r in rows)
    assert {r["guard_late_ms"] for r in rows} == {300, 400, 500, 600}


def test_sensitivity_compares_the_same_positions():
    rows = [pose(i, wrist_y=130) for i in range(5)] + [pose(5, wrist_y=130, vis=0.1)]
    report = diag.sensitivity(rows)
    assert all(x["total_compared"] == 12 for x in report)  # 사용 불가 줄도 손 두 자리를 차지한다
    vis = next(x for x in report if x["param"] == "min_visibility" and x["value"] == 0.7)
    assert vis["same_as_base"] <= vis["total_compared"]
