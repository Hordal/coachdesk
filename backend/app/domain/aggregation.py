"""집계 계산(상세 설계 14의 집계 규칙, 18의 평가 기준).

판정(Assessment)에서 분자·분모·판단 불가·제외를 다시 계산한다. 저장된 집계값을
믿지 않고 판정에서 재계산한 값과 대조하는 데 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

AssessmentIndex = dict[tuple[str, str], Mapping[str, Any]]


@dataclass
class AggregateCounts:
    candidates: list[str]
    denominator: list[str]
    numerator: list[str]
    unknown: list[str]
    excluded: list[str]

    @property
    def ratio(self) -> float | None:
        if not self.denominator:
            return None
        return len(self.numerator) / len(self.denominator)


def index_assessments(
    assessments: Sequence[Mapping[str, Any]],
) -> tuple[AssessmentIndex, list[tuple[str, str]]]:
    """(subject_ref, question_code) → Assessment. 중복된 키 목록을 함께 돌려준다."""
    idx: AssessmentIndex = {}
    duplicates: list[tuple[str, str]] = []
    for a in assessments:
        key = (a.get("subject_ref"), a.get("question_code"))
        if key in idx:
            duplicates.append(key)
        idx[key] = a
    return idx, duplicates


def recompute_aggregate(agg: Mapping[str, Any], aidx: AssessmentIndex) -> AggregateCounts:
    """후보 사건의 판정에서 집계를 다시 계산한다.

    present        분자이자 분모. 부위 조건이 있으면 같은 부위만 분자,
                   부위가 unclear·미기록이면 판단 불가, 다른 부위면 분모만
    absent         분모만
    unknown        판단 불가
    not_applicable 제외. 판정이 없는 후보도 제외
    같은 ID가 후보에 여러 번 있어도 한 번만 센다.
    """
    question = agg["question_code"]
    region_cond = agg.get("contact_region_condition")
    candidates = list(dict.fromkeys(agg.get("candidate_refs", [])))
    num: list[str] = []
    den: list[str] = []
    unk: list[str] = []
    exc: list[str] = []
    for c in candidates:
        a = aidx.get((c, question))
        if a is None or a["value"] == "not_applicable":
            exc.append(c)
        elif a["value"] == "unknown":
            unk.append(c)
        elif a["value"] == "absent":
            den.append(c)
        elif region_cond is None:
            den.append(c)
            num.append(c)
        else:
            region = a.get("contact_region")
            if region in (None, "unclear"):
                unk.append(c)
            else:
                den.append(c)
                if region == region_cond:
                    num.append(c)
    return AggregateCounts(candidates, den, num, unk, exc)
