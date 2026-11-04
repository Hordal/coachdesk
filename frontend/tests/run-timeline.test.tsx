import { QueryClient,QueryClientProvider } from "@tanstack/react-query";
import { cleanup,render,screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { afterEach,expect,it,vi } from "vitest";
import { RunTimeline } from "../src/components/RunTimeline";
import { TimelineTrack } from "../src/components/TimelineTrack";
import { run } from "./fixtures";
import { formatTime } from "../src/lib/format";

afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});

it("shows pose observations when there are no action events, and plays a range inline",async()=>{
  vi.spyOn(HTMLMediaElement.prototype,"pause").mockImplementation(()=>{});
  vi.stubGlobal("fetch",vi.fn(async(input)=>{
    const path=String(input);
    const body=path.includes("/timeline")?{from_us:0,to_us:17000000,states:[],events:[],assessments:[],observations:[{participant_id:run.participants[0]!.participant_id,start_us:600000,end_us:1280000,sample_count:20}]}:path.endsWith("/playback")?{playable:true,access_url:"/signed"}:{...run,run_id:"r1",video_id:"v1",duration_us:17000000};
    return new Response(JSON.stringify(body));
  }));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter><RunTimeline runId="r1"/></MemoryRouter></QueryClientProvider>);
  const observed=await screen.findByRole("button",{name:/관절 관측 20개/});
  await userEvent.click(observed);
  expect(await screen.findByLabelText("타임라인 선택 구간 구간 재생")).toHaveAttribute("src","/signed");
  await userEvent.click(screen.getByRole("button",{name:"영상 닫기"}));
  expect(screen.queryByLabelText("타임라인 선택 구간 구간 재생")).not.toBeInTheDocument();
});

it("keeps the time axis visible even when no observations or events exist",()=>{
  render(<MemoryRouter><TimelineTrack runId="r1" fromUs={0} toUs={17000000} participants={[]} states={[]} events={[]}/></MemoryRouter>);
  expect(screen.getByText(formatTime(0))).toBeInTheDocument();
  expect(screen.getByText(formatTime(17000000))).toBeInTheDocument();
  expect(screen.getByText("이 구간에는 기록된 상태나 사건이 없습니다.")).toBeInTheDocument();
});
