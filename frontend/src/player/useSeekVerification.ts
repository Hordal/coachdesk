import { useEffect } from "react";

import { presentedMatches, type Frame } from "./frames";

export const SEEK_VERIFY_TIMEOUT_MS = 500;

/**
 * 프레임으로 이동한 뒤 실제로 그 프레임이 표시되었는지 확인한다(상세 설계 05).
 * 목표 프레임이 표시되면 ‘shown’, 일정 시간 안에 표시되지 않으면 ‘skipped’로 알린다.
 * 브라우저는 영상 파일의 프레임 길이 정보 등에 따라 프레임을 건너뛸 수 있으므로,
 * 건너뛴 경우에는 호출한 쪽에서 디코더가 식별한 정지 프레임으로 전환한다.
 */
export function useSeekVerification(
  presentedUs: number | null,
  target: Frame | null,
  onResolved: (outcome: "shown" | "skipped", frame: Frame) => void,
  timeoutMs = SEEK_VERIFY_TIMEOUT_MS,
) {
  useEffect(() => {
    if (!target) return;
    if (presentedMatches(presentedUs, target)) {
      onResolved("shown", target);
      return;
    }
    // 표시된 프레임이 바뀔 때마다 기다림을 다시 시작한다. 이전 프레임이 먼저 보고될 수 있기 때문이다.
    const id = setTimeout(() => onResolved("skipped", target), timeoutMs);
    return () => clearTimeout(id);
  }, [presentedUs, target, onResolved, timeoutMs]);
}
