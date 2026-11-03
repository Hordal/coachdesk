import { describe, expect, it } from "vitest";

import { formatDateTime, formatDuration, formatFraction, formatRange, formatTime } from "../src/lib/format";

describe("formatDateTime", () => {
  it("RFC 3339 시각을 분 단위로 줄여 보여 주고 읽을 수 없는 값은 그대로 둔다", () => {
    expect(formatDateTime("2026-10-06T14:58:51.399+00:00")).toMatch(/^2026-10-0[67] \d{2}:\d{2}$/);
    expect(formatDateTime("t")).toBe("t");
  });
});

describe("formatTime", () => {
  it("마이크로초를 mm:ss.cc로 표시한다", () => {
    expect(formatTime(10_000_000)).toBe("00:10.00");
    expect(formatTime(10_700_000)).toBe("00:10.70");
    expect(formatTime(11_020_000)).toBe("00:11.02");
    expect(formatTime(0)).toBe("00:00.00");
  });

  it("1시간 이상은 시를 붙인다", () => {
    expect(formatTime(3_600_000_000 + 5_000_000)).toBe("1:00:05.00");
  });

  it("구간은 시작–끝으로 표시한다", () => {
    expect(formatRange(9_000_000, 14_000_000)).toBe("00:09.00–00:14.00");
  });
});

describe("formatDuration", () => {
  it("분과 초로 표시한다", () => {
    expect(formatDuration(540_000_000)).toBe("9분 00초");
    expect(formatDuration(45_000_000)).toBe("45초");
  });
});

describe("formatFraction", () => {
  it("분모가 0이면 비율을 만들지 않는다", () => {
    expect(formatFraction(0, 0)).toBeNull();
    expect(formatFraction(3, 4)).toBe("3 / 4");
  });
});
