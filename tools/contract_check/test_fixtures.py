"""02A 가상 예제의 회귀 검사(상세 설계 18 ‘당장 가능한 계약 검사’).

실행: python -m unittest discover -s tools/contract_check -v   (저장소 루트에서)
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from check_contract import assessment_index, check_document, load, recompute_aggregate, Report

DOCS = Path(__file__).resolve().parents[2] / "Notion"
COUNTER = DOCS / "02A_가상_사건_데이터.json"
SCENARIO6 = DOCS / "02A_시나리오6_가상_데이터.json"


def find(items: list[dict], key: str, value: str) -> dict:
    return next(x for x in items if x[key] == value)


def recompute(doc: dict, agg_id: str):
    agg = find(doc["aggregates"], "aggregate_id", agg_id)
    return recompute_aggregate(agg, assessment_index(doc, Report("t")))


class CounterFixture(unittest.TestCase):
    """상대 반격 예제: 확인된 발차기 5, 판정 가능 4, 반격 3, 머리 접촉 2."""

    def setUp(self):
        self.doc = load(COUNTER)

    def test_contract_passes(self):
        report, _ = check_document(self.doc)
        self.assertEqual(report.errors, [])

    def test_expected_summary_matches_aggregates(self):
        s = self.doc["expected_summary"]
        ag1, ag2 = recompute(self.doc, "AG01"), recompute(self.doc, "AG02")
        self.assertEqual(s["observed_kicks"], len(ag1.candidates))
        self.assertEqual(s["counter_assessable"], len(ag1.denominator))
        self.assertEqual(s["kicks_followed_by_counter"], len(ag1.numerator))
        self.assertEqual(s["no_counter"], len(ag1.denominator) - len(ag1.numerator))
        self.assertEqual(s["unknown_followup"], len(ag1.unknown))
        self.assertEqual(s["kicks_with_counter_head_contact"], len(ag2.numerator))
        self.assertEqual(s["counter_without_contact"], len(ag2.denominator) - len(ag2.numerator))

    def test_unknown_followup_is_not_counted_as_no_counter(self):
        ag1 = recompute(self.doc, "AG01")
        self.assertIn("E10", ag1.unknown)          # X05: 가림
        self.assertNotIn("E10", ag1.denominator)
        self.assertIn("E09", ag1.denominator)      # X04: 반격 없음은 분모에 포함
        self.assertNotIn("E09", ag1.numerator)

    def test_counter_without_contact_counts_counter_only(self):
        ag1, ag2 = recompute(self.doc, "AG01"), recompute(self.doc, "AG02")
        self.assertIn("E07", ag1.numerator)        # X03: 반격 있음
        self.assertIn("E07", ag2.denominator)
        self.assertNotIn("E07", ag2.numerator)     # 접촉은 없음

    def test_unclear_region_goes_to_unknown(self):
        doc = copy.deepcopy(self.doc)
        find(doc["assessments"], "assessment_id", "AS12")["contact_region"] = "unclear"
        ag2 = recompute(doc, "AG02")
        self.assertEqual((len(ag2.numerator), len(ag2.denominator), len(ag2.unknown)), (1, 2, 1))

    def test_other_region_stays_in_denominator(self):
        doc = copy.deepcopy(self.doc)
        find(doc["assessments"], "assessment_id", "AS12")["contact_region"] = "guard"
        ag2 = recompute(doc, "AG02")
        self.assertEqual((len(ag2.numerator), len(ag2.denominator)), (1, 3))

    def test_duplicate_candidate_is_counted_once(self):
        doc = copy.deepcopy(self.doc)
        agg = find(doc["aggregates"], "aggregate_id", "AG02")
        agg["candidate_refs"] = agg["candidate_refs"] + ["E01"]
        self.assertEqual(len(recompute(doc, "AG02").candidates), 3)
        report, _ = check_document(doc)
        self.assertTrue(any("중복" in e for e in report.errors))

    def test_missing_distance_withholds_only_distance_finding(self):
        findings = {f["finding_id"]: f for f in self.doc["findings"]}
        self.assertEqual(findings["F01"]["status"], "valid")
        self.assertEqual(findings["F03"]["status"], "withheld")
        self.assertEqual(findings["F03"]["hold_reason"], "distance_unavailable")

    def test_broken_reference_is_reported(self):
        doc = copy.deepcopy(self.doc)
        find(doc["relations"], "relation_id", "R01")["object_event_id"] = "E99"
        report, _ = check_document(doc)
        self.assertTrue(any("E99" in e for e in report.errors))

    def test_unknown_without_reason_is_reported(self):
        doc = copy.deepcopy(self.doc)
        find(doc["assessments"], "assessment_id", "AS10")["reason_code"] = None
        report, _ = check_document(doc)
        self.assertTrue(any("AS10" in e and "reason_code" in e for e in report.errors))

    def test_correction_without_rule_cannot_be_valid(self):
        doc = copy.deepcopy(self.doc)
        f = find(doc["findings"], "finding_id", "F02")
        f["status"], f["hold_reason"], f["evidence_refs"] = "valid", None, ["EV01"]
        report, _ = check_document(doc)
        self.assertTrue(any("F02" in e and "rule_ref" in e for e in report.errors))


class Scenario6Fixture(unittest.TestCase):
    """시나리오 6: 크로스 6회 중 뒷손 가드 복귀 지연 4회."""

    def setUp(self):
        self.doc = load(SCENARIO6)

    def test_contract_passes(self):
        report, _ = check_document(self.doc)
        self.assertEqual(report.errors, [])

    def test_expected_summary_matches_aggregate(self):
        s, ag = self.doc["expected_summary"], recompute(self.doc, "AG01")
        self.assertEqual(s["observed_crosses"], len(ag.candidates))
        self.assertEqual(s["return_assessable"], len(ag.denominator))
        self.assertEqual(s["return_delayed"], len(ag.numerator))
        self.assertEqual(s["return_not_delayed"], len(ag.denominator) - len(ag.numerator))
        self.assertEqual(s["unknown"], len(ag.unknown))

    def test_one_unknown_cross_splits_denominator(self):
        doc = copy.deepcopy(self.doc)
        a = find(doc["assessments"], "assessment_id", "AS03")
        a.update(value="unknown", status="UNKNOWN", reason_code="occluded")
        ag = recompute(doc, "AG01")
        self.assertEqual((len(ag.denominator), len(ag.unknown), len(ag.numerator)), (5, 1, 3))
        self.assertAlmostEqual(ag.ratio, 3 / 5)

    def test_all_unknown_gives_null_ratio(self):
        doc = copy.deepcopy(self.doc)
        for a in doc["assessments"]:
            a.update(value="unknown", status="UNKNOWN", reason_code="occluded")
        ag = recompute(doc, "AG01")
        self.assertEqual(len(ag.denominator), 0)
        self.assertIsNone(ag.ratio)

    def test_missing_post_state_reference_is_reported(self):
        doc = copy.deepcopy(self.doc)
        find(doc["events"], "event_id", "E01")["post_state_refs"] = ["S99"]
        report, _ = check_document(doc)
        self.assertTrue(any("S99" in e for e in report.errors))

    def test_repeated_problem_and_correction_are_withheld(self):
        findings = {f["finding_id"]: f for f in self.doc["findings"]}
        self.assertEqual(findings["F02"]["status"], "withheld")
        self.assertEqual(findings["F03"]["status"], "withheld")


if __name__ == "__main__":
    unittest.main()
