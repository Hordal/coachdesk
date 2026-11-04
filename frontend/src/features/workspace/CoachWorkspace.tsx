import { useWorkspaceRole } from "./Workflow";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { apiRequest } from "../../api/client";
import type { components } from "../../api/schema";
import { CoachingDraft } from "../../components/CoachingDraft";
import { Loading, ProblemNotice } from "../../components/ProblemNotice";
import { formatBytes, formatDateTime, formatDuration, formatTime } from "../../lib/format";
import { label, SPORT, EVENT_TYPE } from "../../strings/ko";

type WorkItem = components["schemas"]["WorkItem"];
type WorkPage = components["schemas"]["WorkPage"];
type Review = components["schemas"]["ReviewView"];
type ReviewState = Review["state"];
type Athlete = { athlete_id: string; name: string; discipline: string; created_at: string };
type Activity = { activity_id: number; actor: string; action: string; target_id: string; at: string };
type Status = { status: string; database: string; video_tools: boolean; active_workers: number; disk_free_bytes: number; disk_total_bytes: number; auth_mode: string; profile: string; retention_days: number; fixtures_enabled: boolean; automatic_inference: boolean; analysis_workers: number };
const reviewLabels: Record<string, string> = { pending: "검수 대기", in_review: "검토 중", reviewed: "검수 완료", changes_requested: "보완 필요" };
const sessionLabels: Record<string, string> = { sparring: "스파링", match: "경기", training: "훈련" };
const activityLabels: Record<string, string> = { "analysis_requested":"분석 작업 요청", "analysis_completed":"분석 결과 수신", "member_updated":"팀 구성원 변경", "member_removed":"팀 접근 해제", "athlete.created": "선수 등록", "session.updated": "훈련 기록 수정", "review.pending": "검수 대기로 변경", "review.in_review": "검토 시작", "review.reviewed": "검수 완료", "review.changes_requested": "보완 요청", "report.exported": "보고서 내보내기", "job.retried": "영상 처리 재시작" };
const rosterQuery = () => ({ queryKey: ["athletes"], queryFn: () => apiRequest<Athlete[]>("/workspace/athletes") });
const reviewable = (item: WorkItem) => item.event_count > 0 || item.dataset_kind === "model";
const stateOf = (item: WorkItem) => item.analysis_status === "queued" ? "분석 워커 대기" : item.analysis_status === "running" ? "분석 중" : item.analysis_status === "failed" ? "분석 실패" : item.status === "processing" ? "처리 중" : item.status === "failed" ? "처리 실패" : item.status === "cancelled" ? "처리 취소" : !reviewable(item) ? "분석 결과 대기" : reviewLabels[item.review_state];

export function CoachHome({ reviewOnly = false }: { reviewOnly?: boolean }) {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const status = reviewOnly ? "pending" : params.get("status") ?? "all";
  const rawPage = Number(params.get("page") ?? 1);
  const page = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1;
  const q = params.get("q") ?? "";
  const query = useQuery({ queryKey: ["workspace", status, page, q, params.get("athlete")], queryFn: () => apiRequest<WorkPage>("/workspace/sessions", { params: { status, page, q, athlete_id: params.get("athlete") ?? undefined } }), refetchInterval: 15_000 });
  const counts = query.data?.counts;
  function filter(next: string) { const copy = new URLSearchParams(params); copy.set("status", next); copy.delete("page"); setParams(copy); }
  function find(e: FormEvent) { e.preventDefault(); const copy = new URLSearchParams(params); copy.set("q", search.trim()); copy.delete("page"); setParams(copy); }
  return <div className="coach-page">
    <div className="page-heading"><div><p className="overline">COACH WORKSPACE</p><h1>{reviewOnly ? "검수 대기함" : "경기·훈련 라이브러리"}</h1><p className="muted">{reviewOnly ? "근거 장면을 확인하고 코칭 의견을 남겨 주세요." : "선수의 훈련을 기록하고, 근거를 확인해 다음 경기를 준비하세요."}</p></div><Link className="primary-action" to="/upload">＋ 새 영상 등록</Link></div>
    <section className="coach-metrics" aria-label="분석실 현황">
      {[ ["등록된 영상", counts?.total], ["처리 중", counts?.processing], ["검수 대기", counts?.pending], ["검수 완료", counts?.reviewed] ].map(([title, value]) => <div key={String(title)}><span>{title}</span><strong>{value ?? "—"}<small>건</small></strong></div>)}
    </section>
    <section className="workspace-panel">
      <div className="library-toolbar"><div className="library-tabs" aria-label="영상 필터">{(reviewOnly ? [["pending", "검수 대기"]] : [["all", "전체 기록"], ["processing", "처리 중"], ["pending", "검수 대기"], ["reviewed", "검수 완료"], ["failed", "처리 실패"]]).map(([value, text]) => <button key={value} type="button" aria-pressed={status === value} onClick={() => filter(value ?? "all")}>{text}</button>)}</div>
        <form onSubmit={find} className="search-form"><label className="sr-only" htmlFor="session-search">영상 또는 선수 검색</label><input id="session-search" placeholder="영상 또는 선수 검색" value={search} maxLength={120} onChange={(e) => setSearch(e.target.value)} /><button aria-label="검색" type="submit">검색</button></form></div>
      {query.isPending ? <Loading /> : null}
      {query.error ? <ProblemNotice error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.data?.items.length === 0 ? <div className="coach-empty"><div className="empty-ring" aria-hidden="true">↗</div><h2>{q || status !== "all" || params.has("athlete") ? "조건에 맞는 기록이 없습니다" : "첫 번째 훈련 기록을 시작하세요"}</h2><p>{q || status !== "all" || params.has("athlete") ? "검색어나 상태 필터를 바꿔 다시 확인하세요." : "경기·스파링 영상을 등록하면 준비 상태부터 코칭 보고서까지 한곳에서 관리할 수 있습니다."}</p><Link className="primary-action" to="/upload">영상 등록하기</Link><div className="empty-flow"><span>01 영상 등록</span><span>02 분석 결과 연결</span><span>03 장면 검수</span><span>04 코칭 보고서</span></div></div> : null}
      {query.data && query.data.items.length > 0 ? <div className="table-scroll"><table className="coach-table"><thead><tr><th>경기·훈련 기록</th><th>선수 / 종목</th><th>촬영일 / 길이</th><th>진행 상태</th><th><span className="sr-only">작업</span></th></tr></thead><tbody>{query.data.items.map((item) => <tr key={item.video_id}><td><div className="record-title"><span className="video-glyph" aria-hidden="true">▷</span><div><Link to={`/videos/${item.video_id}`}>{item.title}</Link><small>{sessionLabels[item.session_type]}{item.title !== item.filename ? ` · ${item.filename}` : ""}</small></div></div></td><td>{item.athlete_name ?? "선수 미지정"}<small>{label(SPORT, item.sport)}</small></td><td>{item.session_date ?? "촬영일 미입력"}<small>{item.duration_us !== null ? formatDuration(item.duration_us) : "준비 중"}</small></td><td><span className={`workflow-status workflow-status--${item.status === "failed" ? "failed" : item.review_state}`}>{stateOf(item)}</span>{(item.status === "processing" || item.analysis_status === "running") ? <progress max={1} value={item.progress} aria-label={`${item.title} 처리 진행`} /> : null}</td><td><Link className="quiet-action" to={item.run_id && reviewable(item) ? `/runs/${item.run_id}/review` : `/videos/${item.video_id}`}>{reviewable(item) ? "장면 검토 →" : "기록 열기 →"}</Link></td></tr>)}</tbody></table></div> : null}
      {query.data && query.data.total > 0 ? <div className="pagination"><span>총 {query.data.total.toLocaleString()}건 · {page}페이지</span><div><button disabled={page <= 1} onClick={() => { const copy = new URLSearchParams(params); copy.set("page", String(page - 1)); setParams(copy); }}>이전</button><button disabled={page * query.data.page_size >= query.data.total} onClick={() => { const copy = new URLSearchParams(params); copy.set("page", String(page + 1)); setParams(copy); }}>다음</button></div></div> : null}
    </section>
    <div className="workspace-footnote"><span className="live-dot" /> 실제 등록한 기록만 표시됩니다. 검수 완료는 코치의 검토 상태이며 AI 정확도 인증이 아닙니다.</div>
  </div>;
}

export function RosterPage() {
  const readOnly=useWorkspaceRole()==="viewer";
  const roster = useQuery(rosterQuery());
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [discipline, setDiscipline] = useState("mma");
  const add = useMutation({ mutationFn: () => apiRequest("/workspace/athletes", { method: "POST", json: { name: name.trim(), discipline } }), onSuccess: async () => { setName(""); await client.invalidateQueries({ queryKey: ["athletes"] }); } });
  return <div className="coach-page"><div className="page-heading"><div><p className="overline">ATHLETES</p><h1>선수 명부</h1><p className="muted">훈련 영상을 선수별로 연결해 기록을 이어가세요.</p></div></div><div className="coach-columns"><section className="workspace-panel panel-pad"><h2>선수 등록</h2><form onSubmit={(e) => { e.preventDefault(); add.mutate(); }} className="coach-form"><label>이름 또는 훈련용 별칭<input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required /></label><label>주 종목<select value={discipline} onChange={(e) => setDiscipline(e.target.value)}>{Object.entries(SPORT).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><button className="primary-action" disabled={readOnly || !name.trim() || add.isPending}>{add.isPending ? "등록 중…" : "선수 등록"}</button></form>{add.error ? <ProblemNotice error={add.error} /> : null}</section><section className="workspace-panel panel-pad"><h2>등록 선수 <span className="count-pill">{roster.data?.length ?? 0}</span></h2>{roster.isPending ? <Loading /> : null}{roster.error ? <ProblemNotice error={roster.error} onRetry={() => void roster.refetch()} /> : null}{roster.data?.length === 0 ? <p className="muted">등록된 선수가 없습니다. 왼쪽에서 첫 선수를 등록하세요.</p> : null}<ul className="roster-list">{roster.data?.map((a) => <li key={a.athlete_id}><span className="athlete-avatar" aria-hidden="true">{a.name.slice(0, 1)}</span><div><strong>{a.name}</strong><span>{label(SPORT, a.discipline)}</span></div><Link to={`/?athlete=${a.athlete_id}`}>훈련 기록 →</Link></li>)}</ul></section></div></div>;
}

export function RecordHeading({videoId,fallback}:{videoId:string;fallback:string}) {
  const item=useQuery({queryKey:["session-detail",videoId],queryFn:()=>apiRequest<WorkItem>(`/workspace/sessions/${videoId}`)});
  return <h1 className="filename-heading">{item.data?.title??fallback}</h1>;
}

export function SessionEditor({ videoId }: { videoId: string }) {
  const readOnly=useWorkspaceRole()==="viewer";
  const item = useQuery({ queryKey: ["session-detail", videoId], queryFn: () => apiRequest<WorkItem>(`/workspace/sessions/${videoId}`), refetchInterval: (query) => query.state.data?.status === "processing" ? 1500 : false });
  const roster = useQuery(rosterQuery());
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<WorkItem | null>(null);
  const save = useMutation({ mutationFn: () => apiRequest(`/workspace/sessions/${videoId}`, { method: "PUT", json: { title: draft!.title, athlete_id: draft!.athlete_id, opponent: draft!.opponent, session_date: draft!.session_date || null, session_type: draft!.session_type, notes: draft!.notes, expected_revision: draft!.revision } }), onSuccess: async () => { setEditing(false); await client.invalidateQueries({ queryKey: ["session-detail", videoId] }); await client.invalidateQueries({ queryKey: ["workspace"] }); await client.invalidateQueries({ queryKey: ["coach-report"] }); } });
  if (item.isPending) return <Loading />;
  if (item.error) return <ProblemNotice error={item.error} onRetry={() => void item.refetch()} />;
  if (!item.data) return null;
  const patch = (key: string, value: string | null) => setDraft((old) => old ? { ...old, [key]: value } : old);
  return <section className="workspace-panel panel-pad session-editor"><div className="section-heading"><div><p className="overline">SESSION RECORD</p><h2>경기·훈련 정보</h2></div><button className="quiet-action" disabled={readOnly} onClick={() => { setDraft(item.data!); setEditing(!editing); save.reset(); }}>{editing ? "편집 닫기" : "기록 편집"}</button></div>
    {!editing ? <div className="session-facts"><span>선수 <strong>{item.data.athlete_name ?? "미지정"}</strong></span><span>상대 <strong>{item.data.opponent || "미입력"}</strong></span><span>촬영일 <strong>{item.data.session_date ?? "미입력"}</strong></span><span>구분 <strong>{sessionLabels[item.data.session_type]}</strong></span>{item.data.notes ? <p>{item.data.notes}</p> : null}</div> : draft ? <form className="coach-form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}><label>기록 제목<input value={draft.title} maxLength={160} required onChange={(e) => patch("title", e.target.value)} /></label><div className="form-grid"><label>선수<select value={draft.athlete_id ?? ""} onChange={(e) => patch("athlete_id", e.target.value || null)}><option value="">선수 선택</option>{roster.data?.map((a) => <option key={a.athlete_id} value={a.athlete_id}>{a.name}</option>)}</select></label><label>상대 선수<input value={draft.opponent} maxLength={80} onChange={(e) => patch("opponent", e.target.value)} /></label><label>촬영일<input type="date" value={draft.session_date ?? ""} onInput={(e) => patch("session_date", e.currentTarget.value)} onChange={(e) => patch("session_date", e.target.value)} /></label><label>기록 구분<select value={draft.session_type} onChange={(e) => patch("session_type", e.target.value)}>{Object.entries(sessionLabels).map(([v, t]) => <option value={v} key={v}>{t}</option>)}</select></label></div><label>훈련 목표·코치 메모<textarea value={draft.notes} maxLength={4000} rows={3} onChange={(e) => patch("notes", e.target.value)} /></label><div><button className="primary-action" disabled={save.isPending || !draft.title.trim()}>기록 저장</button></div>{save.error ? <ProblemNotice error={save.error} onRetry={() => { void item.refetch(); setEditing(false); }} /> : null}</form> : null}
    {item.data.run_id && reviewable(item.data) ? <p><Link className="primary-action" to={`/runs/${item.data.run_id}/review`}>코칭 검수 열기 →</Link></p> : item.data.status === "ready" ? <p className="muted">영상 준비를 마쳤습니다. 분석 담당자가 결과를 연결하면 장면 검수를 시작할 수 있습니다.</p> : null}
  </section>;
}

export function RetryVideo({ videoId }: { videoId: string }) {
  const client = useQueryClient();
  const retry = useMutation({ mutationFn: () => apiRequest(`/workspace/sessions/${videoId}/retry`, { method: "POST" }), onSuccess: async () => { await client.invalidateQueries({ queryKey: ["video-summary", videoId] }); await client.invalidateQueries({ queryKey: ["workspace"] }); } });
  return <div className="notice"><p>원본 파일이 남아 있으면 영상 준비를 다시 시도할 수 있습니다.</p><button className="primary-action" onClick={() => retry.mutate()} disabled={retry.isPending}>{retry.isPending ? "다시 등록하는 중…" : "영상 준비 다시 시작"}</button>{retry.error ? <ProblemNotice error={retry.error} /> : null}</div>;
}

export function CoachReviewPage() {
  const readOnly=useWorkspaceRole()==="viewer";
  const { runId = "" } = useParams();
  const client = useQueryClient();
  const review = useQuery({ queryKey: ["coach-review", runId], queryFn: () => apiRequest<Review>(`/workspace/runs/${runId}/review`) });
  const [note, setNote] = useState("");
  const [state, setState] = useState<ReviewState>("in_review");
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (review.data) { setNote(review.data.note); setState(review.data.state === "pending" ? "in_review" : review.data.state); } }, [review.data]);
  const save = useMutation({ mutationFn: () => apiRequest<Review>(`/workspace/runs/${runId}/review`, { method: "PUT", json: { state, note, expected_revision: review.data!.revision } }), onSuccess: async () => { setSaved(true); await client.invalidateQueries({ queryKey: ["coach-review", runId] }); await client.invalidateQueries({ queryKey: ["coach-review-history", runId] }); await client.invalidateQueries({ queryKey: ["coach-report", runId] }); await client.invalidateQueries({ queryKey: ["workspace"] }); } });
  return <div className="coach-review"><div className="section-heading"><div><p className="overline">COACH REVIEW</p><h2>장면 검토와 코칭 의견</h2></div><Link className="primary-action" to={`/runs/${runId}/scene`}>근거 영상 열기 ↗</Link></div><p className="muted">영상과 사건 상세에서 근거를 확인한 뒤, 다음 훈련에 연결할 의견을 남겨 주세요.</p>
    <ReviewHistory runId={runId} />{review.isPending ? <Loading /> : null}{review.error ? <ProblemNotice error={review.error} onRetry={() => void review.refetch()} /> : null}
    {review.data ? <section className="workspace-panel panel-pad"><div className="section-heading"><h3>검수 기록</h3><span className={`workflow-status workflow-status--${review.data.state}`}>{reviewLabels[review.data.state]}</span></div><form className="coach-form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}><label>검수 상태<select value={state} onChange={(e) => { setState(e.target.value as ReviewState); setSaved(false); }}>{Object.entries(reviewLabels).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><label>코칭 의견·다음 훈련 목표<textarea rows={6} maxLength={4000} value={note} placeholder="확인한 장면, 판단이 어려운 부분, 다음 훈련에서 다룰 목표를 기록하세요." onChange={(e) => { setNote(e.target.value); setSaved(false); }} /></label><div className="review-actions"><button className="primary-action" disabled={readOnly || save.isPending || ((state === "reviewed" || state === "changes_requested") && !note.trim())}>검수 기록 저장</button><Link to={`/runs/${runId}/report`}>보고서 미리보기 →</Link></div></form>{saved ? <p role="status" className="notice">검수 기록을 저장했습니다.</p> : null}{save.error ? <ProblemNotice error={save.error} onRetry={() => void review.refetch()} /> : null}{review.data.updated_at ? <p className="muted">마지막 검토: {review.data.reviewer} · {formatDateTime(review.data.updated_at)} · 버전 {review.data.revision}</p> : null}<p className="review-boundary">검수 완료는 코치의 확인 이력입니다. 후보 사건의 검증·접촉·명중 판정을 자동으로 바꾸지 않습니다.</p></section> : null}
  </div>;
}

type Report = { run_id: string; session: WorkItem; review: Review; dataset_kind: string; expert_validated: boolean; events: Array<{ event_id: string; event_label: string; start_timestamp_us: number; end_timestamp_us: number; status: string; verified: boolean; outcome: string | null }>; findings: Array<{ finding_id: string; status: string; text: string | null; hold_reason: string | null }>; notes: string[] };
export function CoachReportPage() {
  const { runId = "" } = useParams();
  const query = useQuery({ queryKey: ["coach-report", runId], queryFn: () => apiRequest<Report>(`/workspace/runs/${runId}/report`) });
  if (query.isPending) return <Loading />;
  if (query.error || !query.data) return <ProblemNotice error={query.error} onRetry={() => void query.refetch()} />;
  const r = query.data;
  const final = r.review.state === "reviewed" && r.session.run_id === runId;
  return <article className="coach-report">
    <div className="report-toolbar"><Link to={`/videos/${r.session.video_id}`}>영상 기록</Link><div><button className="quiet-action" onClick={() => window.print()}>인쇄 / PDF 저장</button>{final ? <a className="primary-action" href={`/api/v1/workspace/runs/${runId}/report.csv`} download>사건 목록 CSV</a> : null}</div></div>
    <header className="report-heading"><p className="overline">COACHDESK / COACHING REPORT</p><span className={`workflow-status workflow-status--${r.review.state}`}>{final ? "코치 확인 완료" : "코치 확인 전 · 초안"}</span><h1>코칭 보고서</h1><h2>{r.session.title}</h2><p>{r.session.athlete_name ?? "영상에서 선택한 분석 선수"} · {label(SPORT, r.session.sport)} · {r.session.session_date ?? "촬영일 미입력"}</p></header>
    {r.dataset_kind === "model" ? <CoachingDraft runId={runId} /> : null}
    {r.findings.length ? <section><h2>연결된 분석 의견</h2>{r.findings.map(f=><p key={f.finding_id}>{f.text ?? `판단 보류: ${f.hold_reason ?? "근거 부족"}`}</p>)}</section> : null}
    <ReportCoachNote runId={runId} review={r.review}/>
    {r.events.length ? <section><h2>기록된 동작 후보</h2><div className="table-scroll"><table className="coach-table"><thead><tr><th>구간</th><th>동작 후보</th><th>상태</th></tr></thead><tbody>{r.events.map(e=><tr key={e.event_id}><td><Link to={`/runs/${runId}/scene?from_us=${e.start_timestamp_us}&to_us=${e.end_timestamp_us}`}>{formatTime(e.start_timestamp_us)}–{formatTime(e.end_timestamp_us)}</Link></td><td>{label(EVENT_TYPE,e.event_label)}</td><td>{e.verified?"확인됨":"영상 확인 필요"}</td></tr>)}</tbody></table></div></section> : null}
    <details className="report-method"><summary>분석 도구와 기록 정보</summary>{r.notes.map((n,i)=><p key={i}>{n.replace("외부 모델의 검증 전 후보.","모델의 검증 전 후보.")}</p>)}<p>분석 {runId} · 코치 메모 버전 {r.review.revision}</p></details>
    <footer>자동 제안은 영상 확인 전 초안입니다. 명중·득점·경기 결과를 확정하지 않습니다.</footer>
  </article>;
}

function ReportCoachNote({runId,review}:{runId:string;review:Review}) {
  const readOnly=useWorkspaceRole()==="viewer";
  const client=useQueryClient();
  const [note,setNote]=useState(review.note);
  useEffect(()=>setNote(review.note),[review.note]);
  const save=useMutation({mutationFn:()=>apiRequest(`/workspace/runs/${runId}/review`,{method:"PUT",json:{state:review.state,note,expected_revision:review.revision}}),onSuccess:async()=>{await Promise.all([["coach-report",runId],["coach-review",runId],["coach-review-history",runId],["workspace"]].map(queryKey=>client.invalidateQueries({queryKey})));}});
  return <section><h2>코치 메모와 다음 훈련 목표</h2><p>선택 사항입니다. 자동 관찰과 연습 제안은 메모를 작성하지 않아도 확인할 수 있습니다. 추가로 남길 의견이나 훈련 목표가 있을 때 사용하세요.</p>{review.note ? <p className="report-print-note">{review.note}</p> : null}<form className="coach-form" onSubmit={e=>{e.preventDefault();save.mutate();}}><label>확인한 내용·연습할 목표<textarea value={note} maxLength={4000} rows={4} disabled={readOnly} placeholder="예: 확인한 장면의 시간, 다음 훈련에서 확인할 손 위치, 재촬영할 과제를 적어 주세요." onChange={e=>{setNote(e.target.value);save.reset();}}/></label><button className="primary-action" disabled={readOnly||save.isPending||note===review.note||((review.state==="reviewed"||review.state==="changes_requested")&&!note.trim())}>보고서에 메모 저장</button></form>{save.isSuccess?<p role="status">코치 메모를 저장했습니다.</p>:null}{save.error?<ProblemNotice error={save.error}/>:null}{review.reviewer?<p className="muted">작성자 {review.reviewer}</p>:null}</section>;
}

function ReviewHistory({ runId }: { runId: string }) {
  const history = useQuery({ queryKey: ["coach-review-history", runId], queryFn: () => apiRequest<Review[]>(`/workspace/runs/${runId}/review-history`) });
  return <details className="review-history"><summary>이전 검수 이력 ({history.data?.length ?? 0})</summary>{history.error ? <ProblemNotice error={history.error} onRetry={() => void history.refetch()} /> : null}{history.data?.map((r) => <div key={r.revision}><strong>버전 {r.revision} · {reviewLabels[r.state]}</strong><p>{r.note || "의견 없음"}</p><small>{r.reviewer} · {r.updated_at ? formatDateTime(r.updated_at) : ""}</small></div>)}</details>;
}

export function OperationsPage() {
  const status = useQuery({ queryKey: ["workspace-status"], queryFn: () => apiRequest<Status>("/workspace/status"), refetchInterval: 10_000 });
  const activity = useQuery({ queryKey: ["workspace-activity"], queryFn: () => apiRequest<Activity[]>("/workspace/activity"), refetchInterval: 15_000 });
  const s = status.data;
  return <div className="coach-page"><div className="page-heading"><div><p className="overline">WORKSPACE OPERATIONS</p><h1>운영 현황</h1><p className="muted">작업에 필요한 연결 상태와 최근 변경 이력을 확인합니다.</p></div></div>{status.isPending ? <Loading /> : null}{status.error ? <ProblemNotice error={status.error} onRetry={() => void status.refetch()} /> : null}{s ? <><div className="operations-grid">{[["기록 저장소", s.database === "ok" ? "연결됨" : "확인 필요"], ["영상 처리", s.video_tools ? "준비됨" : "도구 확인 필요"], ["활성 워커", `${s.active_workers}개`], ["저장 공간", `${formatBytes(s.disk_free_bytes)} 여유`]].map(([name, value]) => <div className="workspace-panel panel-pad" key={name}><span className="muted">{name}</span><h2>{value}</h2></div>)}</div><section className="workspace-panel panel-pad"><h2>분석실 운영 설정</h2><dl className="operations-facts"><div><dt>접근 방식</dt><dd>{s.auth_mode === "session" ? "초대 계정 · 세션 인증" : "단일 사용자 로컬 접속 · 로그인 비활성"}</dd></div><div><dt>영상 보관</dt><dd>{s.retention_days ? `준비 후 ${s.retention_days}일` : "사용자가 삭제할 때까지"}</dd></div><div><dt>외부 분석 워커</dt><dd>{s.analysis_workers ? `${s.analysis_workers}개 연결 · 요청한 작업을 수행` : "연결 대기 · 파일로 결과 연결 가능"}</dd></div><div><dt>예제 데이터</dt><dd>{s.fixtures_enabled ? "개발 예제 사용" : "운영 기록과 분리"}</dd></div></dl><p className="muted">개인 공간과 팀 공간의 기록은 분리됩니다. 팀과 작업 공간에서 편집·열람 권한을 관리할 수 있습니다.</p></section></> : null}<section className="workspace-panel panel-pad"><h2>최근 작업 이력</h2>{activity.error ? <ProblemNotice error={activity.error} onRetry={() => void activity.refetch()} /> : null}{activity.data?.length === 0 ? <p className="muted">아직 기록된 변경이 없습니다.</p> : null}<ul className="activity-list">{activity.data?.map((a) => <li key={a.activity_id}><span className="activity-dot" /><div><strong>{activityLabels[a.action] ?? a.action}</strong><span>{a.actor}</span></div><time>{formatDateTime(a.at)}</time></li>)}</ul></section></div>;
}
