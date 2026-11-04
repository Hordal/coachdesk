import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { apiRequest } from "../../api/client";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";

type Clip = {clip_id:string; filename:string; source_url:string|null; frame_count:number; has_research_pose:boolean};
type Match = {available:boolean; ready:boolean; attached:boolean; assigned:boolean; has_research_pose:boolean};
export function useResearchMatch(videoId:string) {
  return useQuery({queryKey:["research-match",videoId], queryFn:()=>apiRequest<Match>(`/videos/${videoId}/research`), refetchInterval:4000, retry:false});
}

export function ResearchLibraryPage() {
  const navigate=useNavigate();
  const query=useQuery({queryKey:["research-clips"],queryFn:()=>apiRequest<{items:Clip[];note:string}>("/research/clips")});
  const load=useMutation({mutationFn:(id:string)=>apiRequest<{video_id:string}>(`/research/clips/${id}/import`,{method:"POST"}),onSuccess:data=>navigate(`/videos/${data.video_id}`)});
  return <div className="coach-page"><div className="page-heading"><div><h1>연구 영상과 분석 자료</h1><p>제공한 연구 묶음에서 검증한 영상·추적·관절 결과를 불러옵니다.</p></div></div>
    <p className="notice">{query.data?.note ?? "선수는 사진에서 직접 지정합니다. 모델 예측은 코치의 확인이 필요합니다."}</p>
    {query.isPending?<Loading/>:null}{query.error?<ProblemNotice error={query.error}/>:null}{load.error?<ProblemNotice error={load.error}/>:null}
    {query.data?.items.length===0?<p>연구 자료가 준비되지 않았습니다. 프로젝트의 prepare_research.py로 연구 ZIP을 연결해 주세요.</p>:null}
    <div className="coach-columns">{query.data?.items.map(c=><section className="workspace-panel panel-pad" key={c.clip_id}><h2>복싱 연구 영상 {c.clip_id}</h2><p>{c.filename} · {c.frame_count}프레임</p><p>{c.has_research_pose?"연구 추적·관절 결과 포함":"연구 추적 결과 포함 · 관절은 내장 모델로 분석"}</p><button className="primary-action" disabled={load.isPending} onClick={()=>load.mutate(c.clip_id)}>{load.isPending&&load.variables===c.clip_id?"영상 준비 중…":"이 영상으로 시작"}</button>{c.source_url?<p><a href={c.source_url} target="_blank" rel="noreferrer">영상 출처</a></p>:<p className="muted">출처 확인이 필요한 로컬 연구 자료</p>}</section>)}</div>
    <p className="muted">공개 체험에서는 출처를 확인한 영상만 표시합니다. 연구 자료는 정답 라벨이나 새로 학습된 모델이 아닙니다.</p><Link to="/upload">내 영상으로 분석하기 →</Link>
  </div>;
}

export function ResearchWorkflow({videoId,match}:{videoId:string;match:Match}) {
  const client=useQueryClient();
  const navigate=useNavigate();
  const attach=useMutation({mutationFn:()=>apiRequest(`/videos/${videoId}/research/observations`,{method:"POST"}),onSuccess:async()=>{await client.invalidateQueries();navigate(`/videos/${videoId}/observations`);}});
  const analyze=useMutation({mutationFn:()=>apiRequest<{run_id:string}>(`/videos/${videoId}/research/actions`,{method:"POST"}),onSuccess:async data=>{await client.invalidateQueries();navigate(`/runs/${data.run_id}/report`);}});
  return <section className="workspace-panel panel-pad"><h2>제공한 연구 결과로 분석하기</h2><ol>
    <li><p>원본 영상과 일치하는 검출·추적·관절 결과를 연결합니다.</p><button className="quiet-action" disabled={!match.ready||attach.isPending||analyze.isPending} onClick={()=>attach.mutate()}>{attach.isPending?"결과 검증 중…":match.attached?"연구 결과 다시 연결·선수 확인":"연구 결과 연결·선수 지정"}</button>{!match.ready?<p>영상 준비가 완료되면 사용할 수 있습니다.</p>:null}</li>
    <li><p>사진에서 분석할 선수와 상대를 직접 지정하고 저장합니다.</p>{match.attached?<Link to={`/videos/${videoId}/observations`}>선수 지정 열기 →</Link>:null}</li>
    <li><p>관절 근거를 확인하고 코칭 초안과 보고서를 만듭니다.</p><button className="primary-action" disabled={!match.attached||!match.assigned||!match.has_research_pose||analyze.isPending||attach.isPending} onClick={()=>analyze.mutate()}>{analyze.isPending?"보고서 생성 중…":"분석·조언 보고서 생성"}</button></li>
    </ol>{!match.has_research_pose?<p className="notice">이 영상에는 연구 관절 결과가 없습니다. 아래 내장 분석에서 검출·추적부터 진행하세요.</p>:null}
    <p className="muted">품질 검사에 통과한 관절만 사용합니다. 제외된 동작 후보를 복원하거나 명중·득점을 만들어 내지 않습니다. 관측이 부족하면 보고서에 이유와 수동 확인 항목을 표시합니다.</p>
    {attach.error?<ProblemNotice error={attach.error}/>:null}{analyze.error?<ProblemNotice error={analyze.error}/>:null}
  </section>;
}
