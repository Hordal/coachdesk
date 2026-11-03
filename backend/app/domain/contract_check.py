"""데이터 계약(상세 설계 14) 검사.

결과 묶음(dict)을 읽어 참조 무결성, 시간 구간, 판정 값, 집계 정합성, 보류 규칙을
검사한다. 모델(pydantic)이 형식을 검사하고, 이 모듈이 값의 의미를 검사한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .aggregation import AggregateCounts, AssessmentIndex, index_assessments, recompute_aggregate

VALUES = {"present", "absent", "unknown", "not_applicable"}
RESULT_STATUS = {"VALID", "UNKNOWN", "UNSUPPORTED", "ERROR"}
CONTACT_REGIONS = {"head", "body", "leg", "guard", "unclear"}
FINDING_STATUS = {"valid", "withheld"}
CLAIMS_NEEDING_RULE = {"causal_hypothesis", "correction"}

# 객체 묶음 이름 → ID 필드 이름
COLLECTIONS = {
    "state_intervals": "state_id",
    "events": "event_id",
    "relations": "relation_id",
    "assessments": "assessment_id",
    "exchanges": "exchange_id",
    "evidence": "evidence_id",
    "aggregates": "aggregate_id",
    "findings": "finding_id",
}


@dataclass
class Report:
    path: str
    errors: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def load(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def index_ids(doc: dict, report: Report) -> dict[str, str]:
    """모든 객체 ID → 소속 묶음. ID 중복을 오류로 기록한다."""
    ids: dict[str, str] = {}
    for coll, key in COLLECTIONS.items():
        for obj in doc.get(coll, []):
            oid = obj.get(key)
            if oid is None:
                report.error(f"{coll}: '{key}' 없음 — {obj}")
                continue
            if oid in ids:
                report.error(f"ID 중복: {oid} ({ids[oid]}, {coll})")
            ids[oid] = coll
    for p in doc.get("participants", []):
        pid = p if isinstance(p, str) else p.get("participant_id")
        ids.setdefault(pid, "participants")
    return ids


def assessment_index(doc: dict, report: Report) -> AssessmentIndex:
    """같은 대상·질문의 중복 판정은 오류로 기록한다."""
    idx, duplicates = index_assessments(doc.get("assessments", []))
    for key in duplicates:
        report.error(f"같은 대상·질문의 판정 중복: {key}")
    return idx


def check_refs(doc: dict, ids: dict[str, str], report: Report) -> None:
    def must_exist(owner: str, ref: str, allowed: set[str] | None = None) -> None:
        if ref not in ids:
            report.error(f"{owner}: 끊어진 참조 {ref}")
        elif allowed and ids[ref] not in allowed:
            report.error(f"{owner}: {ref}는 {ids[ref]}이며 허용 묶음 {sorted(allowed)}가 아님")

    for e in doc.get("events", []):
        for r in e.get("pre_state_refs", []) + e.get("post_state_refs", []):
            must_exist(e["event_id"], r, {"state_intervals"})
        for r in (e.get("participant_id"), e.get("target_participant_id"), e.get("top_participant_id"), e.get("bottom_participant_id")):
            if r is not None:
                must_exist(e["event_id"], r, {"participants"})
        if e.get("evidence_ref") is not None:
            must_exist(e["event_id"], e["evidence_ref"], {"evidence"})
    for r in doc.get("relations", []):
        must_exist(r["relation_id"], r["subject_event_id"], {"events"})
        must_exist(r["relation_id"], r["object_event_id"], {"events"})
        if r.get("assessment_ref"):
            must_exist(r["relation_id"], r["assessment_ref"], {"assessments"})
    for a in doc.get("assessments", []):
        must_exist(a["assessment_id"], a["subject_ref"])
        for r in a.get("input_refs", []):
            must_exist(a["assessment_id"], r)
    for x in doc.get("exchanges", []):
        for r in x.get("event_refs", []):
            must_exist(x["exchange_id"], r, {"events"})
        for r in x.get("relation_refs", []):
            must_exist(x["exchange_id"], r, {"relations"})
        for r in x.get("evidence_refs", []):
            must_exist(x["exchange_id"], r, {"evidence"})
    for g in doc.get("aggregates", []):
        for key in ("candidate_refs", "denominator_refs", "numerator_refs", "unknown_refs", "excluded_refs"):
            for r in g.get(key, []):
                must_exist(f"{g['aggregate_id']}.{key}", r)
    for f in doc.get("findings", []):
        for r in f.get("input_refs", []):
            must_exist(f["finding_id"], r)
        for r in f.get("evidence_refs", []):
            must_exist(f["finding_id"], r, {"evidence"})


def check_intervals(doc: dict, report: Report) -> None:
    duration = doc.get("duration_us")
    for coll, key in COLLECTIONS.items():
        for obj in doc.get(coll, []):
            s_key, e_key = ("start_timestamp_us", "end_timestamp_us") if coll == "events" else ("start_us", "end_us")
            if s_key not in obj and e_key not in obj:
                continue
            s, e = obj.get(s_key), obj.get(e_key)
            oid = obj.get(key)
            if not isinstance(s, int) or not isinstance(e, int):
                report.error(f"{oid}: 시각은 정수 마이크로초여야 함 ({s}, {e})")
                continue
            if not s < e:
                report.error(f"{oid}: 구간 [start, end)에서 start < end 위반 ({s}, {e})")
            if s < 0 or (duration is not None and e > duration):
                report.error(f"{oid}: 영상 길이 범위 밖 ({s}, {e}, duration={duration})")
    for e in doc.get("events", []):
        for name, t in (e.get("phase_marks") or {}).items():
            if not e["start_timestamp_us"] <= t < e["end_timestamp_us"]:
                report.error(f"{e['event_id']}: phase_marks.{name}={t}가 사건 구간 밖")


def check_assessments(doc: dict, report: Report) -> None:
    for a in doc.get("assessments", []):
        aid = a["assessment_id"]
        v, ds = a.get("value"), a.get("status")
        if v not in VALUES:
            report.error(f"{aid}: value '{v}'는 {sorted(VALUES)} 중 하나여야 함")
        if ds not in RESULT_STATUS:
            report.error(f"{aid}: status '{ds}'는 {sorted(RESULT_STATUS)} 중 하나여야 함")
        if v in {"present", "absent"} and ds != "VALID":
            report.error(f"{aid}: {v} 판정은 status=VALID여야 함 (현재 {ds})")
        if v in {"unknown", "not_applicable"} and not a.get("reason_code"):
            report.error(f"{aid}: {v} 판정에는 reason_code가 필요함")
        region = a.get("contact_region")
        if region is not None:
            if region not in CONTACT_REGIONS:
                report.error(f"{aid}: contact_region '{region}'는 {sorted(CONTACT_REGIONS)} 중 하나여야 함")
            if v != "present":
                report.error(f"{aid}: contact_region은 접촉 있음(present) 판정에만 기록")


def resolve_condition_set(doc: dict, ref: str) -> set[str] | None:
    """'AG01.numerator_refs' 같은 조건 참조를 ID 집합으로 바꾼다."""
    if "." not in ref:
        return None
    agg_id, key = ref.split(".", 1)
    for g in doc.get("aggregates", []):
        if g["aggregate_id"] == agg_id:
            return set(g.get(key, []))
    return None


def check_aggregates(doc: dict, aidx: AssessmentIndex, report: Report) -> dict[str, AggregateCounts]:
    computed: dict[str, AggregateCounts] = {}
    for g in doc.get("aggregates", []):
        gid = g["aggregate_id"]
        keys = ("candidate_refs", "denominator_refs", "numerator_refs", "unknown_refs", "excluded_refs")
        sets = {k: g.get(k, []) for k in keys}
        for k, v in sets.items():
            if len(v) != len(set(v)):
                report.error(f"{gid}.{k}: 같은 ID가 중복됨")
        cand, den, num, unk, exc = (set(sets[k]) for k in keys)
        if not num <= den:
            report.error(f"{gid}: numerator_refs가 denominator_refs의 부분집합이 아님 ({sorted(num - den)})")
        for a_name, a_set, b_name, b_set in (
            ("denominator", den, "unknown", unk),
            ("denominator", den, "excluded", exc),
            ("unknown", unk, "excluded", exc),
        ):
            if a_set & b_set:
                report.error(f"{gid}: {a_name}와 {b_name}가 겹침 ({sorted(a_set & b_set)})")
        if den | unk | exc != cand:
            report.error(f"{gid}: denominator·unknown·excluded의 합이 candidate_refs와 다름")
        for cref in g.get("condition_refs", []):
            cond = resolve_condition_set(doc, cref)
            if cond is not None and cond != cand:
                report.error(f"{gid}: 조건 {cref}의 집합과 candidate_refs가 다름")

        counts = g.get("counts", {})
        expected_counts = {
            "candidates": len(cand),
            "denominator": len(den),
            "numerator": len(num),
            "unknown": len(unk),
            "excluded": len(exc),
        }
        for k, v in expected_counts.items():
            if counts.get(k) != v:
                report.error(f"{gid}: counts.{k}={counts.get(k)}이지만 참조 ID 수는 {v}")

        ratio = g.get("ratio")
        if not den:
            if ratio is not None:
                report.error(f"{gid}: 분모 0이면 ratio=null이어야 함")
        elif ratio is None or abs(ratio - len(num) / len(den)) > 1e-9:
            report.error(f"{gid}: ratio={ratio}이지만 {len(num)}/{len(den)}")

        re = recompute_aggregate(g, aidx)
        computed[gid] = re
        for name, mine, theirs in (
            ("numerator", re.numerator, num),
            ("denominator", re.denominator, den),
            ("unknown", re.unknown, unk),
            ("excluded", re.excluded, exc),
        ):
            if set(mine) != theirs:
                report.error(f"{gid}: 판정에서 다시 계산한 {name}={sorted(mine)}가 기록값 {sorted(theirs)}와 다름")
    return computed


def check_findings(doc: dict, report: Report) -> None:
    for f in doc.get("findings", []):
        fid, status = f["finding_id"], f.get("status")
        if status not in FINDING_STATUS:
            report.error(f"{fid}: status '{status}'는 {sorted(FINDING_STATUS)} 중 하나여야 함")
        if status == "withheld" and not f.get("hold_reason"):
            report.error(f"{fid}: 보류(withheld)에는 hold_reason이 필요함")
        if status == "valid" and f.get("claim_type") in CLAIMS_NEEDING_RULE and not f.get("rule_ref"):
            report.error(f"{fid}: {f['claim_type']} 주장은 검토된 규칙(rule_ref) 없이 valid일 수 없음")
        if status == "valid" and not f.get("evidence_refs"):
            report.error(f"{fid}: valid 주장에는 근거 장면(evidence_refs)이 필요함")


def check_document(doc: dict, path: str = "<memory>") -> tuple[Report, dict[str, AggregateCounts]]:
    report = Report(path)
    for key in ("schema_version", "dataset_kind", "annotation_source", "video_id", "duration_us"):
        if key not in doc:
            report.error(f"최상위 필드 '{key}' 없음")
    ids = index_ids(doc, report)
    aidx = assessment_index(doc, report)
    check_refs(doc, ids, report)
    check_intervals(doc, report)
    check_assessments(doc, report)
    computed = check_aggregates(doc, aidx, report)
    check_findings(doc, report)
    return report, computed


def main(argv: list[str]) -> int:
    if not argv:
        print("사용법: python check_contract.py <결과묶음.json> [...]")
        return 2
    failed = 0
    for p in argv:
        report, computed = check_document(load(p), p)
        print(f"== {p}")
        for gid, c in computed.items():
            ratio = "null" if c.ratio is None else f"{len(c.numerator)}/{len(c.denominator)}"
            print(
                f"  {gid}: 후보 {len(c.candidates)}, 분모 {len(c.denominator)}, 분자 {len(c.numerator)}, "
                f"판단 불가 {len(c.unknown)}, 제외 {len(c.excluded)}, 비율 {ratio}"
            )
        if report.ok:
            print("  결과: 통과")
        else:
            failed += 1
            print(f"  결과: 오류 {len(report.errors)}건")
            for e in report.errors:
                print(f"   - {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
