import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { run } from "./fixtures";

type Reply = { status: number; body: unknown } | undefined;
interface Seen {
  method: string;
  path: string;
  body: unknown;
}

function setup(path: string, handler: (seen: Seen) => Reply) {
  const seen: Seen[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const s: Seen = { method: init?.method ?? "GET", path: new URL(String(input), "http://localhost").pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined };
      seen.push(s);
      const r = handler(s) ?? { status: 404, body: { code: "not_found", title: "없음" } };
      return new Response(JSON.stringify(r.body), { status: r.status, headers: { "content-type": r.status >= 400 ? "application/problem+json" : "application/json" } });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<App />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return seen;
}

const RUN = "/api/v1/runs/fixture-counter";
const history = { original_run_id: "fixture-counter", latest_run_id: "fixture-counter", entries: [] };
const base = (s: Seen): Reply => {
  if (s.path === RUN) return { status: 200, body: run };
  if (s.path === `${RUN}/corrections` && s.method === "GET") return { status: 200, body: history };
  if (s.path === `${RUN}/reports` && s.method === "GET") return { status: 200, body: [] };
  if (s.path === `${RUN}/context-notes` && s.method === "GET") return { status: 200, body: [] };
  return undefined;
};

afterEach(() => vi.unstubAllGlobals());

describe("정정·기록 화면", () => {
  it("선택하면 적용 내용을 미리 보여 주고, 확인 뒤에만 정정한다", async () => {
    const seen = setup("/runs/fixture-counter/corrections", (s) => {
      if (s.path === `${RUN}/corrections/preview`) {
        return { status: 200, body: { kind: "designation", changes: [{ participant_id: "A", role_before: "USER", role_after: "OPPONENT" }, { participant_id: "B", role_before: "OPPONENT", role_after: "USER" }], recalculation: "not_required", note: "역할 표시만 바뀝니다." } };
      }
      if (s.path === `${RUN}/corrections` && s.method === "POST") return { status: 201, body: { ...run, run_id: "new-run", parent_run_id: "fixture-counter" } };
      return base(s);
    });

    await userEvent.click(await screen.findByRole("radio", { name: /선수 B/ }));
    expect(await screen.findByText("역할 표시만 바뀝니다.")).toBeInTheDocument();
    expect(seen.some((s) => s.method === "POST" && s.path === `${RUN}/corrections`)).toBe(false); // 미리 보기만으로는 저장하지 않는다

    await userEvent.click(screen.getByRole("button", { name: "이 내용으로 정정" }));
    await waitFor(() => expect(seen.find((s) => s.method === "POST" && s.path === `${RUN}/corrections`)?.body).toEqual({ kind: "designation", user_participant_id: "B" }));
  });

  it("바뀌는 내용이 없으면 정정 버튼을 쓸 수 없다", async () => {
    setup("/runs/fixture-counter/corrections", (s) =>
      s.path === `${RUN}/corrections/preview` ? { status: 200, body: { kind: "designation", changes: [], recalculation: "not_required", note: "바뀌는 내용이 없습니다." } } : base(s),
    );
    await userEvent.click(await screen.findByRole("radio", { name: /선수 A/ }));
    expect(await screen.findByText("바뀌는 내용이 없습니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이 내용으로 정정" })).toBeDisabled();
  });

  it("이미 정정된 결과는 최신 결과로 안내하고 정정할 수 없다", async () => {
    setup("/runs/fixture-counter/corrections", (s) => (s.path === RUN ? { status: 200, body: { ...run, superseded_by: "newer" } } : base(s)));
    expect(await screen.findByText(/정정되어 최신이 아닙니다/, { selector: ".notice" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "최신 결과 보기" })).toHaveAttribute("href", "/runs/newer");
    expect(screen.getByRole("radio", { name: /선수 B/ })).toBeDisabled();
  });

  it("서버가 충돌을 알리면 안내 문구를 보여 준다", async () => {
    setup("/runs/fixture-counter/corrections", (s) => {
      if (s.path === `${RUN}/corrections/preview`) return { status: 409, body: { code: "stale_run", title: "x", latest_run_id: "n" } };
      return base(s);
    });
    await userEvent.click(await screen.findByRole("radio", { name: /선수 B/ }));
    expect(await screen.findByText(/이미 정정되어 최신이 아닙니다. 최신 결과에서/)).toBeInTheDocument();
  });

  it("신고와 의도 설명을 접수한다", async () => {
    const seen = setup("/runs/fixture-counter/corrections", (s) => {
      if (s.method === "POST" && s.path === `${RUN}/reports`) return { status: 201, body: { report_id: "r", run_id: "fixture-counter", category: "technique", subject_ref: "E01", text: "푸시입니다", created_at: "t" } };
      if (s.method === "POST" && s.path === `${RUN}/context-notes`) return { status: 201, body: { note_id: "n", run_id: "fixture-counter", participant_id: null, start_us: 1_000_000, end_us: 2_500_000, text: "일부러", created_at: "t" } };
      return base(s);
    });
    await screen.findByRole("heading", { name: "정정·기록" });

    await userEvent.type(screen.getByPlaceholderText("예: E01"), "E01");
    await userEvent.type(screen.getAllByRole("textbox", { name: /내용/ })[0]!, "푸시입니다");
    await userEvent.click(screen.getByRole("button", { name: "신고 접수" }));
    await waitFor(() => expect(seen.find((s) => s.path === `${RUN}/reports` && s.method === "POST")?.body).toMatchObject({ category: "position", subject_ref: "E01", text: "푸시입니다" }));

    await userEvent.type(screen.getByRole("textbox", { name: /시작/ }), "1");
    await userEvent.type(screen.getByRole("textbox", { name: /끝/ }), "2.5");
    await userEvent.type(screen.getByRole("textbox", { name: /설명/ }), "일부러");
    await userEvent.click(screen.getByRole("button", { name: "설명 저장" }));
    await waitFor(() => expect(seen.find((s) => s.path === `${RUN}/context-notes` && s.method === "POST")?.body).toEqual({ participant_id: null, start_us: 1_000_000, end_us: 2_500_000, text: "일부러" }));
  });
});

describe("삭제", () => {
  const summary = (over: object = {}) => ({
    video_id: "v1", filename: "a.mp4", sport: "boxing", size_bytes: 10, duration_us: 1_000_000, width: 320, height: 180, status: "ready", run_id: "run-1", created_at: "t",
    job: null, media_state: "present", media_expires_at: "2026-10-12T00:00:00.000+00:00", ...over,
  });

  it("확인 없이는 지우지 않고, 전체 삭제가 끝나면 목록으로 돌아간다", async () => {
    const seen = setup("/videos/v1", (s) => {
      if (s.path === "/api/v1/videos/v1/summary") return { status: 200, body: summary() };
      if (s.method === "DELETE" && s.path === "/api/v1/videos/v1") return { status: 202, body: { video_id: "v1", status: "deleted" } };
      if (s.path === "/api/v1/videos" || s.path === "/api/v1/runs") return { status: 200, body: [] };
      return undefined;
    });
    expect(await screen.findByText(/2026-10-12에 자동 삭제 예정/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "영상과 결과 모두 삭제" }));
    expect(seen.some((s) => s.method === "DELETE")).toBe(false);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("되돌릴 수 없습니다");

    await userEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByRole("heading", { name: "경기·훈련 라이브러리" })).toBeInTheDocument();
  });

  it("정리가 끝나지 않았으면 완료라고 하지 않고 정리 중이라고 알린다", async () => {
    setup("/videos/v1", (s) => {
      if (s.path === "/api/v1/videos/v1/summary") return { status: 200, body: summary() };
      if (s.method === "DELETE") return { status: 202, body: { video_id: "v1", status: "deleting" } };
      return undefined;
    });
    await userEvent.click(await screen.findByRole("button", { name: "영상과 결과 모두 삭제" }));
    await userEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(await screen.findByText(/지우는 중입니다/)).toBeInTheDocument();
  });

  it("원본이 이미 삭제된 영상은 원본 삭제 버튼을 쓸 수 없다", async () => {
    setup("/videos/v1", (s) => (s.path === "/api/v1/videos/v1/summary" ? { status: 200, body: summary({ media_state: "expired", media_expires_at: null }) } : undefined));
    expect(await screen.findByText("보관 기간이 지나 원본 삭제됨")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "원본 영상만 삭제" })).toBeDisabled();
  });
});
