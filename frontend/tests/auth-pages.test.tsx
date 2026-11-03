import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../src/api/client";
import { App } from "../src/App";

interface Seen {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
}
type Reply = { status: number; body?: unknown } | undefined;

function setup(path: string, handler: (s: Seen) => Reply) {
  const seen: Seen[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const s: Seen = {
        method: init?.method ?? "GET",
        path: new URL(String(input), "http://localhost").pathname,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
        headers: (init?.headers ?? {}) as Record<string, string>,
      };
      seen.push(s);
      const r = handler(s) ?? { status: 404, body: { code: "not_found", title: "없음" } };
      if (r.status === 204) return new Response(null, { status: 204 });
      return new Response(JSON.stringify(r.body ?? {}), { status: r.status, headers: { "content-type": r.status >= 400 ? "application/problem+json" : "application/json" } });
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

const me = (role = "participant") => ({ user_id: "u1", email: "p@example.com", display_name: "참가자", role, csrf_token: "csrf-1", auth_mode: "session" });
const unauth = { status: 401, body: { code: "unauthenticated", title: "로그인이 필요함" } };

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("로그인", () => {
  it("로그인하지 않았으면 로그인 화면으로 보낸다", async () => {
    setup("/", (s) => (s.path === "/api/v1/me" ? unauth : undefined));
    expect(await screen.findByRole("heading", { name: "로그인" })).toBeInTheDocument();
  });

  it("로그인하면 원래 가려던 화면으로 가고, 이후 변경 요청에 CSRF 토큰을 붙인다", async () => {
    let loggedIn = false;
    const seen = setup("/upload", (s) => {
      if (s.path === "/api/v1/me") return loggedIn ? { status: 200, body: me() } : unauth;
      if (s.path === "/api/v1/auth/login") {
        loggedIn = true;
        return { status: 200, body: me() };
      }
      if (s.path === "/api/v1/uploads" && s.method === "POST") return { status: 201, body: {} };
      if (s.path === "/api/v1/runs") return { status: 200, body: [] };
      return undefined;
    });
    await userEvent.type(await screen.findByLabelText(/이메일/), "p@example.com");
    await userEvent.type(screen.getByLabelText(/비밀번호/), "correct horse battery");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(await screen.findByRole("heading", { name: "영상 올리기" })).toBeInTheDocument();
    expect(seen.find((s) => s.path === "/api/v1/auth/login")?.body).toEqual({ email: "p@example.com", password: "correct horse battery" });
    expect(screen.getByText("참가자")).toBeInTheDocument();

    // 로그인 뒤의 변경 요청에는 세션의 CSRF 토큰이 붙는다
    const { apiRequest } = await import("../src/api/client");
    await apiRequest("/uploads", { method: "POST", json: {} });
    expect(seen.filter((s) => s.path === "/api/v1/uploads").at(-1)?.headers["X-CSRF-Token"]).toBe("csrf-1");
    await apiRequest("/runs");
    expect(seen.filter((s) => s.path === "/api/v1/runs").at(-1)?.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("잘못된 로그인과 잠금은 안내 문구로 보여 준다", async () => {
    setup("/login", (s) => {
      if (s.path === "/api/v1/me") return unauth;
      if (s.path === "/api/v1/auth/login") return { status: 429, body: { code: "too_many_attempts", title: "x" } };
      return undefined;
    });
    await userEvent.type(await screen.findByLabelText(/이메일/), "p@example.com");
    await userEvent.type(screen.getByLabelText(/비밀번호/), "whatever whatever");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));
    expect(await screen.findByText(/로그인 시도가 너무 많습니다/)).toBeInTheDocument();
  });

  it("로그아웃하면 서버에 알리고 로그인 화면으로 간다", async () => {
    const seen = setup("/", (s) => {
      if (s.path === "/api/v1/me") return { status: 200, body: me() };
      if (s.path === "/api/v1/auth/logout") return { status: 204 };
      if (s.path === "/api/v1/videos" || s.path === "/api/v1/runs") return { status: 200, body: [] };
      return undefined;
    });
    await userEvent.click(await screen.findByRole("button", { name: "로그아웃" }));
    expect(await screen.findByRole("heading", { name: "로그인" })).toBeInTheDocument();
    expect(seen.some((s) => s.path === "/api/v1/auth/logout" && s.method === "POST")).toBe(true);
  });
});

describe("초대 수락", () => {
  const invite = (s: Seen): Reply => (s.path === "/api/v1/auth/invites/tok" ? { status: 200, body: { email: "new@example.com", role: "participant" } } : undefined);

  it("동의해야 계정을 만들 수 있고, 만들면 로그인 화면으로 간다", async () => {
    const seen = setup("/invite/tok", (s) => {
      if (s.path === "/api/v1/auth/invites/tok/accept") return { status: 201, body: { email: "new@example.com", role: "participant" } };
      if (s.path === "/api/v1/me") return unauth;
      return invite(s);
    });
    expect(await screen.findByText(/new@example.com 주소로 초대/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/이름/), "새 참가자");
    await userEvent.type(screen.getByLabelText(/비밀번호/), "correct horse battery");
    const submit = screen.getByRole("button", { name: "계정 만들기" });
    expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(submit);
    await waitFor(() => expect(seen.find((s) => s.path.endsWith("/accept"))?.body).toEqual({ display_name: "새 참가자", password: "correct horse battery", consent: true }));
    expect(await screen.findByRole("heading", { name: "로그인" })).toBeInTheDocument();
  });

  it("유효하지 않은 초대는 이유를 구별하지 않고 안내한다", async () => {
    setup("/invite/bad", () => ({ status: 404, body: { code: "invite_invalid", title: "x" } }));
    expect(await screen.findByText(/유효하지 않거나 만료된 초대/)).toBeInTheDocument();
  });
});

describe("계정 관리", () => {
  it("관리자가 아니면 들어갈 수 없다", async () => {
    setup("/admin", (s) => {
      if (s.path === "/api/v1/me") return { status: 200, body: me("participant") };
      if (s.path === "/api/v1/videos" || s.path === "/api/v1/runs") return { status: 200, body: [] };
      return undefined;
    });
    expect(await screen.findByRole("heading", { name: "경기·훈련 라이브러리" })).toBeInTheDocument();
  });

  it("초대 링크는 만든 직후에만 보여 주고 목록에는 싣지 않는다", async () => {
    setup("/admin", (s) => {
      if (s.path === "/api/v1/me") return { status: 200, body: me("admin") };
      if (s.path === "/api/v1/admin/invites" && s.method === "POST") return { status: 201, body: { invite_id: "i1", accept_path: "/invite/SECRET-TOKEN", expires_at: 1 } };
      if (s.path === "/api/v1/admin/invites") return { status: 200, body: [{ invite_id: "i1", email: "n@example.com", role: "participant", state: "pending", created_at: "t", expires_at: 1 }] };
      if (s.path === "/api/v1/admin/users") return { status: 200, body: [{ user_id: "u1", email: "p@example.com", display_name: "나", role: "admin", status: "active", created_at: "t", last_login_at: null }, { user_id: "u2", email: "q@example.com", display_name: "다른 사람", role: "participant", status: "active", created_at: "t", last_login_at: null }] };
      return undefined;
    });
    await userEvent.type(await screen.findByLabelText(/이메일/), "n@example.com");
    await userEvent.click(screen.getByRole("button", { name: "초대 만들기" }));
    expect(await screen.findByText(/SECRET-TOKEN/)).toBeInTheDocument();
    expect(screen.getByText(/지금만 볼 수 있습니다/)).toBeInTheDocument();
    expect(screen.getByText(/n@example.com · 참여자 · 대기 중/)).toBeInTheDocument();
    // 자기 자신은 차단할 수 없다
    expect(screen.getAllByRole("button", { name: "차단" })).toHaveLength(1);
  });
});
