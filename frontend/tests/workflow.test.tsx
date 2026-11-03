import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { AnalysisWorkflow } from "../src/features/workspace/Workflow";

afterEach(()=>vi.unstubAllGlobals());
function setup({assigned=false,viewer=false,queued=false}={}) {
  const writes:string[]=[];
  vi.stubGlobal("fetch",vi.fn(async(input:string,init?:RequestInit)=>{
    const path=String(input);
    if(init?.method==="POST") { writes.push(path); return new Response(JSON.stringify({status:"ok"})); }
    if(path.endsWith("/teams")) return new Response(JSON.stringify({selected_team_id:viewer?"t":null,items:viewer?[{team_id:"t",name:"team",role:"viewer"}]:[]}));
    if(path.endsWith("/designation")) return new Response(JSON.stringify({state:assigned?"ASSIGNED":"NOT_RUN"}));
    return new Response(JSON.stringify(queued?[{task_id:"task1",kind:"tracking",status:"queued",progress:0,created_at:"2026-10-22T00:00:00Z",message:"",error:null,result:null}]:[]));
  }));
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter><AnalysisWorkflow videoId="video1" runId="run1"/></MemoryRouter></QueryClientProvider>);
  return writes;
}

it("선수 지정 전 동작 요청을 막고 검출 요청을 서버 큐에 넣는다",async()=>{
  const writes=setup();
  await screen.findByText(/자동 분석을 사용하려면/);
  expect(screen.getByRole("button",{name:"동작 분석 요청"})).toBeDisabled();
  await userEvent.click(screen.getByRole("button",{name:"검출·추적 요청"}));
  await waitFor(()=>expect(writes).toEqual(["/api/v1/videos/video1/analysis"]));
});

it("대기 작업에 가짜 진행률을 표시하지 않고 중복 요청을 막고 취소를 전송한다",async()=>{
  const writes=setup({queued:true});
  await screen.findByText("검출·추적 · 워커 연결 대기");
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByRole("button",{name:"검출·추적 요청"})).toBeDisabled();
  await userEvent.click(screen.getByRole("button",{name:"작업 취소"}));
  await waitFor(()=>expect(writes).toEqual(["/api/v1/analysis/tasks/task1/cancel"]));
});

it("팀 열람 담당자는 지정되어 있어도 분석 작업을 변경할 수 없다",async()=>{
  setup({assigned:true,viewer:true,queued:true});
  await screen.findByText("검출·추적 · 워커 연결 대기");
  await waitFor(()=>expect(screen.getByRole("button",{name:"작업 취소"})).toBeDisabled());
  expect(screen.getByRole("button",{name:"동작 분석 요청"})).toBeDisabled();
});
