import { AnalysisWorkflow, useWorkspaceRole } from "../workspace/Workflow";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { JobView, VideoSummary } from "../../api/client";
import { useCancelJob, useDeleteVideo, useVideoSummary } from "../../api/hooks";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { formatBytes, formatDuration, formatPercent, formatTime } from "../../lib/format";
import { label, MEDIA_STATE, REASON, SPORT, STAGE_STATUS, UPLOAD_ERROR, VIDEO_STATUS } from "../../strings/ko";
import { RetryVideo, SessionEditor, RecordHeading } from "../workspace/CoachWorkspace";

// 삭제는 확인 단계를 거친다. 파일만 지우면 결과 요약은 남고, 전부 지우면 결과도 사라진다.
function DeleteSection({ video }: { video: VideoSummary }) {
  const navigate = useNavigate();
  const readOnly=useWorkspaceRole()==="viewer";
  const del = useDeleteVideo(video.video_id);
  const [confirm, setConfirm] = useState<"media" | "all" | null>(null);
  const [pending, setPending] = useState(false);
  const busy = video.status === "processing";

  function run(scope: "media" | "all") {
    del.mutate(scope, {
      onSuccess: (result) => {
        setConfirm(null);
        if (scope === "all") {
          if ((result as { status: string }).status === "deleted") void navigate("/");
          else setPending(true);
        }
      },
    });
  }

  if (readOnly) return <p className="muted">열람 전용 권한으로는 영상을 삭제할 수 없습니다.</p>;

  if (pending) {
    return (
      <p className="notice notice--muted" role="status">
        영상과 결과를 지우는 중입니다. 정리가 끝나기 전에도 더 이상 열람할 수 없으며, 완료되면 목록에서 사라집니다. <Link to="/">목록으로</Link>
      </p>
    );
  }

  return (
    <section className="card" aria-labelledby="delete-title">
      <h2 id="delete-title">삭제</h2>
      <p className="muted">
        {label(MEDIA_STATE, video.media_state)}
        {video.media_expires_at ? ` · ${video.media_expires_at.slice(0, 10)}에 자동 삭제 예정` : ""}
      </p>
      {confirm === null ? (
        <p>
          <button type="button" disabled={busy || video.media_state !== "present"} onClick={() => setConfirm("media")}>
            원본 영상만 삭제
          </button>{" "}
          <button type="button" onClick={() => setConfirm("all")}>
            영상과 결과 모두 삭제
          </button>
        </p>
      ) : (
        <div role="alertdialog" aria-label="삭제 확인">
          <p>
            {confirm === "media"
              ? "원본과 재생용 영상을 삭제합니다. 요약과 수치는 남지만 근거 장면을 다시 재생할 수 없고, 영상을 다시 올리기 전에는 재계산할 수 없습니다."
              : "영상과 모든 결과, 정정과 신고 기록을 삭제합니다. 되돌릴 수 없습니다."}
          </p>
          <p>
            <button type="button" disabled={del.isPending} onClick={() => run(confirm)}>
              삭제
            </button>{" "}
            <button type="button" onClick={() => setConfirm(null)}>
              취소
            </button>
          </p>
        </div>
      )}
      {del.error ? <ProblemNotice error={del.error} /> : null}
      {busy ? <p className="muted">처리 중인 영상은 처리를 취소하거나 끝난 뒤 원본만 삭제할 수 있습니다. 전체 삭제는 처리를 취소하고 진행합니다.</p> : null}
    </section>
  );
}

function StageTable({ job }: { job: JobView }) {
  return (
    <table className="stage-table">
      <caption className="sr-only">처리 단계</caption>
      <thead>
        <tr>
          <th scope="col">단계</th>
          <th scope="col">상태</th>
          <th scope="col">진행</th>
          <th scope="col">내용</th>
        </tr>
      </thead>
      <tbody>
        {job.stages.map((s) => (
          <tr key={s.name}>
            <th scope="row">{s.label}</th>
            <td>{label(STAGE_STATUS, s.status)}</td>
            <td>{s.status === "unsupported" ? "" : formatPercent(s.progress)}</td>
            <td className="muted">{s.message ?? (s.reason_code ? label(REASON, s.reason_code) : "")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobError({ job }: { job: JobView }) {
  if (!job.error) return null;
  const e = job.error;
  return (
    <div className="notice notice--error" role="alert">
      <p>
        <strong>{UPLOAD_ERROR[e.code] ?? e.title}</strong>
      </p>
      {e.at_us !== null ? <p className="muted">영상 {formatTime(e.at_us)} 부근에서 읽을 수 없었습니다.</p> : null}
      <p className="muted">
        자동 시도 {job.attempts} / {job.max_attempts}회
        {e.trace_id ? ` · 문의 시 확인 번호: ${e.trace_id}` : ""}
      </p>
    </div>
  );
}

/** 영상 준비 진행 화면. 완료 예정 시간은 보여주지 않고 단계별 진행 비율만 보여준다. */
export function VideoPage() {
  const { videoId = "" } = useParams();
  const { data: video, error, isPending, refetch } = useVideoSummary(videoId);
  const cancel = useCancelJob(videoId);

  if (isPending) return <Loading />;
  if (error || !video) return <ProblemNotice error={error} onRetry={() => void refetch()} />;

  const job = video.job;
  const active = job !== null && (job.status === "queued" || job.status === "running");

  return (
    <>
      <p className="breadcrumb"><Link to="/">경기·훈련 기록</Link> / 기록 상세</p>
      <RecordHeading videoId={videoId} fallback={video.filename} />
      <p className="muted">
        {label(SPORT, video.sport)} · {formatBytes(video.size_bytes)}
        {video.duration_us !== null ? ` · ${formatDuration(video.duration_us)}` : ""}
        {video.width !== null && video.height !== null ? ` · ${video.width}×${video.height}` : ""}
      </p>
      <p>
        상태: <strong>{label(VIDEO_STATUS, video.status)}</strong>
      </p>
      <SessionEditor videoId={videoId} />
      {video.status === "ready" && video.media_state === "present" ? <AnalysisWorkflow videoId={videoId} runId={video.run_id} /> : null}
      {video.status === "failed" || video.status === "cancelled" ? <RetryVideo videoId={videoId} /> : null}

      {job ? (
        <details className="card process-details" open={active || video.status === "failed"}>
          <summary>영상 준비 {active ? "진행 중" : "처리 내역"}</summary>
          <h2 id="job-progress">처리 진행</h2>
          <p role="status">
            <progress max={1} value={job.progress} aria-label="전체 진행률" /> {formatPercent(job.progress)}
            {job.status === "queued" ? " · 대기 중" : ""}
            {job.cancel_requested && active ? " · 취소하는 중" : ""}
          </p>
          <StageTable job={job} />
          {active ? (
            <p>
              <button type="button" disabled={job.cancel_requested || cancel.isPending} onClick={() => cancel.mutate(job.job_id)}>
                처리 취소
              </button>
            </p>
          ) : null}
          {cancel.error ? <ProblemNotice error={cancel.error} /> : null}
        </details>
      ) : null}

      {job ? <JobError job={job} /> : null}

      {video.status === "cancelled" ? <p className="notice notice--muted">처리를 취소했습니다. 영상은 다시 올릴 수 있습니다.</p> : null}

      <details className="card process-details"><summary>추가 도구·기록 관리</summary>
        <p><Link to={`/videos/${video.video_id}/import`}>사건·판정 수동 가져오기</Link></p>
        <DeleteSection video={video} />
      </details>



      <p>
        <Link to="/">목록으로</Link>
      </p>
    </>
  );
}
