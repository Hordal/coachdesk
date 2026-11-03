import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiRequest, type ObservationSetView } from "../../api/client";
import { useObservations } from "../../api/hooks";
import { Loading } from "../../components/ProblemNotice";
import { DesignationPanel } from "./DesignationPanel";
import { formatDuration, formatTime } from "../../lib/format";

const MAX_BYTES = 200 * 1024 * 1024;

interface Failure {
  title: string;
  details: string[];
  traceId: string | null;
}

/** JSONL(한 줄에 JSON 하나)을 배열로 바꾼다. 빈 줄은 건너뛴다. */
export function parseJsonLines(text: string): unknown[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as unknown);
}

const percent = (ratio: number) => `${(ratio * 100).toFixed(ratio < 0.01 ? 2 : 1)}%`;

/** 선수 검출·추적 결과를 가져오고 후보 트랙의 진단 값을 보여 준다. 트랙은 후보일 뿐 선수가 아니다. */
export function ObservationsPage() {
  const { videoId = "" } = useParams();
  const client = useQueryClient();
  const current = useObservations(videoId);
  const [metadata, setMetadata] = useState<File | null>(null);
  const [tracks, setTracks] = useState<File | null>(null);
  const [detections, setDetections] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!metadata || !tracks || busy) return;
    setFailure(null);
    if ([metadata, tracks, detections].some((f) => f && f.size > MAX_BYTES)) {
      setFailure({ title: "파일이 너무 큽니다", details: [], traceId: null });
      return;
    }
    let body: unknown;
    try {
      body = {
        metadata: JSON.parse(await metadata.text()),
        tracks: parseJsonLines(await tracks.text()),
        ...(detections ? { detections: parseJsonLines(await detections.text()) } : {}),
      };
    } catch {
      setFailure({ title: "JSON 또는 JSONL 파일로 읽을 수 없습니다", details: [], traceId: null });
      return;
    }
    setBusy(true);
    try {
      await apiRequest<ObservationSetView>(`/videos/${videoId}/observations`, { method: "POST", json: body });
      await client.invalidateQueries({ queryKey: ["observations", videoId] });
    } catch (e) {
      if (e instanceof ApiError) setFailure({ title: e.message, details: e.details, traceId: e.traceId });
      else setFailure({ title: "가져오지 못했습니다", details: [], traceId: null });
    } finally {
      setBusy(false);
    }
  }

  const set = current.data ?? null;
  const sorted = set ? [...set.tracks].sort((a, b) => b.frame_count - a.frame_count || a.track_id - b.track_id) : [];

  return (
    <>
      <h1>{set ? "분석할 선수 지정" : "검출·추적 결과 가져오기"}</h1>
      {set ? <p className="muted">사진에서 선수 선택 → 선택 저장 → 동작 분석 순서로 진행합니다.</p> : null}
      {set ? <DesignationPanel key={set.set_id} videoId={videoId} set={set} /> : null}
      <details className="card process-details" open={!set}><summary>분석 담당자용: 외부 검출·추적 파일 가져오기</summary>
      <p className="muted">
        선수 검출·추적 파이프라인이 만든 <code>metadata.json</code>, <code>tracking_frames.jsonl</code>, <code>detection_frames.jsonl</code>(선택)을 이 영상에 연결합니다.
        가져온 내용은 고치지 않고 그대로 저장하며, 영상의 프레임 수와 모든 프레임 시각이 맞지 않으면 저장하지 않습니다.
      </p>

      <form className="card" onSubmit={(e) => void submit(e)}>
        <p>
          <label>
            metadata.json <input type="file" accept=".json,application/json" disabled={busy} onChange={(e) => setMetadata(e.target.files?.[0] ?? null)} />
          </label>
        </p>
        <p>
          <label>
            tracking_frames.jsonl <input type="file" accept=".jsonl,.json,application/json" disabled={busy} onChange={(e) => setTracks(e.target.files?.[0] ?? null)} />
          </label>
        </p>
        <p>
          <label>
            detection_frames.jsonl(선택) <input type="file" accept=".jsonl,.json,application/json" disabled={busy} onChange={(e) => setDetections(e.target.files?.[0] ?? null)} />
          </label>
        </p>
        <p>
          <button type="submit" disabled={!metadata || !tracks || busy}>
            가져오기
          </button>
        </p>
      </form>
      </details>

      {failure ? (
        <div className="notice notice--error" role="alert">
          <p>
            <strong>{failure.title}</strong>
          </p>
          {failure.details.length > 0 ? (
            <ul>
              {failure.details.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          ) : null}
          {failure.traceId ? <p className="muted">문의 시 확인 번호: {failure.traceId}</p> : null}
        </div>
      ) : null}

      {current.isPending ? <Loading /> : null}
      {!current.isPending && set === null ? <p className="muted">가져온 검출·추적 결과가 없습니다.</p> : null}

      {set ? (
        <details className="card process-details"><summary>분석 담당자용: 검출 상태 자세히 보기 · 선택할 필요 없음</summary><section aria-labelledby="candidates">
          <h2 id="candidates">후보 트랙</h2>
          <p className="muted">
            프레임 {set.frame_count.toLocaleString()}개 · 트랙 {set.track_count}개 · {set.detection_count>0?`검출 ${set.detection_count.toLocaleString()}건`:set.source.startsWith("coachdesk-local")?"관절 기반 후보":"검출 파일 미첨부"} · 선수 지정 상태: {set.assignment_state === "ASSIGNED" ? "지정됨" : "선택 전"}
          </p>
          <p className="notice notice--muted" role="note">
            트랙은 추적기가 붙인 임시 번호이며 선수가 아닙니다. 선수 지정에서 실제 장면을 보고 트랙을 연결해 주세요. 이 표는 후보를 고를 때 참고하는 진단 값입니다.
          </p>
          <table className="stage-table">
            <caption className="sr-only">후보 트랙</caption>
            <thead>
              <tr>
                <th scope="col">트랙</th>
                <th scope="col">보이는 구간</th>
                <th scope="col">프레임</th>
                <th scope="col">끊긴 프레임</th>
                <th scope="col">가장 긴 공백</th>
                <th scope="col">화면 면적 중앙값</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((t) => (
                <tr key={t.track_id}>
                  <th scope="row">{t.track_id}</th>
                  <td>
                    {formatTime(t.first_us)}–{formatTime(t.last_us)}
                  </td>
                  <td>{t.frame_count.toLocaleString()}</td>
                  <td>{t.missing_frames.toLocaleString()}</td>
                  <td>{t.longest_gap_us > 0 ? t.longest_gap_us<1_000_000 ? `${(t.longest_gap_us/1_000_000).toFixed(2)}초` : formatDuration(t.longest_gap_us) : "없음"}</td>
                  <td>{percent(t.median_area_ratio)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {set.warnings.length > 0 ? (
            <ul className="member-list">
              {set.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          ) : null}
        </section></details>
      ) : null}

      <p>
        <Link to={`/videos/${videoId}`}>영상으로 돌아가기</Link>
      </p>
    </>
  );
}
