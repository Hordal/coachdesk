"""흐름 분석 진단(라벨 없이). 팀 동작 검출 모델의 실제 출력에 상태 판정·후보 정책을 돌려 어디까지 분석되고 어디서 막히는지 센다.

정확도 검사가 아니다. 정답 없이 확인할 수 있는 것만 낸다: (1) 입력 커버리지와 판단 불가 사유, (2) 임시 매개변수 민감도, (3) 불변식.
사용: python flow_diagnostics.py <연구 묶음 results 폴더> [--json 출력경로]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.analysis import AnalysisParams, derive_guard_return  # noqa: E402
from app.domain.models import PoseObservationRecord  # noqa: E402
from app.domain.policy import CONFIRMED, event_state  # noqa: E402
from app.domain.states import StateParams, compute_features, judge_hand, judge_states  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sources(results: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    b = results / "mvp_phase1b_pose_quality_action"
    c = results / "mvp_phase1c_participant_temporal_pose"
    if (b / "pose_observations.jsonl").exists():
        out["1B"] = {"poses": read_jsonl(b / "pose_observations.jsonl"), "events": read_jsonl(b / "action_candidates.jsonl")}
    if (c / "pose_quality_frames.jsonl").exists():
        out["1C"] = {"poses": read_jsonl(c / "pose_quality_frames.jsonl"), "events": read_jsonl(c / "action_candidates.jsonl")}
    return out


def parse(rows: list[dict]) -> list[PoseObservationRecord]:
    return [PoseObservationRecord.model_validate(r) for r in rows]


def coverage(rows: list[PoseObservationRecord], params: StateParams) -> dict:
    """프레임 줄 단위 커버리지. 손 판정이 되려면 몸통 5점과 손목이 유효해야 한다."""
    res = Counter()
    labels = Counter()
    for r in rows:
        res["rows"] += 1
        res[f"status:{r.status or 'NONE'}"] += 1
        if not r.landmarks:
            res["no_landmarks"] += 1
            continue
        if not r.participant_id:
            res["no_participant"] += 1
            continue
        f = compute_features(r, params)
        if f.unusable:
            res[f"unusable:{f.unusable}"] += 1
            continue
        for side in (f.left, f.right):
            label, reason = judge_hand(side, params)
            labels[label or f"unknown:{reason}"] += 1
    return {"rows": dict(res), "hand_labels": dict(labels)}


def sensitivity(rows: list[PoseObservationRecord]) -> list[dict]:
    """한 매개변수씩 바꿔 손 단위 라벨이 기준값 대비 얼마나 바뀌는지 본다."""
    base = StateParams()

    def labels(p: StateParams) -> list[str]:
        out: list[str] = []
        for r in rows:
            if not r.landmarks or not r.participant_id:
                continue
            f = compute_features(r, p)
            if f.unusable:
                out += ["unusable", "unusable"]  # 손 두 개 자리를 유지해 설정 사이를 같은 위치끼리 비교한다
                continue
            for side in (f.left, f.right):
                out.append(judge_hand(side, p)[0] or "unknown")
        return out

    ref = labels(base)
    grid = {
        "min_visibility": (0.3, 0.7),
        "high_hand_height_max": (0.30, 0.40),
        "high_hand_chin_max": (0.3, 0.5),
        "low_hand_height_min": (0.9, 1.1),
    }
    report = []
    for name, values in grid.items():
        for v in values:
            cur = labels(replace(base, **{name: v}))
            n = max(len(ref), len(cur))
            judged_ref, judged_cur = (sum(x not in ("unusable", "unknown") for x in v) for v in (ref, cur))
            same = sum(1 for a, b in zip(ref, cur) if a == b)
            report.append({"param": name, "value": v, "base": getattr(base, name), "judged_base": judged_ref, "judged": judged_cur,
                           "high_guard": sum(x == "high_guard" for x in cur), "low_guard": sum(x == "low_guard" for x in cur),
                           "same_as_base": same, "total_compared": n})
    return report


def states_summary(rows: list[PoseObservationRecord]) -> dict:
    out = judge_states(rows)
    by_label: Counter[str] = Counter()
    span = 0
    for s in out.states:
        by_label[s["label"]] += s["end_us"] - s["start_us"]
        span += s["end_us"] - s["start_us"]
    unknown = Counter()
    for u in out.unknown:
        unknown[u["reason_code"]] += u["end_us"] - u["start_us"]
    return {"state_intervals": len(out.states), "state_time_us": span, "label_time_us": dict(by_label),
            "unknown_intervals": len(out.unknown), "unknown_time_us_by_reason": dict(unknown)}


def event_policy(events: list[dict]) -> dict:
    return {"events": len(events), "by_state": dict(Counter(event_state(e) for e in events)), "labels": dict(Counter(e.get("event_label") for e in events))}


def invariants(derived: dict, events: list[dict]) -> list[str]:
    """집계·판정 불변식. 어긋난 항목 설명을 돌려준다(빈 목록이면 통과)."""
    problems: list[str] = []
    values = {a["subject_ref"]: a["value"] for a in derived["assessments"]}
    confirmed = {e["event_id"] for e in events if event_state(e) == CONFIRMED}
    if set(values) - confirmed:
        problems.append("확인되지 않은 사건에 판정이 있음")
    for a in derived["assessments"]:
        if a["value"] == "unknown" and a["status"] != "UNKNOWN":
            problems.append(f"{a['assessment_id']}: unknown인데 status가 UNKNOWN이 아님")
        if a["value"] in ("present", "absent") and a["status"] != "VALID":
            problems.append(f"{a['assessment_id']}: 판정이 있는데 status가 VALID가 아님")
    for g in derived["aggregates"]:
        refs = g["candidate_refs"]
        vs = [values.get(r) for r in refs]
        if None in vs:
            problems.append(f"{g['aggregate_id']}: 판정 없는 후보가 있음")
    return problems


def scenario6_sensitivity() -> list[dict]:
    path = ROOT / "Notion" / "02A_시나리오6_가상_데이터.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for late in (300_000, 400_000, 500_000, 600_000):
        d = derive_guard_return(doc["events"], doc["state_intervals"], video_id="v", duration_us=doc.get("duration_us", 600_000_000),
                                params=AnalysisParams(guard_late_us=late))
        vals = Counter(a["value"] for a in d["assessments"])
        out.append({"guard_late_ms": late // 1000, "present": vals["present"], "absent": vals["absent"], "unknown": vals["unknown"],
                    "invariant_problems": invariants(d, doc["events"])})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    report: dict = {"sources": {}, "scenario6_guard_late": scenario6_sensitivity()}
    for name, src in sources(args.results).items():
        rows = parse(src["poses"])
        clips = sorted({r.clip_id for r in rows if r.clip_id}) or [None]
        per_clip = {}
        for clip in clips:
            sel = [r for r in rows if r.clip_id == clip] if clip else rows
            per_clip[clip or "-"] = {"coverage": coverage(sel, StateParams()), "states": states_summary(sel), "sensitivity": sensitivity(sel)}
        events = src["events"]
        derived = derive_guard_return(events, [], video_id="v", duration_us=10**9)
        report["sources"][name] = {"events": event_policy(events), "derived": {k: len(v) for k, v in derived.items()},
                                   "invariant_problems": invariants(derived, events), "clips": per_clip}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
