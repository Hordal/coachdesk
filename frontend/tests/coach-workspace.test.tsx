import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { CoachHome, CoachReviewPage, SessionEditor } from "../src/features/workspace/CoachWorkspace";

afterEach(() => vi.unstubAllGlobals());
const item = { video_id:"v1",filename:"video.mp4",title:"코치 기록",athlete_id:null,athlete_name:null,opponent:"",session_date:null,session_type:"sparring",notes:"",revision:0,run_id:"r1",event_count:1,status:"ready",sport:"boxing" };
function setup(component: React.ReactNode, path="/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry:false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes><Route path="*" element={component}/></Routes></MemoryRouter></QueryClientProvider>);
}

it("촬영일의 input 이벤트를 저장 요청에 넣고 버전 충돌을 피할 기준을 보낸다", async () => {
  let saved: Record<string,unknown> | null = null;
  vi.stubGlobal("fetch",vi.fn(async (input: string,init?: RequestInit) => {
    const path = String(input);
    if (init?.method === "PUT") { saved = JSON.parse(String(init.body)); return new Response(JSON.stringify({ ...item,...saved,revision:1 })); }
    return new Response(JSON.stringify(path.endsWith("athletes") ? [] : item));
  }));
  setup(<SessionEditor videoId="v1" />);
  await userEvent.click(await screen.findByRole("button",{ name:"기록 편집" }));
  fireEvent.input(screen.getByLabelText("촬영일"),{ target:{value:"2026-10-22"} });
  await userEvent.click(screen.getByRole("button",{ name:"기록 저장" }));
  await waitFor(() => expect(saved).toMatchObject({ session_date:"2026-10-22",expected_revision:0 }));
});

it("빈 검수 의견으로 완료할 수 없고 충돌하면 오류와 작성한 의견을 유지한다", async () => {
  vi.stubGlobal("fetch",vi.fn(async (input:string,init?:RequestInit) => {
    if (init?.method === "PUT") return new Response(JSON.stringify({ code:"stale_review",title:"다른 화면에서 검수가 바뀌었습니다" }),{ status:409 });
    return new Response(JSON.stringify(String(input).endsWith("review-history") ? [] : { run_id:"r1",state:"pending",note:"",revision:0,reviewer:null,updated_at:null }));
  }));
  const client = new QueryClient({ defaultOptions:{queries:{retry:false}} });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/runs/r1/review"]}><Routes><Route path="runs/:runId/review" element={<CoachReviewPage/>}/></Routes></MemoryRouter></QueryClientProvider>);
  await screen.findByRole("button",{name:"검수 기록 저장"});
  await userEvent.selectOptions(screen.getByRole("combobox",{name:"검수 상태"}),"reviewed");
  expect(screen.getByRole("button",{name:"검수 기록 저장"})).toBeDisabled();
  await userEvent.type(screen.getByLabelText("코칭 의견·다음 훈련 목표"),"장면 확인 후 코치 의견");
  await userEvent.click(screen.getByRole("button",{name:"검수 기록 저장"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("다른 화면에서 검수가 바뀌었습니다");
  expect(screen.getByLabelText("코칭 의견·다음 훈련 목표")).toHaveValue("장면 확인 후 코치 의견");
});

it("검색과 필터를 서버에 보내고 결과가 없으면 등록 영상인 것처럼 채우지 않는다", async () => {
  const seen:string[] = [];
  vi.stubGlobal("fetch",vi.fn(async (input:string) => { seen.push(String(input)); return new Response(JSON.stringify({ items:[],total:0,page:1,page_size:12,counts:{total:0,processing:0,pending:0,reviewed:0} })); }));
  setup(<CoachHome/>);
  await screen.findByRole("heading",{name:"첫 번째 훈련 기록을 시작하세요"});
  await userEvent.type(screen.getByLabelText("영상 또는 선수 검색"),"김코치");
  await userEvent.click(screen.getByRole("button",{name:"검색"}));
  await waitFor(() => expect(seen.some((url) => decodeURIComponent(url).includes("q=김코치"))).toBe(true));
  expect(await screen.findByRole("heading",{name:"조건에 맞는 기록이 없습니다"})).toBeInTheDocument();
});
