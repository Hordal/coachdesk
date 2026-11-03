import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render,screen,waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach,expect,it,vi } from "vitest";
import { ObservationSummary } from "../src/components/ObservationSummary";
import { SummaryPage } from "../src/features/findings/SummaryPage";
import { CoachingDraft } from "../src/components/CoachingDraft";

afterEach(()=>vi.unstubAllGlobals());
function show(element:React.ReactNode, response:(path:string)=>unknown) {
  vi.stubGlobal("fetch",vi.fn(async(input)=>new Response(JSON.stringify(response(String(input))),{headers:{"content-type":"application/json"}})));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter>{element}</MemoryRouter></QueryClientProvider>);
}
it("shows actual observations and links to a bounded scene without inventing coaching",async()=>{
  show(<ObservationSummary runId="r1"/>,()=>({available:true,sample_count:3,event_count:0,both_observed_frames:1,suggested_time_us:200,duration_us:1000000,participants:[{participant_id:"A",role:"USER",frame_count:2,first_us:100,last_us:200}]}));
  expect(await screen.findByText("이번 영상에서 확인한 내용")).toBeInTheDocument();
  expect(screen.getByText(/공격 기술·명중·득점은 확정하지 않았습니다/)).toBeInTheDocument();
  expect(screen.getByRole("link",{name:"관절이 관측된 장면 보기 →"})).toHaveAttribute("href","/runs/r1/scene?from_us=200&to_us=1000000");
});
it("does not call an empty evidence list a missing original video",async()=>{
  show(<SummaryPage/>,path=>path.endsWith("/findings")||path.includes("/evidence")?[]:path.endsWith("/playback")?{playable:true}:path.endsWith("/observation-summary")?{available:false,sample_count:0,event_count:0,participants:[],both_observed_frames:0,suggested_time_us:null,duration_us:1000000}:{video_id:"v1",dataset_kind:"model"});
  expect(await screen.findByText("원본 영상은 장면 복기에서 볼 수 있습니다 →")).toBeInTheDocument();
  expect(screen.queryByText(/원본 영상이 없어 재생할 수 없습니다/)).not.toBeInTheDocument();
});
it("shows grounded coaching scenes, practice and the real video together",async()=>{
  show(<CoachingDraft runId="r1"/>,path=>path.endsWith("/playback")?{playable:true,access_url:"/signed-clip"}:{available:true,video_id:"v1",duration_us:5000000,participants:[],coaching:{scope:"화면 좌표 기반 초안",cards:[{id:"right-hand-height",title:"오른손 위치 확인",observation:"확인 가능한 장면에서 측정한 관찰",interpretation:"공격 동작인지 확인하세요",practice:"복귀 지연을 코치가 확인하면 연습하세요",check:"같은 각도로 다시 촬영하세요",from_us:1000000,to_us:1300000,image_us:1000000,status:"확인할 장면"}],manual_checks:[]}});
  expect(await screen.findByText("오른손 위치 확인")).toBeInTheDocument();
  expect(screen.getByText("복귀 지연을 코치가 확인하면 연습하세요")).toBeInTheDocument();
  expect(screen.getByRole("img")).toHaveAttribute("src","/api/v1/videos/v1/frame-image?time_us=1000000");
  await waitFor(()=>expect(document.querySelector("video")).toHaveAttribute("src","/signed-clip"));
});
it("provides a manual checklist instead of fabricating measured coaching when poses are insufficient",async()=>{
  show(<CoachingDraft runId="r1"/>,()=>({available:false,participants:[],coaching:{cards:[],scope:"측정 제한",insufficient_reason:"손목이 보이지 않습니다",manual_checks:["공격 전후 손 위치 비교"]}}));
  expect(await screen.findByText("손목이 보이지 않습니다")).toBeInTheDocument();
  expect(screen.getByText("공격 전후 손 위치 비교")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
