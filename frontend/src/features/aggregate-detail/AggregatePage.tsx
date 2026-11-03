import { Link, useParams } from "react-router-dom";

import type { MemberView } from "../../api/client";
import { useAggregate } from "../../api/hooks";
import { AssessmentLine } from "../../components/AssessmentLine";
import { CountBreakdown } from "../../components/CountBreakdown";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { formatRange } from "../../lib/format";
import { CONTACT_REGION, COUNTING_UNIT, EVENT_TYPE, label, QUESTION } from "../../strings/ko";

const GROUPS = [
  { key: "numerator", title: "해당 결과 발생" },
  { key: "denominator", title: "판정 가능한 전체(해당 결과 포함)" },
  { key: "unknown", title: "판단 불가" },
  { key: "excluded", title: "제외" },
] as const;

function conditionText(ref: string): string {
  const match = /^(AG\d+)\.numerator_refs$/.exec(ref);
  return match ? `${match[1]}에서 해당 결과가 발생한 사례만 대상` : ref;
}

export function AggregatePage() {
  const { runId = "", aggregateId = "" } = useParams();
  const { data, error, isPending, refetch } = useAggregate(runId, aggregateId);

  if (isPending) return <Loading />;
  if (error || !data) return <ProblemNotice error={error} onRetry={() => void refetch()} />;

  const { aggregate, members } = data;
  const row = (m: MemberView) => (
    <li key={m.event.event_id}>
      <Link to={`/runs/${runId}/events/${m.event.event_id}`}>
        {label(EVENT_TYPE, m.event.event_label)} {m.event.event_id}
      </Link>{" "}
      <span className="muted">{formatRange(m.event.start_timestamp_us, m.event.end_timestamp_us)}</span>
      <div>{m.assessment ? <AssessmentLine assessment={m.assessment} showQuestion={false} /> : <span className="muted">판정 기록 없음</span>}</div>
    </li>
  );

  return (
    <>
      <p>
        <Link to={`/runs/${runId}`}>← 요약</Link>
      </p>
      <h2>집계 {aggregate.aggregate_id}</h2>
      {aggregate.description ? <p>{aggregate.description}</p> : null}
      <dl className="meta">
        <div>
          <dt>세는 단위</dt>
          <dd>{label(COUNTING_UNIT, aggregate.counting_unit)}</dd>
        </div>
        <div>
          <dt>질문</dt>
          <dd>{label(QUESTION, aggregate.question_code)}</dd>
        </div>
        <div>
          <dt>대상 선수</dt>
          <dd>{aggregate.scope.participant_id}</dd>
        </div>
        {aggregate.contact_region_condition ? (
          <div>
            <dt>접촉 부위 조건</dt>
            <dd>{label(CONTACT_REGION, aggregate.contact_region_condition)}</dd>
          </div>
        ) : null}
        {aggregate.condition_refs.map((c) => (
          <div key={c}>
            <dt>대상 조건</dt>
            <dd>{conditionText(c)}</dd>
          </div>
        ))}
      </dl>

      <CountBreakdown counts={aggregate.counts} ratio={aggregate.ratio} />

      {GROUPS.map(({ key, title }) => {
        const list = members[key] ?? [];
        return (
          <section key={key} aria-labelledby={`grp-${key}`}>
            <h3 id={`grp-${key}`}>
              {title} <span className="muted">({list.length})</span>
            </h3>
            {list.length === 0 ? <p className="muted">해당하는 사례가 없습니다.</p> : <ul className="member-list">{list.map(row)}</ul>}
          </section>
        );
      })}
    </>
  );
}
