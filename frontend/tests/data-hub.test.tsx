import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { DataHubPage, readRows } from "../src/features/import/DataHubPage";

afterEach(() => vi.unstubAllGlobals());

it("JSONL 오류에 원본 파일 이름과 줄 번호를 표시한다", async () => {
  const file = { name: "poses.jsonl", size: 30, text: async () => '{"frame_index":0}\n\ninvalid' } as File;
  await expect(readRows(file)).rejects.toThrow("poses.jsonl: 3번째 줄");
});

it("BOM·빈 줄·JSON 배열을 받고 빈 파일은 거절한다", async () => {
  const file = (text: string) => ({ name: "input.jsonl", size: text.length, text: async () => text }) as File;
  expect(await readRows(file('\uFEFF{"frame_index":0}\n\n{"frame_index":1}'))).toEqual([{ frame_index: 0 }, { frame_index: 1 }]);
  expect(await readRows(file('[{"frame_index":0}]'))).toEqual([{ frame_index: 0 }]);
  await expect(readRows(file('  '))).rejects.toThrow("파일이 비어 있습니다");
});

it("필수 파일이 없으면 가져오기를 막고 종류를 바꾸면 파일 선택을 초기화한다", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "poses_not_found", title: "가져온 포즈가 없음" }), { status: 404 })));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/videos/v1/data"]}>
    <Routes><Route path="videos/:videoId/data" element={<DataHubPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(screen.getByRole("button", { name: "검증 후 가져오기" })).toBeDisabled();
  await userEvent.upload(screen.getByLabelText("포즈 관측 파일 (필수)"), new File(['{"frame_index":0}'], "poses.jsonl"));
  expect(screen.getByRole("button", { name: "검증 후 가져오기" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "동작 후보" }));
  expect(screen.getByRole("button", { name: "검증 후 가져오기" })).toBeDisabled();
  expect(screen.queryByLabelText("포즈 품질 파일 (선택)")).not.toBeInTheDocument();
});
