import { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiRequest } from "../../api/client";
import { useMe } from "../../api/hooks";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { formatDateTime } from "../../lib/format";
import { ResearchWorkflow, useResearchMatch } from "../research/ResearchLibrary";

type Team = {team_id: string; name: string; role: "owner" | "editor" | "viewer"};
type Teams = {selected_team_id: string | null; items: Team[]; auth_mode: string};
export function useTeams() { return useQuery({queryKey:["teams"],queryFn:()=>apiRequest<Teams>("/teams"), retry:false}); }
export function useWorkspaceRole() { const q=useTeams(); return q.data?.items?.find(t=>t.team_id===q.data?.selected_team_id)?.role ?? "owner"; }

export function WorkspaceSwitcher() {
  const teams=useTeams();
  const current=teams.data?.selected_team_id ? teams.data.items?.find(t=>t.team_id===teams.data?.selected_team_id) : undefined;
  return <Link to="/team" className="workspace-switcher" title="팀과 작업 공간 관리"><span className="workspace-initial">{current?.name.slice(0,1) ?? "C"}</span><span><strong>{current?.name ?? "개인 분석실"}</strong><small>{current ? {owner:"팀 소유자",editor:"코치·분석 담당자",viewer:"열람 전용"}[current.role] : "이 공간의 기록만 표시"}</small></span></Link>;
}

export function TeamPage() {
  const query=useTeams();
  const [name,setName]=useState("");
  const [email,setEmail]=useState("");
  const [role,setRole]=useState("editor");
  const tid=query.data?.selected_team_id;
  const current=query.data?.items?.find(t=>t.team_id===tid);
  const client=useQueryClient();
  const members=useQuery({queryKey:["team-members",tid],queryFn:()=>apiRequest<Array<{user_id:string;role:string;email:string|null;display_name:string}>>(`/teams/${tid}/members`),enabled:!!tid});
  const select=useMutation({mutationFn:(team_id:string|null)=>apiRequest("/teams/selection",{method:"PUT",json:{team_id}}),onSuccess:()=>{window.location.assign("/");}});
  const create=useMutation({mutationFn:()=>apiRequest("/teams",{method:"POST",json:{name}}),onSuccess:async()=>{setName("");await client.invalidateQueries({queryKey:["teams"]});}});
  const add=useMutation({mutationFn:()=>apiRequest(`/teams/${tid}/members`,{method:"PUT",json:{email,role}}),onSuccess:async()=>{setEmail("");await client.invalidateQueries({queryKey:["team-members",tid]});}});
  const remove=useMutation({mutationFn:(id:string)=>apiRequest(`/teams/${tid}/members/${id}`,{method:"DELETE"}),onSuccess:async()=>{await client.invalidateQueries({queryKey:["team-members",tid]});}});
  return <div className="coach-page"><div className="page-heading"><div><p className="overline">TEAM WORKSPACE</p><h1>팀과 작업 공간</h1><p className="muted">개인 기록과 팀 기록을 구분하고 구성원의 역할을 관리합니다.</p></div></div>
    {query.error ? <ProblemNotice error={query.error}/> : null}
    {query.data?.auth_mode==="dev" ? <p className="notice">현재는 이 PC의 단일 사용자 모드입니다. 다른 코치의 계정 접속은 계정 인증 모드로 운영할 때 사용할 수 있습니다.</p> : null}
    <section className="workspace-panel panel-pad"><h2>작업 공간 전환</h2><div className="space-options"><button className={!tid?"space-option selected":"space-option"} disabled={select.isPending} onClick={()=>select.mutate(null)}><strong>개인 분석실</strong><span>나의 영상과 코칭 기록</span><small>{!tid?"사용 중":"전환"}</small></button>{query.data?.items?.map(t=><button key={t.team_id} className={tid===t.team_id?"space-option selected":"space-option"} disabled={select.isPending} onClick={()=>select.mutate(t.team_id)}><strong>{t.name}</strong><span>{{owner:"소유자",editor:"편집 가능",viewer:"열람 전용"}[t.role]}</span><small>{tid===t.team_id?"사용 중":"전환"}</small></button>)}</div><p className="muted">새 팀을 만들어도 기존 개인 영상은 공유되지 않습니다. 팀 공간으로 전환한 뒤 등록하는 기록이 팀에 보관됩니다.</p>{select.error?<ProblemNotice error={select.error}/>:null}</section>
    <div className="coach-columns"><section className="workspace-panel panel-pad"><h2>새 팀 만들기</h2><form className="coach-form" onSubmit={e=>{e.preventDefault();create.mutate();}}><label>체육관·팀 이름<input value={name} onChange={e=>setName(e.target.value)} maxLength={80} required/></label><button className="primary-action" disabled={create.isPending||!name.trim()}>팀 만들기</button></form>{create.error?<ProblemNotice error={create.error}/>:null}</section>
    <section className="workspace-panel panel-pad"><h2>{current?`${current.name} 구성원`:"팀 구성원"}</h2>{!tid?<p className="muted">팀 공간으로 전환하면 구성원을 확인할 수 있습니다.</p>:<>{members.error?<ProblemNotice error={members.error}/>:null}<ul className="roster-list">{members.data?.map(m=><li key={m.user_id}><div><strong>{m.display_name}</strong><span>{m.email??"이 PC의 계정"} · {{owner:"소유자",editor:"편집",viewer:"열람"}[m.role]}</span></div>{current?.role==="owner"&&m.role!=="owner"?<button className="quiet-action" disabled={remove.isPending} onClick={()=>remove.mutate(m.user_id)}>접근 해제</button>:null}</li>)}</ul>{current?.role==="owner"?<form className="coach-form" onSubmit={e=>{e.preventDefault();add.mutate();}}><h3>가입한 계정 연결·권한 변경</h3><label>계정 이메일<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required maxLength={254}/></label><label>팀 권한<select value={role} onChange={e=>setRole(e.target.value)}><option value="editor">코치·분석 담당자 — 기록 편집·검수</option><option value="viewer">열람 담당자 — 조회만</option></select></label><button className="primary-action" disabled={add.isPending}>구성원 저장</button><p className="muted">신규 계정은 계정 관리의 초대를 통해 먼저 가입해 주세요.</p></form>:null}{add.error?<ProblemNotice error={add.error}/>:null}{remove.error?<ProblemNotice error={remove.error}/>:null}</>}</section></div></div>;
}

type LocalStatus={installed:boolean;running:boolean;available:boolean;name:string;sample_ready?:boolean};
function useLocalEngine(){return useQuery({queryKey:["local-engine"],queryFn:()=>apiRequest<LocalStatus>("/analysis/local-status"),refetchInterval:5000,retry:false});}

type Task={engine?:"local"|"external";task_id:string;video_id:string;kind:"tracking"|"actions";status:string;progress:number;message:string;error:string|null;created_at:string;attempts:number;result:{run_id:string|null;candidate_count?:number;pose_rows?:number;track_count?:number}|null};
const taskStates:Record<string,string>={queued:"워커 연결 대기",running:"분석 실행 중",completed:"결과 수신 완료",failed:"실패",cancelled:"취소됨"};
export function AnalysisWorkflow({videoId,runId}:{videoId:string;runId:string|null}) {
  const research=useResearchMatch(videoId);
  return <>{research.data?.available?<ResearchWorkflow videoId={videoId} match={research.data}/>:null}{research.data?.available&&research.data.has_research_pose?<details><summary>내장 모델로 새로 분석하기 · 기존 연구 결과 대신 사용</summary><LiveAnalysisWorkflow videoId={videoId} runId={runId}/></details>:<LiveAnalysisWorkflow videoId={videoId} runId={runId}/>}</>;
}
function LiveAnalysisWorkflow({videoId,runId}:{videoId:string;runId:string|null}) {
  const client=useQueryClient();
  const me=useMe();
  const publicDemo=me.data?.auth_mode==="dev"&&me.data.role==="participant";
  const local=useLocalEngine();
  const [chosenEngine,setChosenEngine]=useState<"local"|"external"|null>(null);
  const engine=chosenEngine ?? "local";
  const readOnly=useWorkspaceRole()==="viewer";
  const tasks=useQuery({queryKey:["analysis-tasks",videoId],queryFn:()=>apiRequest<Task[]>(`/analysis/tasks?video_id=${videoId}`),refetchInterval:3000});
  const assignment=useQuery({queryKey:["designation",videoId],queryFn:()=>apiRequest<{state:string}>(`/videos/${videoId}/designation`)});
  const enqueue=useMutation({mutationFn:(kind:string)=>apiRequest(`/videos/${videoId}/analysis`,{method:"POST",json:{kind,engine,request_key:crypto.randomUUID()}}),onSuccess:async()=>{await client.invalidateQueries({queryKey:["analysis-tasks",videoId]});}});
  const cancel=useMutation({mutationFn:(id:string)=>apiRequest(`/analysis/tasks/${id}/cancel`,{method:"POST"}),onSuccess:async()=>{await client.invalidateQueries({queryKey:["analysis-tasks",videoId]});}});
  const useLocal=useMutation({mutationFn:(id:string)=>apiRequest(`/analysis/tasks/${id}/use-local`,{method:"POST"}),onSuccess:async()=>{await client.invalidateQueries({queryKey:["analysis-tasks",videoId]});}});
  const latest=tasks.data?.[0];
  useEffect(()=>{
    if(latest?.status==="completed") {
      for(const key of [["video-summary",videoId],["session-detail",videoId],["designation",videoId],["workspace"]]) void client.invalidateQueries({queryKey:key});
    }
  },[latest?.task_id,latest?.status,videoId,client]);
  const active=tasks.data?.some(t=>t.status==="queued"||t.status==="running");
  return <section className="workspace-panel panel-pad"><div className="section-heading"><div><p className="overline">ANALYSIS WORKFLOW</p><h2>분석과 검수</h2></div><Link className="quiet-action" to={`/videos/${videoId}/data`}>파일로 결과 연결</Link></div>
    <div className="analysis-engine-picker"><label>분석 방식<select value={engine} onChange={e=>setChosenEngine(e.target.value as "local"|"external")} disabled={!!active||readOnly}><option value="local">내장 시험 분석 · 이 PC에서 실행</option>{!publicDemo?<option value="external">외부 모델 · 담당자 워커</option>:null}</select></label><p>{engine==="local" ? local.data?.available ? "내장 분석 준비됨 · 영상은 이 PC에서 처리됩니다." : local.data?.installed ? "분석기를 시작해야 합니다. 프로그램을 다시 실행해 주세요." : "내장 분석을 먼저 준비해 주세요. 분석 준비·사용 안내에서 확인할 수 있습니다." : "연결된 담당자 모델이 작업을 수행합니다."}</p></div>
    {engine==="local" ? <p className="muted">33개 관절을 관측하고 팔의 확장·복귀 동작을 후보로 표시합니다. 명중·득점·기술 종류를 확정하는 모델은 아닙니다.</p>:null}
    <ol className="workflow-steps"><li><span>01</span><div><strong>선수 검출·추적</strong><p>분석 모델로 후보 인물과 관절을 찾습니다.</p><button className="quiet-action" disabled={readOnly||active||enqueue.isPending||local.isPending||(engine==="local"&&!local.data?.available)} onClick={()=>enqueue.mutate("tracking")}>검출·추적 요청</button></div></li><li><span>02</span><div><strong>선수 지정</strong><p>영상에서 분석할 선수와 상대를 직접 고릅니다.</p><Link className="quiet-action" to={`/videos/${videoId}/observations`}>{assignment.data?.state==="ASSIGNED"?"지정 확인·변경":"후보 확인·선수 지정"}</Link></div></li><li><span>03</span><div><strong>동작 분석</strong><p>지정한 선수를 기준으로 동작 후보를 받습니다.</p><button className="quiet-action" disabled={readOnly||active||enqueue.isPending||local.isPending||(engine==="local"&&!local.data?.available)||assignment.data?.state!=="ASSIGNED"} onClick={()=>enqueue.mutate("actions")}>동작 분석 요청</button></div></li><li><span>04</span><div><strong>자동 관찰·코칭 보고서</strong><p>분석 결과와 연습 제안을 확인합니다. 메모 추가는 선택 사항입니다.</p>{runId?<Link className="quiet-action" to={`/runs/${runId}/report`}>코칭 보고서 열기</Link>:<span className="muted">영상 준비 후 열 수 있습니다</span>}</div></li></ol>
    {useLocal.error?<ProblemNotice error={useLocal.error}/>:null}{enqueue.error?<ProblemNotice error={enqueue.error}/>:null}{tasks.error?<ProblemNotice error={tasks.error}/>:null}{cancel.error?<ProblemNotice error={cancel.error}/>:null}
    {latest?<div className="task-status" role="status"><div><strong>{latest.kind==="tracking"?"검출·추적":"동작 분석"} · {latest.engine==="local"&&latest.status==="queued"?"내장 분석 대기":taskStates[latest.status]??latest.status}</strong><p>{latest.error||latest.message||(latest.status==="queued"?latest.engine==="local"?"이 PC의 내장 분석기가 작업을 처리합니다.":"연결된 모델 워커가 이 작업을 가져가면 시작됩니다.":"")}</p>{latest.status==="running"?<progress max={1} value={latest.progress} aria-label="분석 진행률"/>:null}{latest.status==="completed"&&latest.kind==="tracking"?<Link className="quiet-action" to={`/videos/${videoId}/observations`}>검출한 후보에서 선수 지정 →</Link>:null}{latest.result?.run_id?<Link to={`/runs/${latest.result.run_id}/report`}>자동 분석 결과·코칭 보고서 열기 →</Link>:null}</div>{latest.status==="queued"&&latest.engine!=="local"&&local.data?.available?<button className="primary-action" disabled={readOnly||useLocal.isPending} onClick={()=>useLocal.mutate(latest.task_id)}>내장 분석으로 실행</button>:null}{active?<button className="quiet-action" disabled={readOnly||cancel.isPending} onClick={()=>cancel.mutate(latest.task_id)}>작업 취소</button>:null}</div>:<p className="muted">{engine==="local"?"검출·추적 요청으로 시작하세요. 완료 후 후보를 직접 지정하면 동작 분석을 진행할 수 있습니다.":<>자동 분석을 사용하려면 <Link to="/analysis">분석 워커 연결</Link>에서 담당자의 모델을 연결하세요. 모델 없이도 파일 가져오기와 수동 검수를 이용할 수 있습니다.</>}</p>}
    {(tasks.data?.length??0)>1?<details className="review-history"><summary>이전 분석 작업 ({tasks.data!.length-1})</summary>{tasks.data!.slice(1).map(t=><p key={t.task_id}>{t.kind==="tracking"?"검출·추적":"동작 분석"} · {taskStates[t.status]} · {formatDateTime(t.created_at)}{t.error?` · ${t.error}`:""}</p>)}</details>:null}
  </section>;
}

function ExternalAnalysisSettingsPage() {
  const local=useLocalEngine();
  const role=useWorkspaceRole();
  const [label,setLabel]=useState("");
  const [secret,setSecret]=useState("");
  const [revokeId,setRevokeId]=useState<string|null>(null);
  const client=useQueryClient();
  const keys=useQuery({queryKey:["analysis-keys"],queryFn:()=>apiRequest<Array<{key_id:string;label:string;revoked_at:string|null;last_seen:number|null}>>("/analysis/keys"),enabled:role==="owner",refetchInterval:10000});
  const create=useMutation({mutationFn:()=>apiRequest<{token:string}>("/analysis/keys",{method:"POST",json:{label}}),onSuccess:async(data)=>{setSecret(data.token);setLabel("");await client.invalidateQueries({queryKey:["analysis-keys"]});}});
  const revoke=useMutation({mutationFn:(id:string)=>apiRequest(`/analysis/keys/${id}`,{method:"DELETE"}),onSuccess:async()=>{setRevokeId(null);await client.invalidateQueries({queryKey:["analysis-keys"]});}});
  return <div className="coach-page"><div className="page-heading"><div><p className="overline">MODEL CONNECTION</p><h1>외부 모델 연결</h1><p className="muted">분석 담당자의 모델 실행 환경을 현재 작업 공간에 연결합니다.</p></div></div><section className="workspace-panel panel-pad"><h2>내장 시험 분석</h2><p>{local.data?.installed?local.data.available?"준비됨 · 현재 PC에서 영상 추론 가능":"설치됨 · 분석 실행 상태 확인 중":"설치 필요 · setup_analysis.py를 한 번 실행해 주세요"}</p><p className="muted">사전 학습된 MediaPipe 포즈 모델과 팔의 확장·복귀 후보 규칙을 사용합니다. 추가 모델 학습 없이 검출·추적, 33개 관절과 동작 후보를 시험할 수 있습니다.</p><Link to="/">영상을 선택해 분석 시작 →</Link></section><section className="workspace-panel panel-pad"><h2>외부 모델 연결 순서</h2><ol className="connection-guide"><li>현재 공간에서 연결 키를 발급하고 분석 담당자에게 안전하게 전달합니다.</li><li>담당자가 제공된 워커 실행 도구에 모델 실행 함수를 연결합니다.</li><li>영상 기록에서 검출·추적을 요청하고 선수를 지정한 뒤 동작 분석을 요청합니다.</li></ol><p className="muted">작업 큐·원본 전송·진행 상태·재시도·결과 검증은 이 도구가 처리합니다. 모델 학습과 추론 함수는 담당자가 준비해야 합니다.</p><div className="connection-downloads"><a className="quiet-action" href="/api/v1/analysis/worker-script" download>워커 실행 도구 받기</a><a className="quiet-action" href="/api/v1/analysis/result-contract" download>결과 입력 형식 받기</a><Link to="/data-guide">파일 가져오기 안내 →</Link></div></section>
    {role!=="owner"?<p className="notice">연결 키는 현재 공간의 소유자가 관리합니다.</p>:<section className="workspace-panel panel-pad"><h2>모델 연결 키</h2><p className="muted">이 키를 가진 워커는 현재 공간의 대기 작업을 수행하고 해당 작업의 원본 영상을 받을 수 있습니다.</p><form className="coach-form connection-form" onSubmit={e=>{e.preventDefault();create.mutate();}}><label>연결 이름<input value={label} onChange={e=>setLabel(e.target.value)} maxLength={80} placeholder="예: 팀 분석 PC" required/></label><button className="primary-action" disabled={create.isPending||!label.trim()}>연결 키 발급</button></form>{secret?<div className="notice" role="status"><strong>발급한 키는 이 화면에서 한 번만 확인할 수 있습니다.</strong><label className="secret-key">연결 키<input readOnly value={secret} onFocus={e=>e.currentTarget.select()}/></label><button className="quiet-action" onClick={()=>setSecret("")}>확인 후 가리기</button></div>:null}{create.error?<ProblemNotice error={create.error}/>:null}{keys.isPending?<Loading/>:null}{keys.error?<ProblemNotice error={keys.error}/>:null}<ul className="roster-list">{keys.data?.map(k=><li key={k.key_id}><div><strong>{k.label}</strong><span>{k.revoked_at?"폐기됨":k.last_seen&&Date.now()/1000-k.last_seen<60?"최근 1분 내 접속":"연결 대기"}</span></div>{!k.revoked_at?<button className="quiet-action" onClick={()=>setRevokeId(k.key_id)}>폐기</button>:null}</li>)}</ul>{revokeId?<div className="notice" role="alertdialog" aria-label="연결 키 폐기 확인"><p>이 키의 접속과 실행 중 작업을 중단합니다.</p><button className="quiet-action" disabled={revoke.isPending} onClick={()=>revoke.mutate(revokeId)}>폐기 확인</button> <button className="quiet-action" onClick={()=>setRevokeId(null)}>닫기</button></div>:null}{revoke.error?<ProblemNotice error={revoke.error}/>:null}</section>}</div>;
}


export function AnalysisSettingsPage() {
  const local=useLocalEngine();
  const me=useMe();
  const publicDemo=me.data?.auth_mode === "dev" && me.data.role === "participant";
  return <div className="coach-page">
    <div className="page-heading"><div><h1>분석 준비와 사용 안내</h1><p className="muted">짧은 훈련 영상을 이 PC에서 분석하고 결과를 직접 확인합니다.</p></div></div>
    <section className="workspace-panel panel-pad"><h2>내장 분석 상태</h2>
      {local.isPending ? <Loading/> : local.error ? <ProblemNotice error={local.error}/> : <p role="status">{local.data?.available ? "준비 완료 · 이 PC에서 분석할 수 있습니다" : local.data?.installed ? "모델 설치됨 · 프로그램을 다시 실행해 분석기를 시작하세요" : "설치 필요 · 프로젝트 폴더의 분석준비.cmd를 한 번 실행한 후 다시 시작하세요"}</p>}
      <p>사전 학습된 관절 인식 모델로 사람을 추적하고 팔의 확장·복귀 동작 후보를 찾습니다. 명중·득점·기술 종류는 판정하지 않습니다.</p>
    </section>
    <section className="workspace-panel panel-pad"><h2>처음 사용하는 순서</h2><ol>
      <li><Link to="/upload">영상 등록</Link>에서 MP4 또는 MOV를 올리고 영상 준비 완료를 기다립니다.</li>
      <li>영상 상세의 분석 방식에서 ‘내장 시험 분석’을 선택하고 ‘검출·추적 요청’을 누릅니다.</li>
      <li>완료 후 ‘후보 확인·선수 지정’으로 이동합니다. 혼자 나온 영상은 ‘개인 훈련’, 두 사람이 대결하는 영상은 ‘스파링’을 선택합니다.</li>
      <li>사진 속 사람의 상자를 눌러 분석할 선수를 고른 뒤 ‘선택 저장’을 누릅니다. 스파링은 상대도 따로 고릅니다.</li>
      <li>영상 상세로 돌아가 ‘동작 분석 요청’을 누릅니다.</li>
      <li>분석이 완료되면 코칭 보고서에서 자동 관찰과 연습 제안을 확인합니다. 필요할 때 근거 영상·관절을 확인하거나 메모를 추가할 수 있습니다.</li>
    </ol><p className="muted">입력 제한: 3분 이하·5,000프레임 이하·시작 시각 0. 가림이나 빠른 회전에서는 추적 번호가 나뉠 수 있습니다. 번호가 다르다고 다른 사람으로 지정하지 마세요.</p></section>
    <section className="workspace-panel panel-pad"><h2>테스트 영상</h2><p>17초 섀도복싱 영상 · 720p · 약 5MB. ‘개인 훈련’으로 선택해서 업로드부터 보고서까지 시험하세요. 인식 정확도를 평가하는 정답 데이터는 아닙니다.</p>
      {local.data?.sample_ready ? <a className="primary-action" href="/api/v1/analysis/sample-video" download>테스트 영상 받기</a> : <p>분석준비.cmd를 실행하면 테스트 영상도 함께 준비됩니다.</p>}
      <p className="muted">영상: Ketut Subiyanto / <a href="https://www.pexels.com/video/a-man-shadow-boxing-in-a-gym-4806552/" target="_blank" rel="noreferrer">Pexels 원본</a> · <a href="https://www.pexels.com/license/" target="_blank" rel="noreferrer">이용 조건</a></p>
    </section>
    {me.data&&!publicDemo ? <details className="workspace-panel panel-pad"><summary>고급 설정 · 별도로 준비한 외부 모델 연결</summary><ExternalAnalysisSettingsPage/></details> : null}
  </div>;
}
