import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { PoseSample, PoseView } from "../src/api/client";
import { PoseOverlay } from "../src/player/PoseOverlay";
import { buildPoseIndex } from "../src/player/poses";
import { participants } from "./fixtures";

const square = (time_us: number, participant_id: string, dx = 0): PoseSample => ({
  time_us,
  participant_id,
  joints: [
    { name: "tl", x: 10 + dx, y: 20, score: 1, state: "observed" },
    { name: "tr", x: 50 + dx, y: 20, score: 1, state: "observed" },
    { name: "br", x: null, y: null, score: null, state: "missing" },
    { name: "bl", x: 10 + dx, y: 60, score: 0.4, state: "observed" },
  ],
});

const pose: PoseView = {
  available: true,
  reason_code: null,
  landmark_set_id: "t",
  coordinate_space_id: "analysis_px:1280x720",
  width: 640,
  height: 360,
  connections: [["tl", "tr"], ["tr", "br"], ["br", "bl"], ["bl", "tl"]],
  samples: [square(1000, "A"), square(1000, "B", 100), square(2000, "A")],
};

const draw = (timeUs: number | null, p: PoseView = pose, tolerance = 500) =>
  render(
    <MemoryRouter>
      <PoseOverlay pose={p} index={buildPoseIndex(p.samples)} participants={participants} timeUs={timeUs} toleranceUs={tolerance} />
    </MemoryRouter>,
  );

describe("PoseOverlay", () => {
  it("영상과 같은 좌표계(viewBox)를 쓴다", () => {
    const { container } = draw(1000);
    expect(container.querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 640 360");
  });

  it("값이 없는 관절은 그리지 않고, 그 관절을 잇는 선도 그리지 않는다", () => {
    const { container } = draw(1000);
    const a = container.querySelector('[data-participant="A"]')!;
    // 4개 중 br이 없으므로 선은 tl-tr, bl-tl 두 개뿐이다.
    expect(a.querySelectorAll("line")).toHaveLength(2);
    expect(a.querySelectorAll("circle")).toHaveLength(3);
    // 좌표를 0으로 채운 원이 없다.
    expect([...a.querySelectorAll("circle")].every((c) => c.getAttribute("cx") !== "0" && c.getAttribute("cy") !== "0")).toBe(true);
  });

  it("선수는 색뿐 아니라 선 모양과 문자로 구별한다", () => {
    const { container } = draw(1000);
    expect(container.querySelector('[data-participant="A"] line')).toHaveClass("pose-line--solid");
    expect(container.querySelector('[data-participant="B"] line')).toHaveClass("pose-line--dashed");
    expect(container.querySelector('[data-participant="A"] text')).toHaveTextContent("A(사용자)");
    expect(container.querySelector('[data-participant="B"] text')).toHaveTextContent("B(상대)");
  });

  it("관측 시각과 맞지 않으면 그리지 않는다", () => {
    const { container } = draw(1500, pose, 100);
    expect(container.querySelectorAll("[data-participant]")).toHaveLength(0);
  });

  it("허용 오차 안이면 가장 가까운 관측을 그린다", () => {
    const { container } = draw(1050, pose, 100);
    expect(container.querySelector('[data-participant="A"]')).not.toBeNull();
  });

  it("그 시각에 관측이 있는 선수만 그린다", () => {
    const { container } = draw(2000);
    expect(container.querySelector('[data-participant="A"]')).not.toBeNull();
    expect(container.querySelector('[data-participant="B"]')).toBeNull();
  });

  it("관절 데이터가 없거나 표시 시각을 모르면 아무것도 그리지 않는다", () => {
    expect(draw(1000, { ...pose, available: false, width: null, height: null, samples: [] }).container.querySelector("svg")).toBeNull();
    expect(draw(null).container.querySelector("svg")).toBeNull();
  });
});
