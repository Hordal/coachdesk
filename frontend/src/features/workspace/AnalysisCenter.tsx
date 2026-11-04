import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiRequest } from "../../api/client";
import type { components } from "../../api/schema";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { CoachingDraft } from "../../components/CoachingDraft";
import { RunTimeline } from "../../components/RunTimeline";
import { formatTime } from "../../lib/format";
import { AnalysisSettingsPage, AnalysisWorkflow } from "./Workflow";

type WorkItem=components["schemas"]["WorkItem"];
type WorkPage=components["schemas"]["WorkPage"];
type RuleResult={note:string;states:{state_id:string;participant_id:string;body_part:string;label:string;start_us:number;end_us:number}[];unknown_count:number;unknown_reasons:Record<string,number>;pose_samples:number;confirmed_attacks:number;candidate_events:number;guard_return_status:string;guard_return_reason:string|null;guard_return:{assessments:unknown[]}|null;participants:{participant_id:string;role:string}[]};
const labels:Record<string,string>={high_guard:"높은 손 위치",low_guard:"낮은 손 위치",long_guard:"팔을 뻗은 가드",other_guard:"그 외 손 위치"};

export function GuardRuleResult({runId}:{runId:string}) {
  const query=useQuery({queryKey:["guard-rules",runId],queryFn:()=>apiRequest<RuleResult>(`/runs/${runId}/rule-analysis`)});
  if(query.isPending)return <Loading/>;
  if(query.error||!query.data)return <ProblemNotice error={query.error}/>;
  const r=query.data;
  const person=(id:string)=>r.participants.find(p=>p.participant_id===id)?.role==="USER"?"분석 선수":"상대";
  return <section className="workspace-panel panel-pad" aria-label="가드 상태 분석"><h2>가드 상태 관찰</h2><p>{r.note}</p>
    <p>관절 관측 {r.pose_samples}개 · 손 위치 구간 {r.states.length}개 · 판단 보류 {r.unknown_count}개</p>
    {r.states.length?<div className="table-scroll"><table className="stage-table"><thead><tr><th>선수·손</th><th>관찰</th><th>영상 구간</th></tr></thead><tbody>{r.states.slice(0,80).map(s=><tr key={s.state_id}><td>{person(s.participant_id)} · {s.body_part==="left_hand"?"왼손":"오른손"}</td><td>{labels[s.label]??"손 위치"}</td><td><Link to={`/runs/${runId}/scene?from_us=${s.start_us}&to_us=${s.end_us}`}>{formatTime(s.start_us)}–{formatTime(s.end_us)} 확인 →</Link></td></tr>)}</tbody></table>{r.states.length>80?<p>처음 80개 구간을 표시합니다.</p>:null}</div>:<p className="notice">가드 상태를 판단할 연속 관측이 부족합니다. 아래 코칭 초안에서 상체 관측을 이용한 손 위치 측정이 가능한지 확인할 수 있습니다.</p>}
    <details><summary>판단이 보류된 이유와 공격 후 복귀 분석</summary><p>가림·몸통 관측 부족·짧은 관측 구간은 보류합니다. 상대 방향과 앞발을 추측해 앞손·뒷손을 정하지 않습니다.</p><ul>{Object.entries(r.unknown_reasons).map(([reason,count])=><li key={reason}>{{torso_invalid:"코·어깨·골반 관측 부족",too_short:"관측 지속 시간 부족",missing_input:"필요한 관절 관측 부족",no_pose:"관절 관측 없음",stance_unknown:"스탠스 확인 필요"}[reason]??"관측 조건 미충족"}: {count}개</li>)}</ul><p>확인된 공격 {r.confirmed_attacks}건 · 집계 보류 사건 {r.candidate_events}건</p><p>{r.guard_return_status==="calculated"?`입력된 공격·뒷손 상태에 근거한 복귀 판정 ${r.guard_return?.assessments.length??0}건을 계산했습니다. 각 판정에도 관측 부족이 있을 수 있습니다.`:r.guard_return_reason}</p></details>
  </section>;
}

export function AnalysisCenterPage() {
  const [params,setParams]=useSearchParams();
  const [page,setPage]=useState(1);
  const query=useQuery({queryKey:["workspace","analysis-center",page],queryFn:()=>apiRequest<WorkPage>("/workspace/sessions",{params:{page,page_size:12}}),refetchInterval:5000});
  const selectedId=params.get("video")??query.data?.items[0]?.video_id??"";
  const selected=useQuery({queryKey:["session-detail",selectedId],queryFn:()=>apiRequest<WorkItem>(`/workspace/sessions/${selectedId}`),enabled:!!selectedId,refetchInterval:5000});
  const item=selected.data;
  return <div className="coach-page"><div className="page-heading"><div><h1>영상 분석실</h1><p className="muted">영상을 선택해 분석하고, 관절 근거·가드 상태·코칭 제안을 확인합니다.</p></div><Link className="primary-action" to="/upload">새 영상 등록</Link></div>
    <section className="workspace-panel panel-pad"><h2>분석할 영상 선택</h2>{query.isPending?<Loading/>:null}{query.error?<ProblemNotice error={query.error}/>:null}
      {query.data?.items.length===0?<p>등록된 영상이 없습니다. <Link to="/research">연구 영상으로 시작</Link>하거나 새 영상을 등록하세요.</p>:null}
      {query.data?.items.length?<label className="coach-form">경기·훈련 영상<select aria-label="분석할 영상" value={selectedId} onChange={e=>{const next=new URLSearchParams(params);next.set("video",e.target.value);setParams(next);}}>{!query.data.items.some(v=>v.video_id===selectedId)?<option value={selectedId}>{item?.title??"선택한 영상"}</option>:null}{query.data.items.map(v=><option key={v.video_id} value={v.video_id}>{v.title} · {v.status==="ready"?"준비 완료":v.status==="processing"?"영상 준비 중":"처리 상태 확인 필요"}</option>)}</select></label>:null}
      {query.data&&query.data.total>12?<div className="pagination"><span>{page}페이지</span><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>이전 영상 목록</button><button disabled={page*12>=query.data.total} onClick={()=>setPage(p=>p+1)}>다음 영상 목록</button></div>:null}<p><Link to="/research">제공된 연구 영상 사용 →</Link></p>
    </section>
    {selected.error?<ProblemNotice error={selected.error}/>:null}{selectedId&&selected.isPending?<Loading/>:null}
    {item?<><section className="workspace-panel panel-pad"><h2>{item.title}</h2><p>{item.filename}</p><Link to={`/videos/${item.video_id}`}>영상 기록 열기 →</Link>{item.run_id&&item.dataset_kind!=="unanalyzed"?<p><Link className="primary-action" to={`/runs/${item.run_id}/report`}>코칭 보고서 열기</Link></p>:null}</section>
      {item.status==="ready"?<AnalysisWorkflow key={item.video_id} videoId={item.video_id} runId={item.run_id}/>:<p className="notice">{item.status==="processing"?"영상 준비 중입니다. 완료되면 이 화면에서 분석을 시작할 수 있습니다.":"영상 준비 상태를 확인해 주세요. 영상 기록에서 실패 원인과 재시도를 확인할 수 있습니다."}</p>}
      {item.run_id&&item.dataset_kind!=="unanalyzed"?<><RunTimeline key={`timeline-${item.run_id}`} runId={item.run_id}/><GuardRuleResult key={`rules-${item.run_id}`} runId={item.run_id}/><CoachingDraft key={`coaching-${item.run_id}`} runId={item.run_id}/></>:null}</>:null}
    <details className="workspace-panel panel-pad"><summary>분석 준비와 사용 안내</summary><AnalysisSettingsPage/></details>
  </div>;
}
