import type { Assessment } from "../api/client";
import { CONTACT_REGION, label, QUESTION, REASON } from "../strings/ko";
import { AssessmentBadge } from "./StatusBadge";

/** 판정 한 줄: 질문, 값, 접촉 부위, 사유. 판단 불가에는 사유를 함께 보여준다. */
export function AssessmentLine({ assessment, showQuestion = true }: { assessment: Assessment; showQuestion?: boolean }) {
  return (
    <span className="assessment-line">
      {showQuestion ? <span className="assessment-line__q">{label(QUESTION, assessment.question_code)}</span> : null}
      <AssessmentBadge value={assessment.value} />
      {assessment.contact_region ? (
        <span className="assessment-line__region">부위: {label(CONTACT_REGION, assessment.contact_region)}</span>
      ) : null}
      {assessment.reason_code ? (
        <span className="muted assessment-line__reason">({label(REASON, assessment.reason_code)})</span>
      ) : null}
    </span>
  );
}
