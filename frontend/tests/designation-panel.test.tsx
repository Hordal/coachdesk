import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ObservationsPage } from "../src/features/observations/ObservationsPage";

const tracks = [
  { track_id: 0, first_us: 0, last_us: 1_000_000, frame_count: 380, missing_frames: 27, longest_gap_us: 600_000, median_area_ratio: 0.15, mean_score: 0.85 },
  { track_id: 1, first_us: 0, last_us: 1_000_000, frame_count: 406, missing_frames: 0, longest_gap_us: 0, median_area_ratio: 0.17, mean_score: 0.93 },
  { track_id: 3, first_us: 0, last_us: 1_000_000, frame_count: 402, missing_frames: 0, longest_gap_us: 0, median_area_ratio: 0.004, mean_score: 0.8 },
];
const set = (state = "NOT_RUN") => ({
  set_id: "s1", video_id: "v1", created_at: "t", source: "x", frame_count: 407, detection_count: 1, track_count: 3,
  analysis_width: 1280, analysis_height: 720, assignment_state: state, warnings: [], tracks,
});
const frames = (separated = true) => ({
  set_id: "s1", display_width: 1920, display_height: 1080, candidate_state: "OK",
  frames: [{ frame_index: 60, time_us: 2_502_500, separated, boxes: [{ track_id: 0, bbox: [875, 229, 1229, 1037] }, { track_id: 1, bbox: [477, 285, 900, 1078] }] }],
});

type Reply = { status: number; body: unknown };
function setup(handler: (path: string, method: string, body: unknown) => Reply | undefined) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path, method, body });
      const r = handler(path, method, body) ?? { status: 404, body: { code: "not_found", title: "없음" } };
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

const empty = { state: "NOT_RUN", set_id: null, assignments: [] };
const base = (path: string): Reply | undefined => {
  if (path === "/api/v1/videos/v1/observations") return { status: 200, body: set() };
  if (path === "/api/v1/videos/v1/designation-frames") return { status: 200, body: frames() };
  if (path === "/api/v1/videos/v1/designation") return { status: 200, body: empty };
  return undefined;
};

afterEach(() => vi.unstubAllGlobals());

describe("선수 지정", () => {
  it("사진에서 두 사람을 누르면 역할을 나누고 저장한다. 같은 사람의 중복 지정은 막는다", async () => {
    const calls = setup((path, method) => method === "PUT" ? { status: 200, body: empty } : base(path));
    await userEvent.click(await screen.findByRole("button", { name: "사진 속 번호 1 선수를 분석 선수로 선택" }));
    expect(screen.getByRole("button", { name: "선택 저장" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "사진 속 번호 1 선수를 상대로 선택" }));
    expect(screen.getByRole("alert")).toHaveTextContent("같은 사람을 두 번 고를 수 없습니다");
    await userEvent.click(screen.getByRole("button", { name: "사진 속 번호 0 선수를 상대로 선택" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "선택 저장" }));
    await waitFor(() => expect(calls.find(c => c.method === "PUT")?.body).toEqual({set_id:"s1",user_track_ids:[1],opponent_track_ids:[0],mode:"sparring"}));
  });

  it("선택하기 전에는 아무도 지정되어 있지 않고 확정할 수 없다", async () => {
    setup(base);
    expect(await screen.findByText("사진에서 두 선수를 골라 주세요")).toBeInTheDocument();
    expect(screen.getByLabelText("트랙 0")).toHaveValue("none");
    expect(screen.getByLabelText("트랙 1")).toHaveValue("none"); // 가장 크거나 먼저 나온 트랙을 미리 고르지 않는다
    expect(screen.getByRole("button", { name: "선택 저장" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "사진 속 번호 0 선수를 분석 선수로 선택" })).toBeEnabled();
  });

  it("두 선수를 직접 골라 확정하면 선택한 트랙이 서버로 간다", async () => {
    const calls = setup((path, method) => (method === "PUT" ? { status: 200, body: { state: "ASSIGNED", set_id: "s1", assignments: [] } } : base(path)));
    await screen.findByLabelText("트랙 0");
    await userEvent.click(screen.getByText("추가 설정: 같은 선수가 여러 번호로 나뉜 경우"));
    await userEvent.selectOptions(screen.getByLabelText("트랙 1"), "user");
    expect(screen.getByRole("button", { name: "선택 저장" })).toBeDisabled(); // 상대가 아직 없다
    await userEvent.selectOptions(screen.getByLabelText("트랙 0"), "opponent");
    await userEvent.click(screen.getByRole("button", { name: "선택 저장" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ set_id: "s1", user_track_ids: [1], opponent_track_ids: [0], mode: "sparring" }));
  });

  it("나와 상대를 바꿀 수 있다", async () => {
    const calls = setup((path, method) => (method === "PUT" ? { status: 200, body: empty } : base(path)));
    await screen.findByLabelText("트랙 0");
    await userEvent.click(screen.getByText("추가 설정: 같은 선수가 여러 번호로 나뉜 경우"));
    await userEvent.selectOptions(screen.getByLabelText("트랙 1"), "user");
    await userEvent.selectOptions(screen.getByLabelText("트랙 0"), "opponent");
    await userEvent.click(screen.getByRole("button", { name: "분석 선수와 상대 바꾸기" }));
    expect(screen.getByLabelText("트랙 1")).toHaveValue("opponent");
    expect(screen.getByLabelText("트랙 0")).toHaveValue("user");
    await userEvent.click(screen.getByRole("button", { name: "선택 저장" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ set_id: "s1", user_track_ids: [0], opponent_track_ids: [1], mode: "sparring" }));
  });

  it("서버가 모순된 선택을 거절하면 이유를 보여 준다", async () => {
    setup((path, method) =>
      method === "PUT" ? { status: 422, body: { code: "inconsistent_selection", title: "x", errors: ["나로 고른 트랙 [0, 3]이 프레임 5에서 동시에 보임"] } } : base(path),
    );
    await screen.findByLabelText("트랙 0");
    await userEvent.click(screen.getByText("추가 설정: 같은 선수가 여러 번호로 나뉜 경우"));
    await userEvent.selectOptions(screen.getByLabelText("트랙 0"), "user");
    await userEvent.selectOptions(screen.getByLabelText("트랙 3"), "user");
    await userEvent.selectOptions(screen.getByLabelText("트랙 1"), "opponent");
    await userEvent.click(screen.getByRole("button", { name: "선택 저장" }));
    expect(await screen.findByText(/한 선수의 트랙은 같은 시각에 동시에 보이면 안 됩니다/)).toBeInTheDocument();
  });

  it("이미 지정된 결과를 보여 주고 바꾸기 전에는 다시 확정할 수 없다", async () => {
    setup((path) =>
      path === "/api/v1/videos/v1/designation"
        ? { status: 200, body: { state: "ASSIGNED", set_id: "s1", assignments: [{ participant_id: "A", role: "USER", track_ids: [1] }, { participant_id: "B", role: "OPPONENT", track_ids: [0, 3] }] } }
        : path === "/api/v1/videos/v1/observations"
          ? { status: 200, body: set("ASSIGNED") }
          : base(path),
    );
    expect(await screen.findByText(/저장된 선택: 분석 선수 = 번호 1 · 상대 = 번호 0, 3/)).toBeInTheDocument();
    expect(screen.getByLabelText("트랙 3")).toHaveValue("opponent");
    expect(screen.getByRole("button", { name: "선택 저장" })).toBeDisabled();
    await userEvent.click(screen.getByText("추가 설정: 같은 선수가 여러 번호로 나뉜 경우"));
    await userEvent.selectOptions(screen.getByLabelText("트랙 3"), "none");
    expect(screen.getByRole("button", { name: "선택 저장" })).toBeEnabled();
  });

  it("후보가 모자라거나 겹친 장면뿐이면 알려 준다", async () => {
    setup((path) => (path === "/api/v1/videos/v1/designation-frames" ? { status: 200, body: { ...frames(false), candidate_state: "INSUFFICIENT" } } : base(path)));
    expect(await screen.findByText(/후보 트랙이 두 개 미만입니다/)).toBeInTheDocument();
    expect(screen.getByText(/서로 떨어져 보이는 프레임이 없어/)).toBeInTheDocument();
    expect(screen.getByLabelText("트랙 0")).toBeDisabled();
  });
  it("개인 훈련에서는 후보 한 명만 선택해서 저장하고 상대를 만들지 않는다", async () => {
    const calls = setup((path, method) => method === "PUT" ? {status:200,body:empty} : path.endsWith("/designation-frames") ? {status:200,body:{...frames(),candidate_state:"INSUFFICIENT"}} : base(path));
    await userEvent.selectOptions(await screen.findByLabelText("영상 유형"), "solo");
    await userEvent.click(screen.getByRole("button", {name:"사진 속 번호 1 선수를 분석 선수로 선택"}));
    expect(screen.getByRole("button", {name:"선택 저장"})).toBeEnabled();
    expect(screen.queryByRole("button", {name:/2. 상대 선수/})).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name:"선택 저장"}));
    await waitFor(() => expect(calls.find(c => c.method === "PUT")?.body).toEqual({set_id:"s1",user_track_ids:[1],opponent_track_ids:[],mode:"solo"}));
  });

});
