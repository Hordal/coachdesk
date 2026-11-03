import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, type UploadView } from "../../api/client";
import { formatBytes, formatPercent } from "../../lib/format";
import { label, SPORT, UPLOAD_ERROR } from "../../strings/ko";
import { abortUpload, uploadFile, type Sport, type UploadProgress } from "../../upload/engine";

const SPORTS: Sport[] = ["boxing", "kickboxing", "muay_thai", "mma"];

function messageFor(error: unknown): { text: string; traceId: string | null } {
  if (error instanceof ApiError) {
    return { text: UPLOAD_ERROR[error.code] ?? error.message, traceId: error.traceId };
  }
  return { text: "업로드하지 못했습니다.", traceId: null };
}

export function UploadPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [sport, setSport] = useState<Sport>("boxing");
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<{ text: string; traceId: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const uploadRef = useRef<UploadView | null>(null);

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    setError(null);
    setBusy(true);
    controller.current = new AbortController();
    try {
      const done = await uploadFile(file, sport, {
        signal: controller.current.signal,
        onProgress: setProgress,
        onUpload: (u) => {
          uploadRef.current = u;
        },
      });
      void client.invalidateQueries({ queryKey: ["videos"] });
      void client.invalidateQueries({ queryKey: ["workspace"] });
      if (done.video_id) void navigate(`/videos/${done.video_id}`);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    controller.current?.abort();
    try {
      if (file) await abortUpload(file, sport, uploadRef.current?.upload_id ?? null);
    } catch {
      /* 임시 조각 삭제에 실패해도 서버가 기한이 지난 업로드를 정리한다 */
    }
    uploadRef.current = null;
    setProgress(null);
  }

  const ratio = progress && progress.totalBytes > 0 ? progress.sentBytes / progress.totalBytes : 0;

  return (
    <>
      <h1>영상 올리기</h1>
      <p className="muted">mp4 또는 mov 파일을 올릴 수 있습니다. 중간에 끊기면 같은 파일을 다시 선택해 이어 올릴 수 있습니다.</p>

      <form className="card coach-upload-form" onSubmit={(e) => void start(e)}>
        <p>
          <label>
            영상 파일{" "}
            <input
              type="file"
              accept=".mp4,.mov,video/mp4,video/quicktime"
              disabled={busy}
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setProgress(null);
                setError(null);
              }}
            />
          </label>
        </p>
        {file ? <p className="muted">{file.name} · {formatBytes(file.size)}</p> : null}
        <p>
          <label>
            종목{" "}
            <select value={sport} disabled={busy} onChange={(e) => setSport(e.target.value as Sport)}>
              {SPORTS.map((s) => (
                <option key={s} value={s}>
                  {label(SPORT, s)}
                </option>
              ))}
            </select>
          </label>
        </p>
        <p>
          <button type="submit" disabled={!file || busy}>
            올리기
          </button>{" "}
          {busy ? (
            <button type="button" onClick={() => void cancel()}>
              취소
            </button>
          ) : null}
        </p>
      </form>

      {progress ? (
        <div className="card" role="status">
          <p>
            <progress max={1} value={ratio} aria-label="업로드 진행률" /> {formatPercent(ratio)}
          </p>
          <p className="muted">
            {formatBytes(progress.sentBytes)} / {formatBytes(progress.totalBytes)} · 조각 {progress.partsDone} / {progress.partCount}
          </p>
        </div>
      ) : null}

      {error ? (
        <div className="notice notice--error" role="alert">
          <p>
            <strong>{error.text}</strong>
          </p>
          {error.traceId ? <p className="muted">문의 시 확인 번호: {error.traceId}</p> : null}
        </div>
      ) : null}

      <p>
        <Link to="/">목록으로</Link>
      </p>
    </>
  );
}
