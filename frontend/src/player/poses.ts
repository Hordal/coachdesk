import type { PoseSample } from "../api/client";

export type PoseIndex = ReadonlyMap<string, readonly PoseSample[]>;

/** 선수별로 시각 순서로 정렬한다. */
export function buildPoseIndex(samples: readonly PoseSample[]): PoseIndex {
  const map = new Map<string, PoseSample[]>();
  for (const s of samples) {
    const list = map.get(s.participant_id);
    if (list) list.push(s);
    else map.set(s.participant_id, [s]);
  }
  for (const list of map.values()) list.sort((a, b) => a.time_us - b.time_us);
  return map;
}

/**
 * timeUs에 가장 가까운 관측. 허용 오차(toleranceUs) 밖이면 undefined이다.
 * 오래된 관측을 현재 것처럼 쓰지 않으며, 관측이 없는 시각은 비워 둔다(19).
 */
export function poseAt(index: PoseIndex, participantId: string, timeUs: number, toleranceUs: number): PoseSample | undefined {
  const list = index.get(participantId);
  if (!list || list.length === 0) return undefined;
  let lo = 0;
  let hi = list.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid]!.time_us < timeUs) lo = mid + 1;
    else hi = mid;
  }
  const candidates = [list[lo], list[lo - 1]].filter((s): s is PoseSample => s !== undefined);
  let best: PoseSample | undefined;
  for (const s of candidates) {
    const distance = Math.abs(s.time_us - timeUs);
    if (distance <= toleranceUs && (best === undefined || distance < Math.abs(best.time_us - timeUs))) best = s;
  }
  return best;
}
