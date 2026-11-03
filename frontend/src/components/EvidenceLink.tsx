import { Link } from "react-router-dom";

import type { EvidenceView } from "../api/client";
import { formatRange } from "../lib/format";
import { label, REASON, T } from "../strings/ko";
import { AvailabilityBadge } from "./StatusBadge";

/** 근거 구간으로 이동하는 링크. 영상을 재생할 수 없어도 구간과 선수 정보는 보여준다. */
export function EvidenceLink({
  runId,
  view,
  showStatus = true,
}: {
  runId: string;
  view: EvidenceView;
  /** false이면 재생 가능 여부 배지를 숨긴다(같은 화면에서 한 번만 안내할 때). */
  showStatus?: boolean;
}) {
  const { evidence } = view;
  const to = `/runs/${runId}/scene?from_us=${evidence.start_us}&to_us=${evidence.end_us}`;
  return (
    <span className="evidence-link">
      <Link to={to}>{formatRange(evidence.start_us, evidence.end_us)}</Link>
      {showStatus ? <AvailabilityBadge availability={evidence.availability} /> : null}
      {!view.playable && view.reason_code ? (
        <span className="visually-hidden">{label(REASON, view.reason_code)}</span>
      ) : null}
    </span>
  );
}

/** 근거 영상을 재생할 수 없을 때의 안내(원본 만료·삭제·미연결 포함). */
export function ExpiredNotice({ reasonCode }: { reasonCode?: string | null }) {
  return (
    <p className="notice notice--muted" role="note">
      {T.evidenceNoVideo}
      {reasonCode ? <span> ({label(REASON, reasonCode)})</span> : null}
    </p>
  );
}
