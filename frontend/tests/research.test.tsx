import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ResearchLibraryPage, ResearchWorkflow } from "../src/features/research/ResearchLibrary";

afterEach(()=>vi.unstubAllGlobals());
function wrap(element:React.ReactNode) {
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter><Routes><Route path="/" element={element}/><Route path="/videos/v1" element={<p>영상 상세 도착</p>}/><Route path="/runs/r1/report" element={<p>보고서 도착</p>}/></Routes></MemoryRouter></QueryClientProvider>);
}

it("연구 영상을 불러와 서버에서 반환한 영상으로 이동한다",async()=>{
  const writes:string[]=[];
  vi.stubGlobal("fetch",vi.fn(async(input:string,init?:RequestInit)=>{
    if(init?.method==="POST") {writes.push(String(input));return new Response(JSON.stringify({video_id:"v1"}));}
    return new Response(JSON.stringify({items:[{clip_id:"01",filename:"boxing.mp4",frame_count:407,source_url:null,has_research_pose:true}],note:"연구 결과"}));
  }));
  wrap(<ResearchLibraryPage/>);
  await userEvent.click(await screen.findByRole("button",{name:"이 영상으로 시작"}));
  expect(await screen.findByText("영상 상세 도착")).toBeInTheDocument();
  expect(writes).toEqual(["/api/v1/research/clips/01/import"]);
});

it("선수 지정 전에는 연구 보고서를 생성하지 못한다",()=>{
  wrap(<ResearchWorkflow videoId="v1" match={{available:true,ready:true,attached:true,assigned:false,has_research_pose:true}}/>);
  expect(screen.getByRole("button",{name:"분석·조언 보고서 생성"})).toBeDisabled();
});

it("관절 결과가 없는 영상은 내장 모델 분석을 안내한다",()=>{
  wrap(<ResearchWorkflow videoId="v1" match={{available:true,ready:true,attached:true,assigned:true,has_research_pose:false}}/>);
  expect(screen.getByRole("button",{name:"분석·조언 보고서 생성"})).toBeDisabled();
  expect(screen.getByText(/연구 관절 결과가 없습니다/)).toBeInTheDocument();
});

it("선수 지정 후 보고서를 생성하고 보고서 화면으로 이동한다",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({run_id:"r1"}))));
  wrap(<ResearchWorkflow videoId="v1" match={{available:true,ready:true,attached:true,assigned:true,has_research_pose:true}}/>);
  await userEvent.click(screen.getByRole("button",{name:"분석·조언 보고서 생성"}));
  expect(await screen.findByText("보고서 도착")).toBeInTheDocument();
});
