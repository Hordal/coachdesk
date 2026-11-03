import { ApiError } from "../api/client";
import { T } from "../strings/ko";

export function Loading() {
  return (
    <p className="notice notice--muted" role="status">
      {T.loading}…
    </p>
  );
}

/** 오류는 안전한 제목과 문의용 trace_id만 보여준다(14). */
export function ProblemNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div className="notice notice--error" role="alert">
      <p>
        <strong>{apiError?.message ?? "알 수 없는 오류가 발생했습니다"}</strong>
      </p>
      {apiError?.traceId ? <p className="muted">문의 시 확인 번호: {apiError.traceId}</p> : null}
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          {T.retry}
        </button>
      ) : null}
    </div>
  );
}
