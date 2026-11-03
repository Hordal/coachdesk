import { Link, useOutletContext, useParams } from "react-router-dom";

import type { RunSummary } from "../../api/client";
import { useEvent, useEvidenceList } from "../../api/hooks";
import { AssessmentLine } from "../../components/AssessmentLine";
import { EvidenceLink } from "../../components/EvidenceLink";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { participantName } from "../../components/TimelineTrack";
import { formatRange } from "../../lib/format";
import { EVENT_STATUS, EVENT_TYPE, FINISH_CONTEXT, GROUND_RELATION, label, QUESTION, RELATION, TAKEDOWN_OUTCOME } from "../../strings/ko";

export function EventPage() {
  const { runId = "", eventId = "" } = useParams();
  const run = useOutletContext<RunSummary>();
  const { data, error, isPending, refetch } = useEvent(runId, eventId);
  const evidenceList = useEvidenceList(runId);

  if (isPending) return <Loading />;
  if (error || !data) return <ProblemNotice error={error} onRetry={() => void refetch()} />;

  const { event, assessments, relations, exchanges, evidence } = data;
  const who = (id: string | null | undefined) => participantName(id, run.participants) || "—";
  const evidenceViews = (evidenceList.data ?? []).filter((v) => evidence.some((e) => e.evidence_id === v.evidence.evidence_id));

  return (
    <>
      <p>
        <Link to={`/runs/${runId}`}>← 요약</Link>
      </p>
      <h2>
        {label(EVENT_TYPE, event.event_label)} <span className="h1__sub">{event.event_id}</span>
      </h2>
      <dl className="meta">
        <div>
          <dt>시각</dt>
          <dd>{formatRange(event.start_timestamp_us, event.end_timestamp_us)}</dd>
        </div>
        <div>
          <dt>수행 선수</dt>
          <dd>{who(event.participant_id)}</dd>
        </div>
        <div>
          <dt>대상 선수</dt>
          <dd>{who(event.target_participant_id)}</dd>
        </div>
        {event.outcome ? (
          <div>
            <dt>결과</dt>
            <dd>{label(TAKEDOWN_OUTCOME, event.outcome)}</dd>
          </div>
        ) : null}
        {event.relation ? (
          <div>
            <dt>그라운드 관계</dt>
            <dd>{label(GROUND_RELATION, event.relation)}</dd>
          </div>
        ) : null}
        {event.top_participant_id || event.bottom_participant_id ? (
          <div>
            <dt>위 · 아래</dt>
            <dd>
              {who(event.top_participant_id)} · {who(event.bottom_participant_id)}
            </dd>
          </div>
        ) : null}
        {event.finish_context ? (
          <div>
            <dt>종료 맥락</dt>
            <dd>{label(FINISH_CONTEXT, event.finish_context)}</dd>
          </div>
        ) : null}
        {event.source_surface || event.target_surface ? (
          <div>
            <dt>접촉면</dt>
            <dd>
              {event.source_surface ?? "—"} → {event.target_surface ?? "—"}
            </dd>
          </div>
        ) : null}
        <div>
          <dt>상태</dt>
          <dd>
            {label(EVENT_STATUS, event.status)}
            {event.verified ? " · 검증됨" : " · 검증 전"}
          </dd>
        </div>
        {event.arm || event.track_id != null ? (
          <div>
            <dt>팔 · 추적 번호</dt>
            <dd>
              {event.arm ?? "—"} · {event.track_id ?? "—"}
            </dd>
          </div>
        ) : null}
      </dl>

      {event.status === "CANDIDATE" ? (
        <p className="notice">검출 모델이 낸 후보입니다. 확인하기 전에는 타격이나 접촉으로 단정하지 않으며, 결과는 판단 불가로 둡니다.</p>
      ) : null}
      {event.evidence.length > 0 ? (
        <>
          <h3>근거 측정</h3>
          <ul className="member-list">
            {event.evidence.map((m, i) => (
              <li key={`${m.measure}-${i}`}>
                {m.measure}: {String(m.value)}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {event.limitations.length > 0 || event.reason_codes.length > 0 ? (
        <p className="muted">
          {event.limitations.length > 0 ? `한계: ${event.limitations.join(", ")}` : ""}
          {event.limitations.length > 0 && event.reason_codes.length > 0 ? " · " : ""}
          {event.reason_codes.length > 0 ? `사유: ${event.reason_codes.join(", ")}` : ""}
        </p>
      ) : null}

      {event.status === "UNKNOWN" ? (
        <p className="notice">이 사건이 있었는지, 또는 그 구간을 신뢰성 있게 확정하지 못했습니다. 집계에서 성공이나 실패로 세지 않습니다.</p>
      ) : null}

      <h3>판정</h3>
      {assessments.length === 0 ? (
        <p className="muted">기록된 판정이 없습니다.</p>
      ) : (
        <ul className="member-list">
          {assessments.map((a) => (
            <li key={a.assessment_id}>
              {a.subject_ref !== event.event_id ? <span className="muted">{a.subject_ref} · </span> : null}
              <AssessmentLine assessment={a} />
              <span className="muted"> · {label(QUESTION, a.question_code)}</span>
            </li>
          ))}
        </ul>
      )}

      <h3>관계</h3>
      {relations.length === 0 ? (
        <p className="muted">연결된 관계가 없습니다.</p>
      ) : (
        <ul className="member-list">
          {relations.map((r) => {
            const other = r.subject_event_id === event.event_id ? r.object_event_id : r.subject_event_id;
            const direction = r.subject_event_id === event.event_id ? "→" : "←";
            return (
              <li key={r.relation_id}>
                {label(RELATION, r.relation)} {direction}{" "}
                <Link to={`/runs/${runId}/events/${other}`}>{other}</Link>
                {r.basis ? <span className="muted"> · 근거: {r.basis}</span> : null}
              </li>
            );
          })}
        </ul>
      )}

      <h3>공방</h3>
      {exchanges.length === 0 ? (
        <p className="muted">속한 공방이 없습니다.</p>
      ) : (
        <ul className="member-list">
          {exchanges.map((x) => (
            <li key={x.exchange_id}>
              {x.exchange_id} <span className="muted">{formatRange(x.start_us, x.end_us)}</span>
            </li>
          ))}
        </ul>
      )}

      <h3>근거 장면</h3>
      {evidenceViews.length === 0 ? (
        <p className="muted">연결된 근거 장면이 없습니다.</p>
      ) : (
        <ul className="evidence-list">
          {evidenceViews.map((v) => (
            <li key={v.evidence.evidence_id}>
              <EvidenceLink runId={runId} view={v} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
