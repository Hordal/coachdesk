import { useState, type FormEvent } from "react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";

import { ApiError, type RunSummary } from "../../api/client";
import {
  useContextNotes,
  useCorrectionHistory,
  useCorrectionPreview,
  useCreateContextNote,
  useCreateCorrection,
  useCreateReport,
  useReports,
} from "../../api/hooks";
import { Loading } from "../../components/ProblemNotice";
import { formatDateTime, formatRange } from "../../lib/format";
import { CORRECTION_ERROR, label, REPORT_CATEGORY, ROLE } from "../../strings/ko";

const CATEGORIES = Object.keys(REPORT_CATEGORY);

function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  return (
    <p className="notice notice--error" role="alert">
      {(e && CORRECTION_ERROR[e.code]) ?? e?.message ?? "처리하지 못했습니다"}
      {e?.traceId ? <span className="muted"> (문의 시 확인 번호: {e.traceId})</span> : null}
    </p>
  );
}

function Designation({ run }: { run: RunSummary }) {
  const navigate = useNavigate();
  const [chosen, setChosen] = useState<string>("");
  const preview = useCorrectionPreview(run.run_id);
  const create = useCreateCorrection(run.run_id);
  const outdated = run.superseded_by !== null;
  const current = run.participants.find((p) => p.user_designated)?.participant_id ?? null;

  if (run.participants.length !== 2) {
    return <p className="muted">선수가 두 명으로 확인된 결과에서만 본인을 지정할 수 있습니다.</p>;
  }

  return (
    <>
      <p className="muted">
        현재 본인으로 지정된 선수: {current ? <strong>{current}</strong> : "지정되지 않음"}. 정정하면 새 결과가 만들어지고 이전 결과는 그대로 남습니다.
      </p>
      <fieldset disabled={outdated}>
        <legend>본인인 선수</legend>
        {run.participants.map((p) => (
          <label key={p.participant_id} className="radio-line">
            <input
              type="radio"
              name="me"
              value={p.participant_id}
              checked={chosen === p.participant_id}
              onChange={() => {
                setChosen(p.participant_id);
                preview.mutate(p.participant_id);
                create.reset();
              }}
            />{" "}
            선수 {p.participant_id} <span className="muted">(현재 {label(ROLE, p.role)})</span>
          </label>
        ))}
      </fieldset>
      {outdated ? <p className="muted">이 결과는 이미 정정되어 최신이 아닙니다. 최신 결과에서 정정해 주세요.</p> : null}

      {preview.data ? (
        <div className="card" aria-live="polite">
          <h3>적용 내용 미리 보기</h3>
          {preview.data.changes.length === 0 ? (
            <p>{preview.data.note}</p>
          ) : (
            <>
              <ul>
                {preview.data.changes.map((c) => (
                  <li key={c.participant_id}>
                    선수 {c.participant_id}: {label(ROLE, c.role_before)} → {label(ROLE, c.role_after)}
                  </li>
                ))}
              </ul>
              <p>{preview.data.note}</p>
            </>
          )}
        </div>
      ) : null}
      <ErrorLine error={preview.error ?? create.error} />

      <p>
        <button
          type="button"
          disabled={outdated || !chosen || !preview.data || preview.data.changes.length === 0 || create.isPending}
          onClick={() => create.mutate(chosen, { onSuccess: (r) => void navigate(`/runs/${r.run_id}/corrections`) })}
        >
          이 내용으로 정정
        </button>
      </p>
    </>
  );
}

function History({ runId }: { runId: string }) {
  const history = useCorrectionHistory(runId);
  if (history.isPending) return <Loading />;
  if (!history.data || history.data.entries.length === 0) return <p className="muted">정정 이력이 없습니다.</p>;
  const { original_run_id: original, latest_run_id: latest, entries } = history.data;
  const steps = [original, ...entries.map((e) => e.run_id)];
  return (
    <ol className="correction-history">
      {steps.map((id, i) => (
        <li key={id}>
          <Link to={`/runs/${id}`}>{i === 0 ? "최초 결과" : `정정 ${i}`}</Link>
          {id === runId ? " (지금 보는 결과)" : ""}
          {id === latest ? " · 최신" : ""}
          {i > 0 ? <span className="muted"> · {formatDateTime(entries[i - 1]?.created_at ?? "")}</span> : null}
        </li>
      ))}
    </ol>
  );
}

function ReportForm({ run }: { run: RunSummary }) {
  const reports = useReports(run.run_id);
  const create = useCreateReport(run.run_id);
  const [category, setCategory] = useState(CATEGORIES[0] ?? "other");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { category: category as never, subject_ref: subject.trim() || null, text: text.trim() },
      { onSuccess: () => { setText(""); setSubject(""); } },
    );
  }

  return (
    <>
      <form className="card" onSubmit={submit}>
        <p>
          <label>
            종류{" "}
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{label(REPORT_CATEGORY, c)}</option>
              ))}
            </select>
          </label>{" "}
          <label>
            관련 번호(선택){" "}
            <input value={subject} maxLength={128} placeholder="예: E01" onChange={(e) => setSubject(e.target.value)} />
          </label>
        </p>
        <p>
          <label>
            내용 <br />
            <textarea value={text} maxLength={2000} rows={3} required onChange={(e) => setText(e.target.value)} />
          </label>
        </p>
        <p>
          <button type="submit" disabled={!text.trim() || create.isPending}>신고 접수</button>
        </p>
        <ErrorLine error={create.error} />
        <p className="muted">신고는 접수만 하며 결과를 바꾸지 않습니다.</p>
      </form>
      <ul className="member-list">
        {reports.data?.map((r) => (
          <li key={r.report_id}>
            [{label(REPORT_CATEGORY, r.category)}]{r.subject_ref ? ` ${r.subject_ref}` : ""} {r.text}
          </li>
        ))}
      </ul>
    </>
  );
}

function NoteForm({ run }: { run: RunSummary }) {
  const notes = useContextNotes(run.run_id);
  const create = useCreateContextNote(run.run_id);
  const [participant, setParticipant] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [text, setText] = useState("");
  const toUs = (s: string) => (s.trim() === "" ? null : Math.round(Number(s) * 1_000_000));

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      { participant_id: participant || null, start_us: toUs(from), end_us: toUs(to), text: text.trim() },
      { onSuccess: () => { setText(""); setFrom(""); setTo(""); } },
    );
  }

  return (
    <>
      <form className="card" onSubmit={submit}>
        <p>
          <label>
            선수{" "}
            <select value={participant} onChange={(e) => setParticipant(e.target.value)}>
              <option value="">전체</option>
              {run.participants.map((p) => (
                <option key={p.participant_id} value={p.participant_id}>{p.participant_id}</option>
              ))}
            </select>
          </label>{" "}
          <label>
            시작(초){" "}
            <input inputMode="decimal" size={6} value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>{" "}
          <label>
            끝(초){" "}
            <input inputMode="decimal" size={6} value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </p>
        <p>
          <label>
            설명 <br />
            <textarea value={text} maxLength={2000} rows={3} required onChange={(e) => setText(e.target.value)} />
          </label>
        </p>
        <p>
          <button type="submit" disabled={!text.trim() || create.isPending}>설명 저장</button>
        </p>
        <ErrorLine error={create.error} />
        <p className="muted">직접 적은 설명으로 따로 보관하며 분석 결과와 섞지 않습니다.</p>
      </form>
      <ul className="member-list">
        {notes.data?.map((n) => (
          <li key={n.note_id}>
            {n.participant_id ? `선수 ${n.participant_id} · ` : ""}
            {n.start_us !== null && n.end_us !== null ? `${formatRange(n.start_us, n.end_us)} · ` : ""}
            {n.text}
          </li>
        ))}
      </ul>
    </>
  );
}

/** 선수 지정 정정, 정정 이력, 오류 신고, 의도 설명(상세 설계 11). */
export function CorrectionsPage() {
  const run = useOutletContext<RunSummary>();
  return (
    <>
      <h2>정정·기록</h2>
      <section aria-labelledby="designation">
        <h3 id="designation">본인 지정</h3>
        <Designation run={run} />
      </section>
      <section aria-labelledby="history">
        <h3 id="history">정정 이력</h3>
        <History runId={run.run_id} />
      </section>
      <section aria-labelledby="report">
        <h3 id="report">분석 오류 신고</h3>
        <ReportForm run={run} />
      </section>
      <section aria-labelledby="note">
        <h3 id="note">의도 설명</h3>
        <NoteForm run={run} />
      </section>
    </>
  );
}
