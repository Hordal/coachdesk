import { describe, expect, it } from "vitest";

import { firstFrameWithin, frameAt, seekTimeFor, stepFrame, withinFrameTableLimit, type Frame } from "../src/player/frames";

// 고정 프레임률(30fps): 간격이 33333·33334µs로 반올림되어 일정하지 않다.
const cfr: Frame[] = [0, 33_333, 66_667, 100_000, 133_333].map((pts_us, index) => ({ index, pts_us }));
// 가변 프레임률: 간격이 66667·133333µs로 다르다.
const vfr: Frame[] = [0, 133_333, 200_000, 266_667, 400_000].map((pts_us, index) => ({ index, pts_us }));

describe("frameAt", () => {
  it("그 시각에 화면에 보이는 프레임을 돌려준다(표시 시각이 같거나 이전인 마지막 프레임)", () => {
    expect(frameAt(cfr, 0)?.index).toBe(0);
    expect(frameAt(cfr, 33_332)?.index).toBe(0);
    expect(frameAt(cfr, 33_333)?.index).toBe(1);
    expect(frameAt(cfr, 99_999)?.index).toBe(2);
    expect(frameAt(cfr, 10_000_000)?.index).toBe(4);
  });

  it("첫 프레임보다 앞이면 없다", () => {
    expect(frameAt(cfr, -1)).toBeUndefined();
    expect(frameAt([], 0)).toBeUndefined();
  });

  it("가변 프레임률에서는 간격이 길어도 이전 프레임이 유지된다", () => {
    expect(frameAt(vfr, 250_000)?.index).toBe(2);
    expect(frameAt(vfr, 399_999)?.index).toBe(3);
    expect(frameAt(vfr, 400_000)?.index).toBe(4);
  });
});

describe("stepFrame", () => {
  it("이전·다음 프레임으로 이동하고 처음·끝에서는 순환하지 않는다", () => {
    expect(stepFrame(cfr, cfr[2]!, 1)?.index).toBe(3);
    expect(stepFrame(cfr, cfr[2]!, -1)?.index).toBe(1);
    expect(stepFrame(cfr, cfr[0]!, -1)).toBeUndefined();
    expect(stepFrame(cfr, cfr[4]!, 1)).toBeUndefined();
  });

  it("표에 없는 프레임에서는 이동하지 않는다", () => {
    expect(stepFrame(cfr, { index: 99, pts_us: 1 }, 1)).toBeUndefined();
  });
});

describe("seekTimeFor", () => {
  it("해당 프레임을 표시시키는 시각(프레임과 다음 프레임의 중간)을 돌려준다", () => {
    const t = seekTimeFor(cfr, cfr[1]!);
    expect(t * 1_000_000).toBeGreaterThanOrEqual(33_333);
    expect(t * 1_000_000).toBeLessThan(66_667);
    expect(frameAt(cfr, Math.round(t * 1_000_000))?.index).toBe(1);
  });

  it("가변 프레임률에서도 같은 프레임으로 돌아온다", () => {
    for (const f of vfr) {
      expect(frameAt(vfr, Math.round(seekTimeFor(vfr, f) * 1_000_000))?.index).toBe(f.index);
    }
  });

  it("마지막 프레임도 자기 프레임에 머문다", () => {
    const last = vfr[4]!;
    expect(frameAt(vfr, Math.round(seekTimeFor(vfr, last) * 1_000_000))?.index).toBe(4);
  });
});

describe("firstFrameWithin", () => {
  it("시작 시각에 보이는 프레임이 시작보다 앞서면 다음 프레임(구간 안의 첫 프레임)을 돌려준다", () => {
    // 33_333 이후에 시작하는 사건: 그 시각에 보이는 프레임은 #1(33_333)이므로 시작(40_000) 이후의 첫 프레임은 #2
    expect(firstFrameWithin(cfr, 40_000, 133_333)?.index).toBe(2);
    expect(firstFrameWithin(cfr, 33_333, 133_333)?.index).toBe(1); // 정확히 프레임 시각이면 그 프레임
    expect(firstFrameWithin(cfr, 0, 133_333)?.index).toBe(0);
  });

  it("다음 프레임이 구간 밖이면 시작 시각에 보이는 프레임을 돌려준다", () => {
    expect(firstFrameWithin(cfr, 40_000, 60_000)?.index).toBe(1);
  });

  it("가변 프레임률에서도 사건 시작 이후의 첫 프레임을 돌려준다", () => {
    expect(firstFrameWithin(vfr, 150_000, 300_000)?.index).toBe(2); // 133_333 다음은 200_000
    expect(firstFrameWithin(vfr, 200_000, 300_000)?.index).toBe(2);
  });
});

describe("withinFrameTableLimit", () => {
  it("60초를 넘는 구간은 프레임 표를 받지 않는다", () => {
    expect(withinFrameTableLimit(0, 60_000_000)).toBe(true);
    expect(withinFrameTableLimit(0, 60_000_001)).toBe(false);
  });
});
