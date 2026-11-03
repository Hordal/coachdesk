import { Link } from "react-router-dom";

import type { EvidenceView, FindingView } from "../api/client";
import { CLAIM_TYPE, HOLD_REASON, label } from "../strings/ko";
import { EvidenceLink } from "./EvidenceLink";
import { FindingStatusBadge } from "./StatusBadge";

interface Props {
  runId: string;
  finding: FindingView;
  evidence: Map<string, EvidenceView>;
}

/**
 * 주장 카드. 보류된 주장은 숨기지 않고 사유와 함께 보여준다(19).
 * 문장은 서버가 만든 값을 그대로 표시하며 화면에서 숫자를 만들지 않는다.
 */
export function FindingCard({ runId, finding, evidence }: Props) {
  const aggregateRefs = finding.input_refs.filter((r) => r.startsWith("AG"));
  const otherRefs = finding.input_refs.length - aggregateRefs.length;
  const withheld = finding.status !== "valid";
  const linked = finding.evidence_refs.map((id) => evidence.get(id)).filter((v): v is EvidenceView => v !== undefined);
  const unplayable = linked.filter((v) => !v.playable).length;

  return (
    <article className={`card finding ${withheld ? "finding--withheld" : ""}`}>
      <header className="finding__header">
        <p className="finding__type">{label(CLAIM_TYPE, finding.claim_type)}</p>
        <FindingStatusBadge status={finding.status} />
      </header>

      {withheld ? (
        <p className="finding__hold">
          {finding.hold_reason ? label(HOLD_REASON, finding.hold_reason) : "근거가 부족해 제공하지 않았습니다."}
        </p>
      ) : (
        <p className="finding__text">{finding.text}</p>
      )}

      {aggregateRefs.length > 0 || otherRefs > 0 ? (
        <p className="finding__refs muted">
          근거 집계:{" "}
          {aggregateRefs.map((id, i) => (
            <span key={id}>
              {i > 0 ? ", " : ""}
              <Link to={`/runs/${runId}/aggregates/${id}`}>{id}</Link>
            </span>
          ))}
          {otherRefs > 0 ? <span>{aggregateRefs.length > 0 ? ", " : ""}관련 판정 {otherRefs}건</span> : null}
        </p>
      ) : null}

      {linked.length > 0 ? (
        <>
          <ul className="finding__evidence" aria-label="근거 장면">
            {linked.map((view) => (
              <li key={view.evidence.evidence_id}>
                <EvidenceLink runId={runId} view={view} showStatus={view.playable} />
              </li>
            ))}
          </ul>
          {unplayable > 0 ? (
            <p className="muted finding__novideo">
              {unplayable === linked.length
                ? "근거 장면은 모두 원본 영상이 없어 재생할 수 없습니다."
                : `근거 장면 중 ${unplayable}건은 원본 영상이 없어 재생할 수 없습니다.`}
            </p>
          ) : null}
        </>
      ) : null}
    </article>
  );
}
