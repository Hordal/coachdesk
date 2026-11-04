import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";

const stage = (name: string, label: string, status: string, progress: number, extra: object = {}) => ({
  name, label, status, progress, message: null, reason_code: null, ...extra,
});

function summary(overrides: Record<string, unknown> = {}, job: Record<string, unknown> | null = {}) {
  return {
    video_id: "v1", filename: "round1.mp4", sport: "boxing", size_bytes: 5_242_880, duration_us: 90_000_000,
    width: 1920, height: 1080, media_state:"present", status: "processing", run_id: null, created_at: "t",
    job: job && {
      job_id: "j1", kind: "ingest", status: "running", progress: 0.4, stage: "playback_copy", attempts: 1, max_attempts: 3,
      cancel_requested: false, created_at: "t", started_at: "t", finished_at: null, run_id: null, error: null,
      stages: [
        stage("decode_check", "영상 확인", "completed", 1),
        stage("playback_copy", "재생용 사본 만들기", "running", 0.2),
        stage("frame_table", "프레임 시각 정리", "pending", 0),
        stage("analysis", "분석", "unsupported", 0, { reason_code: "stage_not_implemented" }),
      ],
      ...job,
    },
    ...overrides,
  };
}

function renderVideo(handler: (url: URL, init?: RequestInit) => { status: number; body: unknown } | undefined) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const r = handler(new URL(String(input), "http://localhost"), init) ?? { status: 404, body: { code: "not_found", title: "없음" } };
      return new Response(JSON.stringify(r.body), { status: r.status, headers: { "content-type": "application/json" } });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/videos/v1"]}>
        <Routes>
          <Route path="*" element={<App />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("영상 준비 화면", () => {
  it("단계별 진행을 보여주고 완료 예정 시간은 만들지 않는다", async () => {
    renderVideo((u) => (u.pathname === "/api/v1/videos/v1/summary" ? { status: 200, body: summary() } : undefined));
    expect(await screen.findByRole("heading", { name: "round1.mp4" })).toBeInTheDocument();
    expect(screen.getByText("재생용 사본 만들기")).toBeInTheDocument();
    expect(screen.getByLabelText("전체 진행률")).toHaveValue(0.4);
    expect(screen.getByText("아직 구현되지 않은 단계")).toBeInTheDocument();
    expect(screen.queryByText(/남은 시간|예상/)).toBeNull();
  });

  it("처리 취소를 요청한다", async () => {
    const cancelled: string[] = [];
    renderVideo((u, init) => {
      if (u.pathname === "/api/v1/videos/v1/summary") return { status: 200, body: summary() };
      if (u.pathname === "/api/v1/jobs/j1/cancel" && init?.method === "POST") {
        cancelled.push(u.pathname);
        return { status: 200, body: {} };
      }
      return undefined;
    });
    await userEvent.click(await screen.findByRole("button", { name: "처리 취소" }));
    await waitFor(() => expect(cancelled).toHaveLength(1));
  });

  it("실패하면 안전한 제목과 확인 번호만 보여준다", async () => {
    renderVideo((u) =>
      u.pathname === "/api/v1/videos/v1/summary"
        ? {
            status: 200,
            body: summary({ status: "failed" }, {
              status: "failed", progress: 0.1, attempts: 1, finished_at: "t",
              error: { code: "decode_error", title: "영상을 읽는 중 오류가 발생했습니다", stage: "decode_check", retryable: false, at_us: 5_000_000, trace_id: "trace-9" },
            }),
          }
        : undefined,
    );
    expect(await screen.findByText("영상을 읽는 중 오류가 발생했습니다")).toBeInTheDocument();
    expect(screen.getByText(/00:05.00 부근/)).toBeInTheDocument();
    expect(screen.getByText(/trace-9/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "처리 취소" })).toBeNull();
  });

  it("준비되면 분석 연결 순서와 영상 복기를 제공하고 완료된 처리 내역은 접는다", async () => {
    renderVideo((u) =>
      u.pathname === "/api/v1/videos/v1/summary"
        ? { status: 200, body: summary({ status: "ready", run_id: "run-1" }, { status: "partial", progress: 1, finished_at: "t", run_id: "run-1" }) }
        : undefined,
    );
    expect(await screen.findByRole("heading", {name:"분석과 검수"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name:"동작 분석 요청"})).toBeDisabled();
    expect(screen.getByRole("link", { name: "코칭 보고서 열기" })).toHaveAttribute("href", "/runs/run-1/report");
    expect(screen.queryByRole("button", { name: "처리 취소" })).toBeNull();
  });
});
