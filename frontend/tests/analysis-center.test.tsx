import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render,screen,waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach,expect,it,vi } from "vitest";
import { AnalysisCenterPage,GuardRuleResult } from "../src/features/workspace/AnalysisCenter";

afterEach(()=>vi.unstubAllGlobals());
function show(element:React.ReactNode,respond:(path:string)=>unknown) {
  const writes:string[]=[];
  vi.stubGlobal("fetch",vi.fn(async(input,init)=>{if(init?.method&&init.method!=="GET")writes.push(String(input));return new Response(JSON.stringify(defaultResponse(String(input)) ?? respond(String(input))));}));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter>{element}</MemoryRouter></QueryClientProvider>);
  return writes;
}
function defaultResponse(path:string):unknown {
  if(path.endsWith("/me"))return {auth_mode:"dev",role:"participant",user_id:"public-demo"};
  if(path.endsWith("/analysis/local-status"))return {installed:true,running:true,available:true};
  if(path.endsWith("/teams"))return {selected_team_id:null,items:[]};
  if(path.includes("/analysis/tasks")||path.endsWith("/analysis/keys"))return [];
  if(path.endsWith("/designation"))return {state:"NOT_RUN"};
  if(path.endsWith("/research"))return {available:false};
  return undefined;
}
const item=(id:string,status="ready")=>({video_id:id,title:`영상 ${id}`,filename:`${id}.mp4`,status,run_id:null,dataset_kind:"unanalyzed"});
const held={note:"관찰 규칙",states:[],pose_samples:0,unknown_count:0,unknown_reasons:{},participants:[],confirmed_attacks:0,candidate_events:2,guard_return_status:"held",guard_return_reason:"확인된 공격과 뒷손 상태가 필요합니다",guard_return:null};

it("lets the user select a video without automatically starting or duplicating analysis",async()=>{
  const writes=show(<AnalysisCenterPage/>,path=>path.includes("/sessions?")?{items:[item("v1"),item("v2")],total:2}:item(path.endsWith("v2")?"v2":"v1"));
  await screen.findByRole("heading",{name:"영상 v1"});
  await userEvent.selectOptions(screen.getByRole("combobox",{name:"분석할 영상"}),"v2");
  expect(await screen.findByRole("heading",{name:"영상 v2"})).toBeInTheDocument();
  expect(writes).toEqual([]);
});

it("blocks the analysis workflow while the video is preparing",async()=>{
  show(<AnalysisCenterPage/>,path=>path.includes("/sessions?")?{items:[item("v1","processing")],total:1}:item("v1","processing"));
  expect(await screen.findByText(/영상 준비 중입니다/)).toBeInTheDocument();
  expect(screen.queryByRole("heading",{name:"분석과 검수"})).not.toBeInTheDocument();
});

it("explains missing observations and does not report unconfirmed candidates as attacks",async()=>{
  show(<GuardRuleResult runId="r1"/>,()=>held);
  expect(await screen.findByText(/가드 상태를 판단할 연속 관측이 부족/)).toBeInTheDocument();
  expect(screen.getByText("확인된 공격과 뒷손 상태가 필요합니다")).toBeInTheDocument();
  expect(screen.getByText(/확인된 공격 0건 · 집계 보류 사건 2건/)).toBeInTheDocument();
});

it("links observed guard intervals to the original run and bounded scene",async()=>{
  show(<GuardRuleResult runId="r1"/>,()=>({...held,participants:[{participant_id:"A",role:"USER"}],states:[{state_id:"s1",participant_id:"A",body_part:"left_hand",label:"high_guard",start_us:1000000,end_us:1500000}]}));
  expect(await screen.findByText("높은 손 위치")).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByRole("link")).toHaveAttribute("href","/runs/r1/scene?from_us=1000000&to_us=1500000"));
});
