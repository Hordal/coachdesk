import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportPage } from "../src/features/import/ImportPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/videos/v1/import"]}>
        <Routes>
          <Route path="videos/:videoId/import" element={<ImportPage />} />
          <Route path="runs/:runId" element={<p>결과 화면 {location.pathname}</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const pick = async (content: string) => {
  const input = screen.getByLabelText(/입력 파일/) as HTMLInputElement;
  await userEvent.upload(input, new File([content], "labels.json", { type: "application/json" }));
  await userEvent.click(screen.getByRole("button", { name: "가져오기" }));
};

afterEach(() => vi.unstubAllGlobals());

describe("가져오기 화면", () => {
  it("검증 오류 목록을 그대로 보여 준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ code: "invalid_import", title: "가져온 내용이 데이터 계약에 맞지 않음", trace_id: "t-1", errors: ["E99: 없는 ID를 참조합니다", "E01: 길이가 0입니다"], error_count: 2 }),
          { status: 422, headers: { "content-type": "application/problem+json" } },
        ),
      ),
    );
    renderPage();
    await pick('{"annotation_source":"a"}');
    expect(await screen.findByText("가져온 내용이 데이터 계약에 맞지 않음")).toBeInTheDocument();
    expect(screen.getByText("E99: 없는 ID를 참조합니다")).toBeInTheDocument();
    expect(screen.getByText("E01: 길이가 0입니다")).toBeInTheDocument();
    expect(screen.getByText(/t-1/)).toBeInTheDocument();
  });

  it("JSON이 아닌 파일은 서버에 보내지 않는다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await pick("not json");
    expect(await screen.findByText("JSON 파일로 읽을 수 없습니다")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("성공하면 새 실행의 결과 화면으로 이동한다", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      return new Response(JSON.stringify({ run_id: "run-9" }), { status: 201, headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await pick('{"annotation_source":"a"}');
    await waitFor(() => expect(screen.getByText(/결과 화면/)).toBeInTheDocument());
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/videos/v1/imports");
  });
});
