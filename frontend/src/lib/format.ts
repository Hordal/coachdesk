/** 원본 재생 시작 기준 마이크로초를 mm:ss.cc(1시간 이상은 h:mm:ss.cc)로 표시한다. */
export function formatTime(us: number): string {
  const totalCs = Math.floor(us / 10_000);
  const cs = totalCs % 100;
  const totalS = Math.floor(totalCs / 100);
  const s = totalS % 60;
  const totalM = Math.floor(totalS / 60);
  const m = totalM % 60;
  const h = Math.floor(totalM / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  const base = `${pad(m)}:${pad(s)}.${pad(cs)}`;
  return h > 0 ? `${h}:${base}` : base;
}

/** [시작, 끝) 구간 표시 */
export function formatRange(startUs: number, endUs: number): string {
  return `${formatTime(startUs)}–${formatTime(endUs)}`;
}

export function formatDuration(us: number): string {
  const totalS = Math.round(us / 1_000_000);
  const m = Math.floor(totalS / 60);
  const s = totalS % 60;
  return m > 0 ? `${m}분 ${String(s).padStart(2, "0")}초` : `${s}초`;
}

/** 비율 표시. 분모가 없으면 null을 돌려준다(0으로 바꾸지 않는다). */
export function formatFraction(numerator: number, denominator: number): string | null {
  return denominator > 0 ? `${numerator} / ${denominator}` : null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}

/** 서버의 RFC 3339 시각을 사용자 시간대의 `YYYY-MM-DD HH:mm`으로 보여 준다. 읽을 수 없는 값은 그대로 둔다. */
export function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export const formatPercent = (ratio: number): string => `${Math.floor(Math.min(Math.max(ratio, 0), 1) * 100)}%`;
