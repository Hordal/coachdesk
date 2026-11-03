import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { TimelineTrack } from "../src/components/TimelineTrack";
import { activeAt } from "../src/features/scene/ScenePage";
import { contact, evidenceList, guardState, kick, participants, playableEvidenceList, run } from "./fixtures";

type Handler = (url: URL) => unknown;

function mockFetch(handler: Handler) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const body = handler(new URL(String(input), "http://localhost"));
      if (body === undefined) {
        return new Response(JSON.stringify({ code: "not_found", title: "찾을 수 없음", trace_id: "t" }), {
          status: 404,
          headers: { "content-type": "application/problem+json" },
        });
      }
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
}

const timelineBody = {
  from_us: 9_000_000,
  to_us: 14_000_000,
  states: [guardState],
  events: [kick, contact],
  relations: [],
  assessments: [],
};

function server(evidence: unknown) {
  return (url: URL): unknown => {
    const p = url.pathname;
    if (p === "/api/v1/runs/fixture-counter") return run;
    if (p === "/api/v1/runs/fixture-counter/evidence") return evidence;
    if (p === "/api/v1/runs/fixture-counter/timeline") return timelineBody;
    if (p === "/api/v1/videos/demo-video-01") {
      return { video_id: "demo-video-01", width: 640, height: 360, duration_us: 540_000_000, frame_count: 16200, nominal_fps: 30, average_fps: 30, is_vfr: false };
    }
    if (p === "/api/v1/videos/demo-video-01/frames") return { video_id: "demo-video-01", from_us: 9_000_000, to_us: 14_000_000, frames: [{ index: 270, pts_us: 9_000_000 }] };
    if (p === "/api/v1/runs/fixture-counter/poses") {
      return { available: false, reason_code: "no_pose_data", landmark_set_id: null, coordinate_space_id: null, width: null, height: null, connections: [], samples: [] };
    }
    return undefined;
  };
}

function renderScene(path = "/runs/fixture-counter/scene") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("장면 복기 화면", () => {
  it("원본 영상이 있으면 영상과 재생 도구를 보여준다", async () => {
    mockFetch(server(playableEvidenceList));
    const { container } = renderScene();

    await screen.findByRole("button", { name: "구간 처음으로" });
    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    expect(video?.getAttribute("src")).toBe("/api/v1/media/demo-video-01?exp=9999999999&sig=test");
    expect(screen.getByRole("button", { name: "정지 프레임 보기" })).toBeInTheDocument();
    expect(screen.getByText(/고정 프레임률/)).toBeInTheDocument();
  });

  it("표시 프레임 시각을 확인할 수 없는 브라우저에서는 정확하지 않음을 알린다", async () => {
    mockFetch(server(playableEvidenceList));
    renderScene();
    // jsdom에는 requestVideoFrameCallback이 없다.
    expect(await screen.findByText(/표시된 프레임의 시각을 확인할 수 없어 재생 위치로 대신합니다/)).toBeInTheDocument();
    expect(screen.getByText("재생 위치로 대신함")).toBeInTheDocument();
  });

  it("관절 데이터가 없으면 없다고 알린다(0으로 채우지 않는다)", async () => {
    mockFetch(server(playableEvidenceList));
    renderScene();
    expect(await screen.findByText("관절 데이터가 없습니다")).toBeInTheDocument();
  });

  it("원본 영상이 없으면 영상 없이 안내하고 타임라인은 그대로 보여준다", async () => {
    mockFetch(server(evidenceList));
    const { container } = renderScene();

    expect(await screen.findByText(/원본 영상이 없어 재생할 수 없습니다/)).toBeInTheDocument();
    expect(container.querySelector("video")).toBeNull();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "정지 프레임 보기" })).not.toBeInTheDocument();
  });

  it("구간 선택 버튼이 근거 구간을 보여준다", async () => {
    mockFetch(server(playableEvidenceList));
    renderScene();
    const buttons = await screen.findAllByRole("button", { name: /^EV0/ });
    expect(buttons.map((b) => b.textContent)).toEqual(["EV01 · 00:09.00–00:14.00", "EV02 · 00:39.00–00:44.00"]);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
  });
});

describe("activeAt", () => {
  it("[시작, 끝) 구간 안의 사건과 상태만 돌려준다", () => {
    const at = (t: number | null) => activeAt(t, [kick, contact], [guardState]);
    expect(at(10_000_000).events.map((e) => e.event_id)).toEqual(["E01"]);
    expect(at(10_699_999).events.map((e) => e.event_id)).toEqual(["E01"]);
    expect(at(10_700_000).events).toEqual([]); // 끝은 제외
    expect(at(11_040_000).events.map((e) => e.event_id)).toEqual(["E03"]);
    expect(at(9_500_000).states.map((s) => s.state_id)).toEqual(["S01"]);
    expect(at(10_000_000).states).toEqual([]); // 상태 끝(10.0초)은 제외
    expect(at(null)).toEqual({ events: [], states: [] });
  });
});

describe("TimelineTrack 재생 위치", () => {
  const props = { runId: "r1", fromUs: 9_000_000, toUs: 14_000_000, participants, states: [guardState], events: [kick, contact] };

  it("현재 표시 프레임 시각에 재생 위치선을 그린다", () => {
    render(
      <MemoryRouter>
        <TimelineTrack {...props} playheadUs={10_000_000} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("playhead")).toHaveStyle({ left: "20%" });
  });

  it("구간 밖이거나 시각을 모르면 그리지 않는다", () => {
    const { rerender } = render(
      <MemoryRouter>
        <TimelineTrack {...props} playheadUs={null} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("playhead")).not.toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <TimelineTrack {...props} playheadUs={20_000_000} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("playhead")).not.toBeInTheDocument();
  });

  it("항목을 누르면 그 시각으로 이동을 요청한다", async () => {
    const onSelect = vi.fn();
    render(
      <MemoryRouter>
        <TimelineTrack {...props} onSelect={onSelect} />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: /발차기 시도.*이 시각으로 이동/ }));
    expect(onSelect).toHaveBeenCalledWith(10_000_000);
    await userEvent.click(screen.getByRole("button", { name: /하이가드.*이 시각으로 이동/ }));
    expect(onSelect).toHaveBeenLastCalledWith(9_000_000);
  });
});
