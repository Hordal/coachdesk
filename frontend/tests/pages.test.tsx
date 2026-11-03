import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { evidenceList, kick, run, takedown, validFinding, withheldCorrection, withheldDistance } from "./fixtures";

type Handler = (url: URL) => { status: number; body: unknown; problem?: boolean };

function mockFetch(handler: Handler) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const { status, body, problem } = handler(new URL(String(input), "http://localhost"));
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": problem ? "application/problem+json" : "application/json" },
      });
    }),
  );
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<App />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const ok = (body: unknown) => ({ status: 200, body });

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe("요약 화면", () => {
  const routes: Handler = (url) => {
    const p = url.pathname;
    if (p === "/api/v1/runs/fixture-counter") return ok(run);
    if (p === "/api/v1/runs/fixture-counter/findings") return ok([validFinding, withheldCorrection, withheldDistance]);
    if (p === "/api/v1/runs/fixture-counter/evidence") return ok(evidenceList);
    return { status: 404, body: { code: "not_found", title: "찾을 수 없음", trace_id: "t" }, problem: true };
  };

  it("가상 예제임을 밝히고 관찰·원인·교정 영역을 분리한다", async () => {
    mockFetch(routes);
    renderAt("/runs/fixture-counter");

    expect(await screen.findByText(validFinding.text!)).toBeInTheDocument();
    expect(screen.getByText(/가상 예제입니다\. 실제 영상을 분석한 결과가 아닙니다/)).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "관찰·집계" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "원인 가설" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "교정 방향" })).toBeInTheDocument();
    expect(screen.getByText("원인 가설을 제공하지 않았습니다.")).toBeInTheDocument();
  });

  it("교정 방향이 없으면 ‘문제 없음’이 아니라 ‘제공하지 않음’으로 안내한다", async () => {
    mockFetch(routes);
    renderAt("/runs/fixture-counter");
    await screen.findByText(validFinding.text!);

    expect(screen.getByText(/교정 방향을 제공하지 않았습니다\. 이는 문제가 없다는 뜻이 아니라/)).toBeInTheDocument();
    expect(screen.getByText(/행동 근거가 부족하고 검토된 교정 규칙이 없어|원인을 설명할 행동 근거가 부족하고/)).toBeInTheDocument();
  });

  it("보류된 거리 주장도 사유와 함께 남겨 둔다", async () => {
    mockFetch(routes);
    renderAt("/runs/fixture-counter");
    expect(await screen.findByText(/실제 거리 측정값이 없어/)).toBeInTheDocument();
  });

  it("원본 영상이 없으면 재생 불가를 안내하되 구간은 보여준다", async () => {
    mockFetch(routes);
    renderAt("/runs/fixture-counter");
    await screen.findByText(validFinding.text!);
    expect(screen.getAllByText(/원본 영상이 없어 재생할 수 없습니다/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "00:09.00–00:14.00" }).length).toBeGreaterThan(0);
  });
});

describe("오류 표시", () => {
  it("없는 실행은 안전한 제목과 확인 번호만 보여준다", async () => {
    mockFetch(() => ({
      status: 404,
      body: { code: "run_not_found", title: "분석 실행을 찾을 수 없음", trace_id: "abc123", detail: "run_id=nope" },
      problem: true,
    }));
    renderAt("/runs/nope");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("분석 실행을 찾을 수 없음");
    expect(alert).toHaveTextContent("abc123");
    expect(alert).not.toHaveTextContent("run_id=nope");
  });

  it("서버에 연결할 수 없으면 다시 시도를 제공한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fail"))));
    renderAt("/");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("서버에 연결할 수 없습니다");
    await waitFor(() => expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument());
  });
});

describe("그라운드 사건 상세", () => {
  const detail = (event: object) => (url: URL) =>
    url.pathname === "/api/v1/runs/fixture-counter"
      ? ok(run)
      : url.pathname === "/api/v1/runs/fixture-counter/events/G01"
        ? ok({ event, assessments: [], relations: [], exchanges: [], evidence: [] })
        : url.pathname === "/api/v1/runs/fixture-counter/evidence"
          ? ok([])
          : { status: 404, body: { code: "not_found", title: "찾을 수 없음", trace_id: "t" }, problem: true };

  it("테이크다운의 결과를 한국어로 보여준다", async () => {
    mockFetch(detail(takedown));
    renderAt("/runs/fixture-counter/events/G01");
    expect(await screen.findByRole("heading", { name: /테이크다운/ })).toBeInTheDocument();
    expect(screen.getByText("방어됨")).toBeInTheDocument();
  });

  it("사건 자체를 확정하지 못했으면 성공·실패로 세지 않는다고 알린다", async () => {
    mockFetch(detail({ ...takedown, status: "UNKNOWN", outcome: null }));
    renderAt("/runs/fixture-counter/events/G01");
    expect(await screen.findByText(/구간을 신뢰성 있게 확정하지 못했습니다/)).toBeInTheDocument();
    expect(screen.queryByText("결과")).toBeNull();
  });
});

describe("팀 동작 검출 모델의 후보 사건 상세", () => {
  const candidate = {
    ...kick,
    event_id: "ev_8299958_PUNCH_CANDIDATE_candidate-0",
    event_label: "PUNCH_CANDIDATE",
    status: "CANDIDATE",
    verified: false,
    outcome: "UNKNOWN",
    participant_id: "candidate:0",
    target_participant_id: null,
    role: "UNRESOLVED",
    track_id: 0,
    arm: "left",
    synthetic: false,
    evidence: [{ measure: "wrist_shoulder_ratio_gain", value: 0.7607 }],
    limitations: ["HEURISTIC_NOT_A_CLASSIFIER", "NO_VERIFIED_CONTACT"],
    reason_codes: ["NORMALIZED_EXTENSION_THEN_RETRACTION"],
  };
  const detail = (url: URL) =>
    url.pathname === "/api/v1/runs/fixture-counter"
      ? ok(run)
      : url.pathname === `/api/v1/runs/fixture-counter/events/${candidate.event_id}`
        ? ok({ event: candidate, assessments: [], relations: [], exchanges: [], evidence: [] })
        : url.pathname === "/api/v1/runs/fixture-counter/evidence"
          ? ok([])
          : { status: 404, body: { code: "not_found", title: "찾을 수 없음", trace_id: "t" }, problem: true };

  it("후보임을 알리고 근거 측정과 한계를 보여준다", async () => {
    mockFetch(detail);
    renderAt(`/runs/fixture-counter/events/${candidate.event_id}`);
    expect(await screen.findByRole("heading", { name: /펀치 후보/ })).toBeInTheDocument();
    expect(screen.getByText(/검출 모델이 낸 후보입니다/)).toBeInTheDocument();
    expect(screen.getByText(/검증 전/)).toBeInTheDocument();
    expect(screen.getByText("wrist_shoulder_ratio_gain: 0.7607")).toBeInTheDocument();
    expect(screen.getByText(/HEURISTIC_NOT_A_CLASSIFIER, NO_VERIFIED_CONTACT/)).toBeInTheDocument();
    expect(screen.getByText("판단 불가")).toBeInTheDocument(); // outcome=UNKNOWN
  });
});
describe("코치 라이브러리", () => {
  const page = { items: [], total: 0, page: 1, page_size: 12, counts: { total: 0, processing: 0, pending: 0, reviewed: 0, failed: 0 } };
  it("등록 기록이 없으면 가상 예제를 섞지 않고 등록 동선을 제공한다", async () => {
    mockFetch(() => ok(page));
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "첫 번째 훈련 기록을 시작하세요" })).toBeInTheDocument();
    expect(screen.queryByText("가상 예제")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "영상 등록하기" })).toHaveAttribute("href", "/upload");
  });
  it("등록한 기록 제목과 검수 작업을 실제 영상에 연결한다", async () => {
    const item = { video_id: "v1", filename: "sparring_01.mp4", title: "오전 스파링", athlete_name: "회원 1", status: "ready", sport: "boxing", session_type: "sparring", session_date: "2026-10-22", duration_us: 60_000_000, event_count: 2, review_state: "pending", run_id: "r1" };
    mockFetch(() => ok({ ...page, items: [item], total: 1, counts: { ...page.counts, total: 1, pending: 1 } }));
    renderAt("/");
    expect(await screen.findByRole("link", { name: "오전 스파링" })).toHaveAttribute("href", "/videos/v1");
    expect(screen.getByRole("link", { name: "장면 검토 →" })).toHaveAttribute("href", "/runs/r1/review");
  });
});
