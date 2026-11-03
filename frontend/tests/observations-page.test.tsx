import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ObservationsPage, parseJsonLines } from "../src/features/observations/ObservationsPage";

const set = {
  set_id: "s1", video_id: "v1", created_at: "t", source: "x", frame_count: 407, detection_count: 1864, track_count: 3,
  analysis_width: 1280, analysis_height: 720, assignment_state: "NOT_RUN", warnings: ["가변 프레임레이트 영상입니다."],
  tracks: [
    { track_id: 7, first_us: 0, last_us: 500_000, frame_count: 12, missing_frames: 0, longest_gap_us: 0, median_area_ratio: 0.0045, mean_score: 0.5 },
    { track_id: 1, first_us: 0, last_us: 13_000_000, frame_count: 407, missing_frames: 0, longest_gap_us: 0, median_area_ratio: 0.16, mean_score: 0.9 },
    { track_id: 0, first_us: 0, last_us: 13_000_000, frame_count: 380, missing_frames: 27, longest_gap_us: 600_000, median_area_ratio: 0.15, mean_score: 0.85 },
  ],
};

function renderPage(handler: (path: string, method: string, body: unknown) => { status: number; body: unknown }) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path, method: init?.method ?? "GET", body });
      const r = handler(path, init?.method ?? "GET", body);
      return new Response(JSON.stringify(r.body), { status: r.status, headers: { "content-type": r.status >= 400 ? "application/problem+json" : "application/json" } });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/videos/v1/observations"]}>
        <Routes>
          <Route path="videos/:videoId/observations" element={<ObservationsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("JSONL", () => {
  it("빈 줄을 건너뛰고 줄마다 JSON으로 읽는다", () => {
    expect(parseJsonLines('{"a":1}\n\n{"a":2}\r\n')).toEqual([{ a: 1 }, { a: 2 }]);
    expect(() => parseJsonLines("not json")).toThrow();
  });
});

describe("검출·추적 결과 가져오기 화면", () => {
  it("후보 트랙을 프레임 수 순서로 보여 주고 선수로 단정하지 않는다", async () => {
    renderPage((path) => (path === "/api/v1/videos/v1/observations" ? { status: 200, body: set } : { status: 404, body: {} }));
    await userEvent.click(await screen.findByText("분석 담당자용: 검출 상태 자세히 보기 · 선택할 필요 없음"));
    const rows = await screen.findAllByRole("row");
    const ids = rows.slice(1).map((r) => r.querySelector("th")?.textContent);
    expect(ids).toEqual(["1", "0", "7"]);
    expect(screen.getByText(/트랙은 추적기가 붙인 임시 번호이며 선수가 아닙니다/)).toBeInTheDocument();
    expect(screen.getByText(/선수 지정 상태: 선택 전/)).toBeInTheDocument();
    expect(screen.getByText("16.0%")).toBeInTheDocument();
    expect(screen.getByText("0.45%")).toBeInTheDocument(); // 작은 값은 소수점 두 자리
    expect(screen.getByText("가변 프레임레이트 영상입니다.")).toBeInTheDocument();
  });

  it("아직 가져온 결과가 없으면 그렇게 알려 준다", async () => {
    renderPage(() => ({ status: 404, body: { code: "observations_not_found", title: "없음" } }));
    expect(await screen.findByText("가져온 검출·추적 결과가 없습니다.")).toBeInTheDocument();
  });

  it("세 파일을 읽어 한 번에 보내고, 검증 오류 목록을 그대로 보여 준다", async () => {
    let imported = false;
    const calls = renderPage((_path, method) => {
      if (method === "POST") {
        imported = true;
        return { status: 422, body: { code: "invalid_observations", title: "가져온 내용이 영상과 맞지 않거나 형식에 맞지 않음", trace_id: "tr-1", errors: ["프레임 30의 시각이 영상과 맞지 않음"] } };
      }
      return imported ? { status: 200, body: set } : { status: 404, body: { code: "observations_not_found", title: "없음" } };
    });
    await screen.findByText("가져온 검출·추적 결과가 없습니다.");
    await userEvent.upload(screen.getByLabelText(/metadata\.json/), new File(['{"frame_timestamps_us":[0,33333]}'], "metadata.json"));
    await userEvent.upload(screen.getByLabelText(/tracking_frames\.jsonl/), new File(['{"timestamp_us":0,"tracks":[]}\n{"timestamp_us":33333,"tracks":[]}\n'], "tracking_frames.jsonl"));
    await userEvent.upload(screen.getByLabelText(/detection_frames\.jsonl/), new File(['{"timestamp_us":0,"detections":[]}\n'], "detection_frames.jsonl"));
    await userEvent.click(screen.getByRole("button", { name: "가져오기" }));

    expect(await screen.findByText("프레임 30의 시각이 영상과 맞지 않음")).toBeInTheDocument();
    expect(screen.getByText(/tr-1/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.path).toBe("/api/v1/videos/v1/observations");
    expect(post?.body).toEqual({
      metadata: { frame_timestamps_us: [0, 33333] },
      tracks: [{ timestamp_us: 0, tracks: [] }, { timestamp_us: 33333, tracks: [] }],
      detections: [{ timestamp_us: 0, detections: [] }],
    });
  });

  it("JSON이 아닌 파일은 서버로 보내지 않는다", async () => {
    const calls = renderPage(() => ({ status: 404, body: { code: "observations_not_found", title: "없음" } }));
    await screen.findByText("가져온 검출·추적 결과가 없습니다.");
    await userEvent.upload(screen.getByLabelText(/metadata\.json/), new File(["oops"], "metadata.json"));
    await userEvent.upload(screen.getByLabelText(/tracking_frames\.jsonl/), new File(["{}"], "t.jsonl"));
    await userEvent.click(screen.getByRole("button", { name: "가져오기" }));
    expect(await screen.findByText("JSON 또는 JSONL 파일로 읽을 수 없습니다")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(false));
  });
});
