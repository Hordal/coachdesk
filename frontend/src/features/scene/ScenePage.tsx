import { useCallback, useEffect, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";

import type { Event, PoseView, RunSummary, StateInterval } from "../../api/client";
import { useEvidenceList, useFrames, usePlayback, usePoses, useTimeline, useVideoInfo } from "../../api/hooks";
import { AssessmentLine } from "../../components/AssessmentLine";
import { ExpiredNotice } from "../../components/EvidenceLink";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { participantName, TimelineTrack } from "../../components/TimelineTrack";
import { formatRange } from "../../lib/format";
import { withinFrameTableLimit } from "../../player/frames";
import { ScenePlayer } from "../../player/ScenePlayer";
import { BODY_PART, EVENT_TYPE, label, STATE_CATEGORY, STATE_LABEL } from "../../strings/ko";

const FALLBACK_SPAN_US = 30_000_000;
const FRAME_TABLE_MARGIN_US = 1_000_000;
// 영상 접근 주소(서명 주소)가 만료되기 전에 갱신한다. 서버 기본 유효 시간은 5분이다.
const URL_REFRESH_MS = 240_000;

const NO_POSE_TOO_LONG: PoseView = {
  available: false,
  reason_code: "range_too_long",
  landmark_set_id: null,
  coordinate_space_id: null,
  width: null,
  height: null,
  connections: [],
  samples: [],
};

interface Range {
  from: number;
  to: number;
}

function intParam(value: string | null): number | undefined {
  if (value === null) return undefined;
  const n = Number(value);
  return Number.isInteger(n) && n >= 0 ? n : undefined;
}

/** 지금 표시된 프레임 시각에 진행 중인 사건과 상태. 구간은 [시작, 끝). */
export function activeAt(timeUs: number | null, events: Event[], states: StateInterval[]) {
  if (timeUs === null) return { events: [], states: [] };
  return {
    events: events.filter((e) => e.start_timestamp_us <= timeUs && timeUs < e.end_timestamp_us),
    states: states.filter((s) => s.start_us <= timeUs && timeUs < s.end_us),
  };
}

/** 근거 구간의 영상, 관절 오버레이, 상태·사건 타임라인을 같은 시각 기준으로 보여준다. */
export function ScenePage() {
  const run = useOutletContext<RunSummary>();
  const [params, setParams] = useSearchParams();
  const evidence = useEvidenceList(run.run_id, { refreshMs: URL_REFRESH_MS });

  const list = evidence.data ?? [];
  const first = list[0]?.evidence;
  const requestedFrom = intParam(params.get("from_us"));
  const requestedTo = intParam(params.get("to_us"));

  let range: Range | null = null;
  if (requestedFrom !== undefined && requestedTo !== undefined && requestedFrom < requestedTo) {
    range = { from: requestedFrom, to: requestedTo };
  } else if (!evidence.isPending) {
    range = first
      ? { from: first.start_us, to: first.end_us }
      : { from: 0, to: Math.min(run.duration_us, FALLBACK_SPAN_US) };
  }
  const tableOk = range !== null && withinFrameTableLimit(range.from, range.to);

  // 영상 접근 주소: 처음 받은 값을 유지하고, 재생 오류가 나면 새 값으로 바꾼다.
  const evidenceUrl = list.find((v) => v.playable)?.access_url ?? null;
  // 분석 전 실행처럼 근거 구간이 없으면 영상 전체의 재생 주소를 따로 받는다.
  const playback = usePlayback(run.video_id, !evidence.isPending && list.length === 0, URL_REFRESH_MS);
  const latestUrl = evidenceUrl ?? (list.length === 0 ? (playback.data?.access_url ?? null) : null);
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (src === null && latestUrl) setSrc(latestUrl);
  }, [src, latestUrl]);
  const { refetch: refetchEvidence } = evidence;
  const { refetch: refetchPlayback } = playback;
  const onSourceError = useCallback(() => {
    void refetchEvidence().then((r) => {
      const url = r.data?.find((v) => v.playable)?.access_url;
      if (url) setSrc(url);
      else if (r.data?.length === 0) {
        void refetchPlayback().then((p) => {
          if (p.data?.access_url) setSrc(p.data.access_url);
        });
      }
    });
  }, [refetchEvidence, refetchPlayback]);

  const video = useVideoInfo(run.video_id, src !== null);
  // 브라우저가 구간 시작 프레임을 건너뛰고 직전 프레임을 보여줄 수 있으므로 앞쪽으로 1초 더 받는다.
  const frames = useFrames(run.video_id, range ? Math.max(range.from - FRAME_TABLE_MARGIN_US, 0) : undefined, range?.to, src !== null && tableOk);
  const poses = usePoses(run.run_id, range?.from, range?.to, src !== null && tableOk);
  const timeline = useTimeline(run.run_id, range?.from, range?.to);

  const [currentUs, setCurrentUs] = useState<number | null>(null);
  const [seekRequest, setSeekRequest] = useState<{ us: number; nonce: number } | null>(null);
  const requestSeek = (us: number) => setSeekRequest((prev) => ({ us, nonce: (prev?.nonce ?? 0) + 1 }));

  const select = (from: number, to: number) => setParams({ from_us: String(from), to_us: String(to) });
  const playerReady = src !== null && video.data !== undefined && (!tableOk || frames.data !== undefined || frames.isError);
  const active = activeAt(currentUs, timeline.data?.events ?? [], timeline.data?.states ?? []);
  const isWhole = range !== null && range.from === 0 && range.to === run.duration_us;

  return (
    <>
      <h2>장면 복기</h2>

      <div className="range-picker" role="group" aria-label="구간 선택">
        {list.map((v) => {
          const selected = range !== null && v.evidence.start_us === range.from && v.evidence.end_us === range.to;
          return (
            <button
              key={v.evidence.evidence_id}
              type="button"
              className={`chip-button ${selected ? "chip-button--active" : ""}`}
              aria-pressed={selected}
              onClick={() => select(v.evidence.start_us, v.evidence.end_us)}
            >
              {v.evidence.evidence_id} · {formatRange(v.evidence.start_us, v.evidence.end_us)}
            </button>
          );
        })}
        <button
          type="button"
          className={`chip-button ${isWhole ? "chip-button--active" : ""}`}
          aria-pressed={isWhole}
          onClick={() => select(0, run.duration_us)}
        >
          전체 길이
        </button>
      </div>

      {range ? (
        <p className="muted">
          표시 구간 {formatRange(range.from, range.to)} (시작 포함, 끝 제외)
        </p>
      ) : null}

      {evidence.error ? <ProblemNotice error={evidence.error} onRetry={() => void evidence.refetch()} /> : null}

      {!evidence.isPending && src === null && !(list.length === 0 && playback.isPending) ? (
        <ExpiredNotice reasonCode={list[0]?.reason_code ?? playback.data?.reason_code} />
      ) : null}
      {video.error ? <ProblemNotice error={video.error} onRetry={() => void video.refetch()} /> : null}

      {src !== null && range && !playerReady && !video.error ? <Loading /> : null}
      {src !== null && range && playerReady && video.data ? (
        <>
          <ScenePlayer
            videoId={run.video_id}
            src={src}
            fromUs={range.from}
            toUs={range.to}
            width={video.data.width}
            height={video.data.height}
            isVfr={video.data.is_vfr}
            frames={tableOk ? (frames.data?.frames ?? null) : null}
            pose={tableOk ? poses.data : NO_POSE_TOO_LONG}
            participants={run.participants}
            onTimeChange={setCurrentUs}
            onSourceError={onSourceError}
            seekRequest={seekRequest}
          />

          <section aria-labelledby="active-now">
            <h3 id="active-now">지금 표시된 프레임의 사건·상태</h3>
            {active.events.length === 0 && active.states.length === 0 ? (
              <p className="muted">이 시각에 기록된 사건이나 상태가 없습니다. 기록이 없다는 뜻이며 발생하지 않았다는 뜻은 아닙니다.</p>
            ) : (
              <ul className="member-list">
                {active.events.map((e) => (
                  <li key={e.event_id}>
                    {participantName(e.participant_id, run.participants)} · {label(EVENT_TYPE, e.event_label)}{" "}
                    <span className="muted">{formatRange(e.start_timestamp_us, e.end_timestamp_us)}</span>
                  </li>
                ))}
                {active.states.map((s) => (
                  <li key={s.state_id}>
                    {participantName(s.participant_id, run.participants)} · {s.body_part ? `${label(BODY_PART, s.body_part)} ` : ""}
                    {label(STATE_CATEGORY, s.state_category)}: {label(STATE_LABEL, s.label)}{" "}
                    <span className="muted">{formatRange(s.start_us, s.end_us)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}

      {timeline.isPending && range ? <Loading /> : null}
      {timeline.error ? <ProblemNotice error={timeline.error} onRetry={() => void timeline.refetch()} /> : null}

      {timeline.data && range ? (
        <>
          <h3>타임라인</h3>
          <TimelineTrack
            runId={run.run_id}
            fromUs={range.from}
            toUs={range.to}
            participants={run.participants}
            states={timeline.data.states}
            events={timeline.data.events}
            observations={timeline.data.observations}
            playheadUs={src !== null ? currentUs : null}
            onSelect={src !== null ? requestSeek : undefined}
          />
          {timeline.data.note?<p className="muted">{timeline.data.note}</p>:null}
          {timeline.data.assessments.length > 0 ? (
            <section aria-labelledby="tl-assessments">
              <h3 id="tl-assessments">이 구간의 판정</h3>
              <ul className="member-list">
                {timeline.data.assessments.map((a) => (
                  <li key={a.assessment_id}>
                    <span className="muted">{a.subject_ref} · </span>
                    <AssessmentLine assessment={a} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </>
  );
}
