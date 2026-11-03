import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, apiRequest, type RunSummary } from "../../api/client";

const MAX_BYTES = 20 * 1024 * 1024;

interface Failure {
  title: string;
  details: string[];
  traceId: string | null;
}

/** 사람이 입력한 사건·판정 JSON을 영상에 연결해 새 실행으로 가져온다. 검증은 서버가 하고 오류 목록을 그대로 보여 준다. */
export function ImportPage() {
  const { videoId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    setFailure(null);
    if (file.size > MAX_BYTES) {
      setFailure({ title: "파일이 너무 큽니다", details: [], traceId: null });
      return;
    }
    let json: unknown;
    try {
      json = JSON.parse(await file.text());
    } catch {
      setFailure({ title: "JSON 파일로 읽을 수 없습니다", details: [], traceId: null });
      return;
    }
    setBusy(true);
    try {
      const run = await apiRequest<RunSummary>(`/videos/${videoId}/imports`, { method: "POST", json });
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: ["video-summary", videoId] });
      void client.invalidateQueries({ queryKey: ["session-detail", videoId] });
      void client.invalidateQueries({ queryKey: ["workspace"] });
      void navigate(`/runs/${run.run_id}`);
    } catch (e) {
      if (e instanceof ApiError) setFailure({ title: e.message, details: e.details, traceId: e.traceId });
      else setFailure({ title: "가져오지 못했습니다", details: [], traceId: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>사건·판정 가져오기</h1>
      <p className="muted">
        사람이 입력한 사건과 판정 파일(JSON)을 이 영상에 연결합니다. 집계의 분자·분모와 요약 문장은 입력한 판정에서 서버가 다시 계산하며,
        가져온 결과는 전문가 검수를 거친 것으로 표시되지 않습니다. 이전 실행은 그대로 남습니다.
      </p>
      <form className="card" onSubmit={(e) => void submit(e)}>
        <p>
          <label>
            입력 파일{" "}
            <input type="file" accept=".json,application/json" disabled={busy} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        </p>
        <p>
          <button type="submit" disabled={!file || busy}>
            가져오기
          </button>
        </p>
      </form>

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

      <p>
        <Link to={`/videos/${videoId}`}>영상으로 돌아가기</Link>
      </p>
    </>
  );
}
