"""시간 측정 정확도 검사(tools/diagnostics/timing_injection.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .test_states import pose

SPEC = importlib.util.spec_from_file_location("timing_injection", Path(__file__).resolve().parents[2] / "tools" / "diagnostics" / "timing_injection.py")
ti = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ti)

STEP = 33_333


def test_clean_input_recovers_the_injected_delay_within_one_frame():
    base = pose(0, wrist_y=130)
    base.timestamp_us = 0
    report = ti.run(base, 0.0, STEP, trials=5)
    for row in report["settings"]["clean"]:
        assert row["unknown_rate"] == 0 and row["max_abs_err_ms"] <= STEP / 1000 + 1, row
        if abs(row["delay_ms"] - 400) > 60:  # 기준(400ms) 근처는 프레임 어긋남으로 갈릴 수 있다
            assert row["correct_rate"] == 1.0, row


def test_trial_is_deterministic_and_noise_never_turns_unknown_into_a_value():
    base = pose(0, wrist_y=130)
    kw = dict(delay_us=600_000, step_us=STEP, torso_px=100.0, sigma_torso=0.2, drop_rate=0.2, seed=7)
    assert ti.trial(base, **kw) == ti.trial(base, **kw)
    r = ti.trial(base, **kw)
    assert (r["value"] == "unknown") == (r["estimated_us"] is None)  # 판단 불가는 값 없이 나온다(0으로 채우지 않음)


def test_transition_delay_appears_only_when_leaving_a_state_and_scales_with_slowness():
    base = pose(0, wrist_y=120)
    base.timestamp_us = 0
    rep = ti.run_ramp(base, 0.0, STEP, trials=4, speeds=(1.0, 4.0))["speeds"]
    for speed in (1.0, 4.0):
        off, on = rep[f"hyst=0.0|clean|speed={speed}"], rep[f"hyst=0.09|clean|speed={speed}"]
        for b in ti.BOUNDARIES:
            assert abs(off[b]["median_ms"]) <= 1.5 * STEP / 1000, (speed, b, off[b])  # 끈 쪽은 샘플링 간격 이내
        for b in ("low_start", "high_start_up"):  # 상태에 들어가는 쪽은 늦춰지지 않는다
            assert abs(on[b]["median_ms"]) <= 1.5 * STEP / 1000, (speed, b, on[b])
        expected = 0.09 / speed * 1000
        for b in ("high_end_down", "low_end"):  # 상태에서 나가는 쪽은 여유 ÷ 속도만큼 늦다
            assert expected - 20 <= on[b]["median_ms"] <= expected + 60, (speed, b, on[b], expected)
    assert rep["hyst=0.09|clean|speed=1.0"]["low_end"]["median_ms"] > rep["hyst=0.09|clean|speed=4.0"]["low_end"]["median_ms"]
