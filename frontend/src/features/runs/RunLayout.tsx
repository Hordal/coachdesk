import { Link, NavLink, Navigate, Outlet, useParams, useLocation } from "react-router-dom";

import { useRun } from "../../api/hooks";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { SyntheticBadge } from "../../components/StatusBadge";
import { formatDuration } from "../../lib/format";
import { label, ROLE, RUN_STATUS, SPORT, T } from "../../strings/ko";

export function RunLayout() {
  const { runId = "" } = useParams();
  const location = useLocation();
  const isReport = location.pathname.endsWith("/report");
  const { data: run, error, isPending, refetch } = useRun(runId);

  if (isPending) return <Loading />;
  if (error || !run) {
    return (
      <>
        <p>
          <Link to="/">← {T.runsTitle}</Link>
        </p>
        <ProblemNotice error={error} onRetry={() => void refetch()} />
      </>
    );
  }

  const hasCoaching = run.dataset_kind !== "synthetic" && run.dataset_kind !== "unanalyzed";
  if(hasCoaching && location.pathname.replace(/\/$/, "") === `/runs/${runId}`) {
    return <Navigate to={`/runs/${runId}/report${location.search}`} replace/>;
  }
  return (
    <>
      <p className="breadcrumb">
        <Link to="/">{T.runsTitle}</Link> / {run.filename ?? `영상 ${run.video_id}`}
      </p>
      <div className={isReport ? "report-context" : "run-context"}>
      <h1>
        분석 결과 <span className="h1__sub">{run.filename ?? `영상 ${run.video_id}`}</span>
      </h1>
      <p className="muted">
        {label(SPORT, run.sport)} · {formatDuration(run.duration_us)} · {label(RUN_STATUS, run.status)}
      </p>
      {run.dataset_kind === "synthetic" ? (
        <p className="notice notice--hold" role="note">
          <SyntheticBadge /> {T.syntheticNotice}
        </p>
      ) : null}
      {run.superseded_by ? (
        <p className="notice notice--hold" role="note">
          이 결과는 정정되어 최신이 아닙니다. <Link to={`/runs/${run.superseded_by}`}>최신 결과 보기</Link>
        </p>
      ) : null}
      {run.parent_run_id ? (
        <p className="notice notice--muted" role="note">
          정정으로 만들어진 결과입니다. <Link to={`/runs/${run.parent_run_id}`}>정정 전 결과 보기</Link>
        </p>
      ) : null}
      {run.dataset_kind === "model" ? <p className="notice notice--hold" role="note">모델의 관측 결과와 검증 전 동작 후보입니다. 후보를 실제 발생·접촉·명중이나 정답으로 해석하지 마세요. 전문가 검수를 거치지 않았습니다.</p> : null}
      {run.dataset_kind === "manual" ? (
        <p className="notice notice--muted" role="note">
          {T.manualNotice}
        </p>
      ) : null}
      <ul className="chips" aria-label="선수">
        {run.participants.map((p) => (
          <li key={p.participant_id} className="chip">
            <strong>{p.participant_id}</strong> {label(ROLE, p.role)}
          </li>
        ))}
      </ul>

      </div>
      <nav className="tabs" aria-label="결과 화면">
        {hasCoaching ? <NavLink to={`/runs/${runId}/report`}>코칭 보고서</NavLink> : null}
        <NavLink to={hasCoaching ? `/runs/${runId}/summary` : `/runs/${runId}`} end>
          {T.tabs.summary}
        </NavLink>
        <NavLink to={`/runs/${runId}/scene`}>{T.tabs.scene}</NavLink>
        <NavLink to={`/runs/${runId}/corrections`}>{T.tabs.corrections}</NavLink>
        {hasCoaching ? <NavLink to={`/runs/${runId}/review`}>코칭 검수</NavLink> : null}
      </nav>

      <Outlet context={run} />
    </>
  );
}
