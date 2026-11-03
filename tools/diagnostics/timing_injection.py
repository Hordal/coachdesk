"""시간 측정 정확도 검사(라벨 없이). 실제 포즈 한 장을 기준 자세로 삼아 '가드가 정확히 d ms 늦게 돌아오는' 영상 줄을 만들고,
상태 판정(states.py)과 가드 복귀 판정(analysis.py)이 d를 얼마나 복원하는지 잰다.

정답이 구성으로 정해지므로 사람 라벨이 필요 없다. 검출 모델의 오류는 재지 못하며, 상태 판정·시간 측정 층의 오차만 잰다.
잡음 크기는 실제 연속 프레임의 손목 좌표 변화에서 추정하고, 프레임 누락은 무작위로 지운다.
사용: python timing_injection.py <연구 묶음 results 폴더> [--json 출력경로]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.analysis import AnalysisParams, judge_guard_return  # noqa: E402
from app.domain.models import PoseObservationRecord  # noqa: E402
from app.domain.states import StateParams, compute_features, judge_hand, judge_states  # noqa: E402

LATE_US = AnalysisParams().guard_late_us
DELAYS_MS = (100, 200, 300, 350, 450, 500, 600, 800, 1000, 1200)


def base_and_noise(rows: list[PoseObservationRecord]) -> tuple[PoseObservationRecord, float, int]:
    """실제 입력에서 (기준 자세, 손목 좌표 잡음 σ[몸통 길이 단위], 프레임 간격[µs])를 구한다.

    기준 자세: 하이가드로 판정되는 왼손이 있는 줄 중 첫 번째. σ: 같은 후보의 연속 프레임에서 손목 좌표 변화의 표준편차 ÷ √2.
    """
    params = StateParams()
    usable = [r for r in rows if r.landmarks and r.participant_id]
    base = next((r for r in usable if (f := compute_features(r, params)).unusable is None and judge_hand(f.left, params)[0] == "high_guard"), None)
    if base is None:
        raise SystemExit("하이가드로 판정되는 기준 자세가 없음")
    diffs: list[float] = []
    steps: list[int] = []
    by_pid: dict[str, list[PoseObservationRecord]] = {}
    for r in usable:
        by_pid.setdefault(r.participant_id, []).append(r)  # type: ignore[arg-type]
    for seq in by_pid.values():
        seq.sort(key=lambda r: r.frame_index)
        for a, b in zip(seq, seq[1:]):
            if b.frame_index - a.frame_index == 1 and b.timestamp_us > a.timestamp_us:
                steps.append(b.timestamp_us - a.timestamp_us)
                fa, fb = compute_features(a, params), compute_features(b, params)
                if fa.unusable is None and fb.unusable is None and fa.torso and fb.torso:
                    for side in ("left_wrist", "right_wrist"):
                        ma, mb = a.landmarks[side], b.landmarks[side]  # type: ignore[index]
                        if None not in (ma.x_px, ma.y_px, mb.x_px, mb.y_px):
                            diffs += [(mb.x_px - ma.x_px) / fa.torso, (mb.y_px - ma.y_px) / fa.torso]  # type: ignore[operator]
    sigma = statistics.pstdev(diffs) / math.sqrt(2) if len(diffs) > 5 else 0.0
    step = int(statistics.median(steps)) if steps else 33_333
    return base, sigma, step


def build_rows(base: PoseObservationRecord, *, step_us: int, drop_start_us: int, return_us: int, total_us: int,
               noise_px: float, drop_rate: float, rng: random.Random) -> list[PoseObservationRecord]:
    """왼손이 [drop_start, return)에서 낮은 가드이고 나머지는 높은 가드인 줄. 같은 기준 자세를 프레임 간격마다 복제한다."""
    raw = base.model_dump(mode="json")
    marks = raw["landmarks"]
    hip_y = (marks["left_hip"]["y_px"] + marks["right_hip"]["y_px"]) / 2
    high_y = marks["left_wrist"]["y_px"]
    low_y = hip_y  # 골반 높이: 로우가드 기준(코 아래 1.0 몸통 이상)을 넘김
    phase = rng.randrange(step_us)  # 프레임 시각과 사건 시각의 어긋남
    rows: list[PoseObservationRecord] = []
    t, i = phase, 0
    while t < total_us:
        i += 1
        if rng.random() >= drop_rate:
            d = json.loads(json.dumps(raw))
            d.update({"frame_index": i, "timestamp_us": t})
            lm = d["landmarks"]
            lm["left_wrist"]["y_px"] = (low_y if drop_start_us <= t < return_us else high_y) + rng.gauss(0, noise_px)
            lm["left_wrist"]["x_px"] += rng.gauss(0, noise_px)
            rows.append(PoseObservationRecord.model_validate(d))
        t += step_us
    return rows


def trial(base: PoseObservationRecord, *, delay_us: int, step_us: int, torso_px: float, sigma_torso: float, drop_rate: float, seed: int,
          state_params: StateParams = StateParams()) -> dict:
    rng = random.Random(seed)
    attack_end = 3_000_000
    rows = build_rows(base, step_us=step_us, drop_start_us=2_000_000, return_us=attack_end + delay_us, total_us=7_000_000,
                      noise_px=sigma_torso * torso_px, drop_rate=drop_rate, rng=rng)
    states = judge_states(rows, params=state_params).states
    rear = [{**s, "body_part": "rear_hand"} for s in states if s["body_part"] == "left_hand"]  # 왼손을 뒷손으로 본다
    attack = {"participant_id": base.participant_id, "end_timestamp_us": attack_end}
    got = judge_guard_return(attack, rear, AnalysisParams())
    return {"delay_us": delay_us, "value": got.value, "estimated_us": got.delay_us, "reason": got.reason_code, "lower_bound": got.lower_bound}


def run(base: PoseObservationRecord, sigma: float, step_us: int, *, trials: int = 20, state_params: StateParams = StateParams()) -> dict:
    f = compute_features(base, StateParams())
    torso = f.torso or 100.0
    settings = {"clean": (0.0, 0.0), "measured_noise": (sigma, 0.0), "noise_x3": (sigma * 3, 0.0), "drop_10": (sigma, 0.1), "drop_30": (sigma, 0.3)}
    out: dict = {"sigma_torso": sigma, "step_us": step_us, "trials": trials, "settings": {}}
    for name, (sg, dr) in settings.items():
        per_delay = []
        for d_ms in DELAYS_MS:
            res = [trial(base, delay_us=d_ms * 1000, step_us=step_us, torso_px=torso, sigma_torso=sg, drop_rate=dr, seed=1000 * d_ms + k, state_params=state_params) for k in range(trials)]
            known = [r for r in res if r["value"] in ("present", "absent")]
            errs = [abs(r["estimated_us"] - r["delay_us"]) / 1000 for r in known if r["estimated_us"] is not None and not r["lower_bound"]]
            expected = "present" if d_ms * 1000 > LATE_US else "absent"
            per_delay.append({
                "delay_ms": d_ms, "unknown_rate": 1 - len(known) / len(res),
                "correct_rate": sum(r["value"] == expected for r in res) / len(res),
                "median_abs_err_ms": statistics.median(errs) if errs else None, "max_abs_err_ms": max(errs) if errs else None,
                "lower_bound_rate": sum(r["lower_bound"] for r in res) / len(res),
            })
        out["settings"][name] = per_delay
    return out


BOUNDARIES = ("high_end_down", "low_start", "low_end", "high_start_up")


def _label_times(row_by_t, times: list[int], params: StateParams) -> dict[str, int | None]:
    """잡음 없는 궤적에서 엄격한 기준(히스테리시스 끔)으로 라벨이 바뀌는 시각. 정답으로 쓴다."""
    strict = StateParams(hysteresis_margin=0.0)
    labels = [judge_hand(compute_features(row_by_t(t), strict).left, strict)[0] for t in times]
    out: dict[str, int | None] = dict.fromkeys(BOUNDARIES)
    i = next((k for k, x in enumerate(labels) if x != "high_guard"), None)
    out["high_end_down"] = times[i] if i is not None else None
    j = next((k for k, x in enumerate(labels) if x == "low_guard"), None)
    out["low_start"] = times[j] if j is not None else None
    if j is not None:
        k = next((k for k in range(j, len(labels)) if labels[k] != "low_guard"), None)
        out["low_end"] = times[k] if k is not None else None
        if k is not None:
            m = next((m for m in range(k, len(labels)) if labels[m] == "high_guard"), None)
            out["high_start_up"] = times[m] if m is not None else None
    return out


def ramp_trial(base: PoseObservationRecord, *, speed: float, step_us: int, sigma_torso: float, seed: int, state_params: StateParams) -> dict[str, float | None]:
    """손이 일정한 속도(몸통 길이/초)로 내려갔다 올라올 때 각 전환 시각의 오차(추정 − 정답, ms)."""
    rng = random.Random(seed)
    raw = base.model_dump(mode="json")
    marks = raw["landmarks"]
    f0 = compute_features(base, StateParams())
    torso = f0.torso or 100.0
    high_y = marks["left_wrist"]["y_px"]
    low_y = (marks["left_hip"]["y_px"] + marks["right_hip"]["y_px"]) / 2
    ramp_us = int((low_y - high_y) / torso / speed * 1_000_000)
    hold = 1_000_000
    t_down, t_low, t_up, t_end = hold, hold + ramp_us, hold + ramp_us + hold, hold + 2 * ramp_us + hold
    total = t_end + hold

    def y_at(t: float) -> float:
        if t < t_down or t >= t_end:
            return high_y
        if t < t_low:
            return high_y + (low_y - high_y) * (t - t_down) / ramp_us
        if t < t_up:
            return low_y
        return low_y - (low_y - high_y) * (t - t_up) / ramp_us

    probe = PoseObservationRecord.model_validate(raw)

    def row_at(t: int) -> PoseObservationRecord:
        probe.landmarks["left_wrist"].y_px = y_at(t)  # type: ignore[index]
        return probe

    truth = _label_times(row_at, list(range(0, total, 1000)), state_params)
    sigma_px = sigma_torso * torso
    rows: list[PoseObservationRecord] = []
    t, i = rng.randrange(step_us), 0
    while t < total:
        i += 1
        d = json.loads(json.dumps(raw))
        d.update({"frame_index": i, "timestamp_us": t})
        d["landmarks"]["left_wrist"]["y_px"] = y_at(t) + rng.gauss(0, sigma_px)
        d["landmarks"]["left_wrist"]["x_px"] += rng.gauss(0, sigma_px)
        rows.append(PoseObservationRecord.model_validate(d))
        t += step_us
    st = [s for s in judge_states(rows, params=state_params).states if s["body_part"] == "left_hand"]
    highs = [s for s in st if s["label"] == "high_guard"]
    lows = [s for s in st if s["label"] == "low_guard"]
    est: dict[str, int | None] = dict.fromkeys(BOUNDARIES)
    if highs:
        est["high_end_down"] = highs[0]["end_us"]
        later = [h for h in highs if lows and h["start_us"] >= lows[0]["end_us"]]
        est["high_start_up"] = later[0]["start_us"] if later else None
    if lows:
        est["low_start"], est["low_end"] = lows[0]["start_us"], lows[0]["end_us"]
    return {k: (None if est[k] is None or truth[k] is None else (est[k] - truth[k]) / 1000) for k in BOUNDARIES}


def run_ramp(base: PoseObservationRecord, sigma: float, step_us: int, *, trials: int = 20, speeds=(0.5, 1.0, 2.0, 4.0, 8.0)) -> dict:
    out: dict = {"sigma_torso": sigma, "step_us": step_us, "trials": trials, "speeds": {}}
    for hyst in (0.0, StateParams().hysteresis_margin):
        sp = StateParams(hysteresis_margin=hyst)
        for sg_name, sg in (("clean", 0.0), ("measured_noise", sigma)):
            for speed in speeds:
                res = [ramp_trial(base, speed=speed, step_us=step_us, sigma_torso=sg, seed=int(speed * 100) * 1000 + k, state_params=sp) for k in range(trials)]
                row = {}
                for b in BOUNDARIES:
                    vals = [r[b] for r in res if r[b] is not None]
                    row[b] = {"n": len(vals), "median_ms": statistics.median(vals) if vals else None,
                              "p10_ms": sorted(vals)[int(0.1 * (len(vals) - 1))] if vals else None, "p90_ms": sorted(vals)[int(0.9 * (len(vals) - 1))] if vals else None}
                out["speeds"][f"hyst={hyst}|{sg_name}|speed={speed}"] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--ramp", action="store_true", help="계단 대신 일정 속도로 내려갔다 올라오는 손의 전환 시각 오차를 잰다")
    ap.add_argument("--hysteresis", type=float, default=StateParams().hysteresis_margin, help="히스테리시스 여유(몸통 길이 단위). 0이면 끔")
    args = ap.parse_args()
    path = args.results / "mvp_phase1c_participant_temporal_pose" / "pose_quality_frames.jsonl"
    rows = [PoseObservationRecord.model_validate(json.loads(x)) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    base, sigma, step = base_and_noise(rows)
    report = run_ramp(base, sigma, step, trials=args.trials) if args.ramp else run(base, sigma, step, trials=args.trials, state_params=StateParams(hysteresis_margin=args.hysteresis))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
