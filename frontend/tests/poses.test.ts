import { describe, expect, it } from "vitest";

import type { PoseSample } from "../src/api/client";
import { buildPoseIndex, poseAt } from "../src/player/poses";

const sample = (time_us: number, participant_id: string): PoseSample => ({
  time_us,
  participant_id,
  joints: [{ name: "tl", x: 1, y: 2, score: 1, state: "observed" }],
});

const index = buildPoseIndex([sample(300, "A"), sample(100, "A"), sample(200, "A"), sample(150, "B")]);

describe("poseAt", () => {
  it("선수별로 가장 가까운 관측을 돌려준다", () => {
    expect(poseAt(index, "A", 100, 0)?.time_us).toBe(100);
    expect(poseAt(index, "A", 190, 50)?.time_us).toBe(200);
    expect(poseAt(index, "B", 150, 0)?.time_us).toBe(150);
  });

  it("입력이 정렬되어 있지 않아도 시각 순서로 다룬다", () => {
    expect(poseAt(index, "A", 300, 0)?.time_us).toBe(300);
    expect(poseAt(index, "A", 100, 0)?.time_us).toBe(100);
  });

  it("허용 오차 밖이면 오래된 관측을 쓰지 않고 비워 둔다", () => {
    expect(poseAt(index, "A", 270, 20)).toBeUndefined(); // 가장 가까운 300이 30 떨어져 있다
    expect(poseAt(index, "A", 280, 20)?.time_us).toBe(300); // 허용 오차는 경계를 포함한다
    expect(poseAt(index, "A", 1000, 100)).toBeUndefined();
    expect(poseAt(index, "A", 0, 100)?.time_us).toBe(100);
    expect(poseAt(index, "A", 0, 99)).toBeUndefined();
  });

  it("관측이 없는 선수는 비어 있다", () => {
    expect(poseAt(index, "C", 100, 1_000_000)).toBeUndefined();
    expect(poseAt(buildPoseIndex([]), "A", 0, 1_000_000)).toBeUndefined();
  });
});
