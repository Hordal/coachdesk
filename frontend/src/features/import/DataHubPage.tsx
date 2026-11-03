import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, apiRequest, type RunSummary } from "../../api/client";
import type { components } from "../../api/schema";
import { formatTime } from "../../lib/format";

type PoseSet = components["schemas"]["PoseSetView"];
type Kind = "poses" | "actions";
const MAX_BYTES = 100 * 1024 * 1024;

export async function readRows(file: File): Promise<unknown[]> {
  if (file.size > MAX_BYTES) throw new Error(`${file.name}: 100 MiB 이하 파일을 선택하세요.`);
  const text = (await file.text()).replace(/^\uFEFF/, "").trim();
  if (!text) throw new Error(`${file.name}: 파일이 비어 있습니다.`);
  if (text.startsWith("[")) {
    try { const rows: unknown = JSON.parse(text); if (Array.isArray(rows)) return rows; }
    catch { throw new Error(`${file.name}: JSON 배열을 읽을 수 없습니다.`); }
  }
  return text.split(/\r?\n/).flatMap((line, index) => {
    if (!line.trim()) return [];
    try { return [JSON.parse(line) as unknown]; }
    catch { throw new Error(`${file.name}: ${index + 1}번째 줄이 올바른 JSON이 아닙니다.`); }
  });
}

const contracts = [
  { kind: "observations", name: "검출·추적", files: "metadata.json + tracking_frames.jsonl", detail: "detection_frames.jsonl은 선택입니다. 영상 프레임 수와 실제 표시 시각을 대조하고, 선수는 사용자가 지정합니다." },
  { kind: "poses", name: "포즈·품질", files: "pose_observations.jsonl / pose_quality_frames.jsonl", detail: "pose_quality.jsonl과 window_candidates.jsonl을 함께 받을 수 있습니다. 33개 관절과 visibility·presence를 보존합니다." },
  { kind: "actions", name: "동작 후보", files: "action_candidates.jsonl", detail: "CANDIDATE · verified=false · outcome=UNKNOWN · role=UNRESOLVED인 연구 결과를 받습니다. 선수 역할은 직접 지정한 track_id에 한해 연결하고, 접촉은 확정하지 않습니다." },
  { kind: "manual", name: "사건·판정", files: "manual_import.json", detail: "사건·상태·판정과 집계 정의를 받습니다. 집계 수치와 요약 문장은 서버가 계산하며 이전 결과를 보존합니다." },
];

export function DataGuidePage() {
  return <>
    <div className="intake-hero"><p className="eyebrow">MMA COACH / DATA WORKSPACE</p><h1>데이터 연결 가이드</h1>
      <p>영상에서 분석 데이터까지, 근거를 따라 연결합니다.</p><Link className="action-link" to="/upload">영상 올리기 →</Link></div>
    <div className="intake-steps" aria-label="작업 순서"><span>01 영상 준비</span><span>02 분석 결과 연결</span><span>03 선수 지정</span><span>04 근거 복기</span></div>
    <p className="notice notice--hold">외부 모델을 연결하면 영상 상세에서 검출·추적과 동작 분석을 요청할 수 있습니다. 이 화면에서는 담당자가 만든 파일이나 수동 판정을 가져옵니다.</p>
    <div className="intake-grid">{contracts.map((c, i) => <section className="card intake-card" key={c.kind}>
      <p className="eyebrow">0{i + 1} / INPUT</p><h2>{c.name}</h2><code>{c.files}</code><p className="muted">{c.detail}</p>
      <a href={`/api/v1/data-contracts/${c.kind}`} download>입력 형식 내려받기 ↗</a>
    </section>)}</div>
    <section className="card"><h2>시간과 좌표의 기준</h2><p>시간은 원본 재생 시작 기준 정수 마이크로초(us), 구간은 [시작, 끝)입니다. 프레임 번호나 평균 FPS로 시각을 만들지 않고 원본 PTS와 대조합니다.</p>
      <p className="muted">JSONL은 한 줄에 JSON 객체 하나입니다. 포즈와 동작 후보는 JSON 배열 파일도 받습니다. 알 수 없는 값은 0으로 바꾸지 않습니다.</p></section>
    <p><Link to="/">내 영상으로 돌아가기</Link></p>
  </>;
}

export function DataHubPage() {
  const { videoId = "" } = useParams();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [kind, setKind] = useState<Kind>("poses");
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const pose = useQuery({ queryKey: ["pose-set", videoId], queryFn: async () => {
    try { return await apiRequest<PoseSet>(`/videos/${videoId}/poses`); }
    catch (e) { if (e instanceof ApiError && e.code === "poses_not_found") return null; throw e; }
  }, retry: false });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !files.primary) return;
    setBusy(true); setFailure([]); setSaved(false);
    try {
      const rows = await readRows(files.primary);
      if (kind === "poses") {
        const body = { source: "team-research", poses: rows,
          quality: files.quality ? await readRows(files.quality) : [],
          windows: files.windows ? await readRows(files.windows) : [] };
        await apiRequest<PoseSet>(`/videos/${videoId}/poses`, { method: "POST", json: body });
        await client.invalidateQueries({ queryKey: ["pose-set", videoId] });
        setSaved(true);
      } else {
        const run = await apiRequest<RunSummary>(`/videos/${videoId}/actions`, { method: "POST", json: { source: "team-research", events: rows } });
        await client.invalidateQueries({ queryKey: ["runs"] });
        await client.invalidateQueries({ queryKey: ["video-summary", videoId] });
        await client.invalidateQueries({ queryKey: ["session-detail", videoId] });
        await client.invalidateQueries({ queryKey: ["workspace"] });
        void navigate(`/runs/${run.run_id}/scene`);
      }
    } catch (e) {
      setFailure(e instanceof ApiError ? [e.message, ...e.details, ...(e.traceId ? [`확인 번호: ${e.traceId}`] : [])] : [e instanceof Error ? e.message : "가져오지 못했습니다."]);
    } finally { setBusy(false); }
  }

  const field = (key: string, title: string) => <label className="intake-file">{title}<input type="file" accept=".jsonl,.json,application/json" disabled={busy}
    key={`${kind}-${key}`} onChange={(e) => { setFiles((old) => ({ ...old, [key]: e.target.files?.[0] ?? null })); setSaved(false); }} /></label>;

  return <>
    <p className="breadcrumb"><Link to={`/videos/${videoId}`}>영상</Link> / 데이터 연결</p>
    <div className="intake-hero"><p className="eyebrow">ANALYSIS / IMPORT</p><h1>분석 데이터 연결</h1><p>분석 도구의 산출물을 원본 영상에 연결하고 검증합니다.</p></div>
    <div className="intake-grid">
      <Link className="card intake-nav" to={`/videos/${videoId}/observations`}><strong>검출·추적 / 선수 지정</strong><span className="muted">상자와 트랙을 확인하고 두 선수를 선택합니다. →</span></Link>
      <Link className="card intake-nav" to={`/videos/${videoId}/import`}><strong>사건·판정</strong><span className="muted">사람이 입력한 사건·상태와 판정을 가져옵니다. →</span></Link>
    </div>
    <section className="card"><h2>연구 산출물 가져오기</h2><p className="muted">파일당 최대 100 MiB. JSONL 또는 JSON 배열을 선택하세요. 영상 준비가 끝난 뒤 연결할 수 있습니다.</p>
      <div className="intake-tabs" role="group" aria-label="데이터 종류">
        <button type="button" aria-pressed={kind === "poses"} disabled={busy} onClick={() => { setKind("poses"); setFiles({}); setFailure([]); setSaved(false); }}>포즈·품질</button>
        <button type="button" aria-pressed={kind === "actions"} disabled={busy} onClick={() => { setKind("actions"); setFiles({}); setFailure([]); setSaved(false); }}>동작 후보</button>
      </div>
      <form onSubmit={(e) => void submit(e)}>
        {field("primary", kind === "poses" ? "포즈 관측 파일 (필수)" : "동작 후보 파일 (필수)")}
        {kind === "poses" ? <>{field("quality", "포즈 품질 파일 (선택)")}{field("windows", "후보 구간 파일 (선택)")}</> : <p className="notice notice--hold">검증 전 후보만 받습니다. 명중·접촉·선수 역할을 확정한 데이터는 사건·판정 가져오기를 사용하세요.</p>}
        <button type="submit" className="intake-submit" disabled={busy || !files.primary}>{busy ? "검증하고 저장하는 중…" : "검증 후 가져오기"}</button>
      </form>
      {failure.length > 0 ? <div role="alert" className="notice notice--error">{failure.map((f, i) => <p key={i}>{f}</p>)}</div> : null}
      {saved ? <p role="status" className="notice">포즈 데이터를 저장했습니다. 아래 요약이 최신 결과입니다.</p> : null}
    </section>
    {pose.isPending ? <p role="status">연결한 포즈 데이터를 확인하는 중…</p> : null}
    {pose.error ? <p role="alert" className="notice notice--error">포즈 요약을 불러오지 못했습니다. <button onClick={() => void pose.refetch()}>다시 확인</button></p> : null}
    {pose.data ? <section className="card"><h2>연결한 포즈 데이터</h2><div className="intake-stats">
      <div><strong>{pose.data.row_count.toLocaleString()}</strong><span>포즈 관측 줄</span></div><div><strong>{pose.data.frame_count.toLocaleString()}</strong><span>관측 프레임</span></div>
      <div><strong>{pose.data.no_landmark_rows.toLocaleString()}</strong><span>관절 없는 줄</span></div><div><strong>{pose.data.quality_row_count.toLocaleString()}</strong><span>품질 기록</span></div></div>
      <p className="muted">{pose.data.first_us !== null && pose.data.last_us !== null ? `${formatTime(pose.data.first_us)}–${formatTime(pose.data.last_us)} · ` : ""}출처: {pose.data.source}</p>
      <ul>{Object.entries(pose.data.status_counts).map(([name, count]) => <li key={name}>{name}: {count.toLocaleString()}줄</li>)}</ul>
      <p className="notice notice--muted">관측 수와 상태 분포이며 정확도가 아닙니다. candidate 번호는 선수 신원이 아닙니다. 연구용 33점 데이터의 재생 오버레이 연결은 아직 지원하지 않습니다.</p>
    </section> : !pose.isPending && !pose.error ? <p className="muted">아직 연결한 포즈 데이터가 없습니다.</p> : null}
    <p><Link to="/data-guide">데이터 형식과 연결 방법 보기 →</Link></p>
  </>;
}
