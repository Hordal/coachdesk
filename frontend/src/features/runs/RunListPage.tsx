import { Link } from "react-router-dom";

import { useRuns, useVideos } from "../../api/hooks";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { SyntheticBadge } from "../../components/StatusBadge";
import { formatDuration, formatPercent } from "../../lib/format";
import { DATASET_KIND, label, RUN_STATUS, SPORT, T, VIDEO_STATUS } from "../../strings/ko";

export function RunListPage() {
  const { data, error, isPending, refetch } = useRuns();

  const videos = useVideos();

  return (
    <>
      <h1>내 영상</h1>
      <p>
        <Link to="/upload">영상 올리기</Link>
      </p>
      {videos.data && videos.data.length === 0 ? <p className="muted">올린 영상이 없습니다.</p> : null}
      <ul className="run-list">
        {videos.data?.map((v) => (
          <li key={v.video_id} className="card">
            <h2 className="run-list__title">
              <Link to={`/videos/${v.video_id}`}>{v.filename}</Link>
            </h2>
            <p className="muted">
              {label(SPORT, v.sport)} · {label(VIDEO_STATUS, v.status)}
              {v.job && v.status === "processing" ? ` · ${formatPercent(v.job.progress)}` : ""}
            </p>
          </li>
        ))}
      </ul>

      <h2>{T.runsTitle}</h2>
      {isPending ? <Loading /> : null}
      {error ? <ProblemNotice error={error} onRetry={() => void refetch()} /> : null}
      {data && data.length === 0 ? <p className="muted">{T.runsEmpty}</p> : null}
      <ul className="run-list">
        {data?.map((run) => (
          <li key={run.run_id} className="card">
            <h2 className="run-list__title">
              <Link to={`/runs/${run.run_id}`}>{run.filename ?? `영상 ${run.video_id}`}</Link>
            </h2>
            <p className="muted">
              {label(SPORT, run.sport)} · {formatDuration(run.duration_us)} · {label(RUN_STATUS, run.status)}
              {run.dataset_kind === "synthetic" ? null : ` · ${label(DATASET_KIND, run.dataset_kind)}`}
            </p>
            <p>
              {run.dataset_kind === "synthetic" ? <SyntheticBadge /> : null}{" "}
              <span className="muted">
                사건 {run.counts.events ?? 0}건 · 집계 {run.counts.aggregates ?? 0}건 · 주장 {run.counts.findings ?? 0}건
              </span>
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}
