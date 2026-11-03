/**
 * 프레임 표 계산(상세 설계 05).
 * 프레임 시각은 서버가 ffprobe로 읽은 실제 표시 시각이다. 명목 fps(1/fps)로 시각을 만들지 않는다.
 */

export interface Frame {
  index: number;
  pts_us: number;
}

/** 표시 시각이 timeUs 이하인 마지막 프레임(그 시각에 화면에 보이는 프레임). 없으면 undefined. */
export function frameAt(frames: readonly Frame[], timeUs: number): Frame | undefined {
  let lo = 0;
  let hi = frames.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const frame = frames[mid]!;
    if (frame.pts_us <= timeUs) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found >= 0 ? frames[found] : undefined;
}

/**
 * 구간 [startUs, endUs)에 속한 첫 프레임. 시각 startUs에 보이는 프레임이 시작 시각보다 앞서면 다음 프레임을 쓴다.
 * 사건 시작으로 이동할 때 이동한 프레임이 그 사건에 속하도록 하기 위한 것이다. 구간 안에 프레임이 없으면 undefined.
 */
export function firstFrameWithin(frames: readonly Frame[], startUs: number, endUs: number): Frame | undefined {
  const visible = frameAt(frames, startUs);
  const candidate = visible && visible.pts_us >= startUs ? visible : visible ? stepFrame(frames, visible, 1) : frames[0];
  return candidate && candidate.pts_us >= startUs && candidate.pts_us < endUs ? candidate : visible;
}

/** 이전(-1)·다음(+1) 프레임. 처음이나 끝을 넘으면 undefined이며 순환하지 않는다. */
export function stepFrame(frames: readonly Frame[], current: Frame, delta: 1 | -1): Frame | undefined {
  const position = frames.findIndex((f) => f.index === current.index);
  if (position < 0) return undefined;
  return frames[position + delta];
}

/**
 * 이 프레임을 화면에 표시시키는 탐색 시각(초).
 * 표시 시각 그대로 탐색하면 부동소수점 오차로 이전 프레임이 보일 수 있으므로,
 * 이 프레임과 다음 프레임 사이의 중간 지점을 쓴다. 마지막 프레임이면 1ms 뒤.
 */
export function seekTimeFor(frames: readonly Frame[], frame: Frame): number {
  const position = frames.findIndex((f) => f.index === frame.index);
  const next = position >= 0 ? frames[position + 1] : undefined;
  const endUs = next ? next.pts_us : frame.pts_us + 2_000;
  return (frame.pts_us + (endUs - frame.pts_us) / 2) / 1_000_000;
}

/** 표시된 프레임 시각이 목표 프레임의 표시 시각과 같은지(반올림 오차 허용). */
export function presentedMatches(presentedUs: number | null, target: Frame, toleranceUs = 500): boolean {
  return presentedUs !== null && Math.abs(presentedUs - target.pts_us) <= toleranceUs;
}

/** 이 길이를 넘는 구간은 프레임 표와 관절을 한꺼번에 받지 않는다. */
export const MAX_FRAME_TABLE_SPAN_US = 60_000_000;

export function withinFrameTableLimit(fromUs: number, toUs: number): boolean {
  return toUs - fromUs <= MAX_FRAME_TABLE_SPAN_US;
}
