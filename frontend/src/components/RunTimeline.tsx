import { useState } from "react";
import { usePlayback,useRun,useTimeline } from "../api/hooks";
import { TimelineTrack } from "./TimelineTrack";
import { CoachingClip } from "./CoachingClip";
import { Loading,ProblemNotice } from "./ProblemNotice";

export function RunTimeline({runId}:{runId:string}) {
  const run=useRun(runId);
  const timeline=useTimeline(runId,0,run.data?.duration_us);
  const playback=usePlayback(run.data?.video_id??"",!!run.data?.video_id,240000);
  const [clip,setClip]=useState<{from:number;to:number}|null>(null);
  const [at,setAt]=useState<number|null>(null);
  if(run.error||timeline.error)return <ProblemNotice error={run.error??timeline.error}/>;
  if(run.isPending||timeline.isPending)return <Loading/>;
  if(!run.data||!timeline.data)return null;
  const r=run.data,t=timeline.data;
  return <section className="workspace-panel panel-pad" aria-label="분석 타임라인"><h2>분석 타임라인</h2><p>파란 구간은 관절 관측, 손 위치 구간은 가드 규칙의 관찰입니다. 빈 구간은 판단 근거가 없는 부분입니다.</p>
    <TimelineTrack runId={runId} fromUs={0} toUs={r.duration_us} participants={r.participants} states={t.states} events={t.events} observations={t.observations} listInitiallyOpen={false} playheadUs={at} onRangeSelect={(from,to)=>{setAt(from);setClip({from,to:Math.min(r.duration_us,to)});}}/>
    {t.note?<p className="muted">{t.note}</p>:null}
    {clip&&playback.data?.playable&&playback.data.access_url?<CoachingClip key={`${clip.from}-${clip.to}-${playback.data.access_url}`} src={playback.data.access_url} fromUs={clip.from} toUs={clip.to} label="타임라인 선택 구간" onClose={()=>{setClip(null);setAt(null);}} onSourceError={()=>void playback.refetch()} onTimeChange={setAt}/>:clip?<p role="status">{playback.isPending?"구간 영상을 준비하고 있습니다…":"현재 원본 영상을 재생할 수 없습니다."}</p>:<p className="muted">구간을 누르면 이 화면에서 해당 구간만 재생합니다.</p>}
    {clip&&playback.error?<ProblemNotice error={playback.error} onRetry={()=>void playback.refetch()}/>:null}
  </section>;
}
