import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import type { EvidenceView, FindingView } from "../../api/client";
import { useEvidenceList, useFindings, useRun, usePlayback } from "../../api/hooks";
import { ObservationSummary } from "../../components/ObservationSummary";
import { EvidenceLink, ExpiredNotice } from "../../components/EvidenceLink";
import { FindingCard } from "../../components/FindingCard";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { T } from "../../strings/ko";

/** 관찰·집계, 원인 가설, 교정 방향을 별도 영역으로 나눈다(19). */
export function groupFindings(findings: FindingView[]) {
  return {
    observation: findings.filter((f) => f.claim_type === "observation" || f.claim_type === "aggregate"),
    cause: findings.filter((f) => f.claim_type === "causal_hypothesis"),
    correction: findings.filter((f) => f.claim_type === "correction"),
  };
}

export function SummaryPage() {
  const { runId = "" } = useParams();
  const findings = useFindings(runId);
  const evidence = useEvidenceList(runId);
  const run = useRun(runId);
  const playback = usePlayback(run.data?.video_id ?? "", !!run.data && evidence.data?.length === 0);

  const evidenceById = useMemo(
    () => new Map<string, EvidenceView>((evidence.data ?? []).map((v) => [v.evidence.evidence_id, v])),
    [evidence.data],
  );

  if (findings.isPending || evidence.isPending) return <Loading />;
  if (findings.error) return <ProblemNotice error={findings.error} onRetry={() => void findings.refetch()} />;
  if (evidence.error) return <ProblemNotice error={evidence.error} onRetry={() => void evidence.refetch()} />;

  const groups = groupFindings(findings.data ?? []);
  const hasCorrection = groups.correction.some((f) => f.status === "valid");
  const evidenceList = evidence.data ?? [];
  const anyPlayable = evidenceList.some((v) => v.playable);

  const card = (f: FindingView) => <FindingCard key={f.finding_id} runId={runId} finding={f} evidence={evidenceById} />;

  return (
    <>
      {run.data?.dataset_kind === "model" ? <ObservationSummary runId={runId} /> : null}
      {run.data?.dataset_kind !== "model" || groups.observation.length > 0 ? <section aria-labelledby="sec-observation">
        <h2 id="sec-observation">{T.sections.observation}</h2>
        {groups.observation.length === 0 ? <p className="muted">제공한 관찰·집계가 없습니다.</p> : groups.observation.map(card)}
      </section> : null}

      {run.data?.dataset_kind !== "model" || groups.cause.length > 0 ? <section aria-labelledby="sec-cause">
        <h2 id="sec-cause">{T.sections.cause}</h2>
        {groups.cause.length === 0 ? <p className="muted">{T.noCause}</p> : groups.cause.map(card)}
      </section> : null}

      {run.data?.dataset_kind !== "model" || groups.correction.length > 0 ? <section aria-labelledby="sec-correction">
        <h2 id="sec-correction">{T.sections.correction}</h2>
        {!hasCorrection ? <p className="notice notice--muted">{T.noCorrection}</p> : null}
        {groups.correction.map(card)}
      </section> : null}

      <section aria-labelledby="sec-evidence">
        <h2 id="sec-evidence">{T.sections.evidence}</h2>
        {evidenceList.length === 0 ? <p>동작 후보에 연결된 근거 구간이 없습니다. {playback.data?.playable ? <Link to={`/runs/${runId}/scene`}>원본 영상은 장면 복기에서 볼 수 있습니다 →</Link> : playback.isPending ? "영상 접근 상태를 확인하고 있습니다." : playback.error ? "영상 접근 상태를 확인하지 못했습니다. 장면 복기에서 다시 확인하세요." : <Link to={`/runs/${runId}/scene`}>장면 복기에서 영상 접근 상태 확인 →</Link>}</p> : !anyPlayable ? <ExpiredNotice reasonCode={evidenceList[0]?.reason_code} /> : null}
        <ul className="evidence-list">
          {evidenceList.map((v) => (
            <li key={v.evidence.evidence_id}>
              <span className="muted">{v.evidence.evidence_id}</span> <EvidenceLink runId={runId} view={v} />
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
