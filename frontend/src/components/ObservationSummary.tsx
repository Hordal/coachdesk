import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiRequest } from "../api/client";
import { formatTime } from "../lib/format";
import { ProblemNotice } from "./ProblemNotice";

export type ObservationStats = {
  available: boolean; sample_count: number; event_count: number; both_observed_frames: number; duration_us: number;
  suggested_time_us: number | null;
  participants: {participant_id: string; role: string; frame_count: number; first_us: number | null; last_us: number | null}[];
  video_id?: string;
  coaching?: {kind:string; scope:string; insufficient_reason:string|null; manual_checks:string[]; method:string; cards: CoachingCard[]};
};
export type CoachingCard = {id:string;title:string;observation:string;interpretation:string;practice:string;check:string;from_us:number;to_us:number;image_us:number;eligible_samples:number;measured_percent:number;status:string;subject_box?:{left:number;top:number;width:number;height:number}|null};
export const useObservationStats = (runId:string) => useQuery({queryKey:["observation-summary",runId],queryFn:()=>apiRequest<ObservationStats>(`/runs/${runId}/observation-summary`)});

export function ObservationSummary({runId}: {runId: string}) {
  const query = useObservationStats(runId);
  if (query.isPending) return <p role="status">저장된 관절 관측을 요약하고 있습니다…</p>;
  if (query.error || !query.data) return <ProblemNotice error={query.error} onRetry={()=>void query.refetch()} />;
  const s = query.data;
  const at = s.suggested_time_us;
  return <section className="workspace-panel panel-pad" aria-label="자동 분석 요약">
    <h2>이번 영상에서 확인한 내용</h2>
    {s.available ? <>
      <p>아래 시간대에서 선택한 선수의 자세를 영상과 함께 확인할 수 있습니다.</p>
      <ul>{s.participants.map(p=><li key={p.participant_id}><strong>{p.role === "USER" ? "분석 선수" : p.role === "OPPONENT" ? "상대" : p.participant_id}</strong>{p.first_us !== null && p.last_us !== null ? ` · ${formatTime(p.first_us)}–${formatTime(p.last_us)}` : " · 확인 가능한 구간 없음"}</li>)}</ul>
      <p className="muted">위 구간 중에도 가림과 추적 끊김으로 관측이 빠진 프레임이 있습니다. 영상 전체의 연속 추적이나 기술 수행 횟수를 뜻하지 않습니다.</p>
    </> : <p>이 분석에 연결된 관절 관측이 없습니다. 선수 지정과 검출·추적 결과를 확인해 주세요.</p>}
    <p className="muted">공격 기술·명중·득점은 확정하지 않았습니다. 손 위치에 대한 규칙 기반 코칭 초안은 보고서에서 확인할 수 있습니다.</p>
    <details><summary>분석 상태 자세히 보기</summary><p>선수별 관절 관측 {s.sample_count}개 · 동시 관측 {s.both_observed_frames}프레임 · 동작 후보 {s.event_count}건. 후보 0건은 실제 동작이 없었다는 뜻이 아닙니다.</p></details>
    <p><Link className="action-link" to={at === null ? `/runs/${runId}/scene` : `/runs/${runId}/scene?from_us=${at}&to_us=${Math.min(s.duration_us,at+5_000_000)}`}>{at === null ? "영상에서 장면 확인 →" : "관절이 관측된 장면 보기 →"}</Link>{" "}<Link to={`/runs/${runId}/review`}>코치 의견 작성 →</Link></p>
  </section>;
}
