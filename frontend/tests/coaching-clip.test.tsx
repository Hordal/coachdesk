import { render,screen,fireEvent,cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach,beforeEach,expect,it,vi } from "vitest";
import { CoachingClip } from "../src/components/CoachingClip";

beforeEach(()=>{
  vi.stubGlobal("requestAnimationFrame",vi.fn(()=>1));
  vi.stubGlobal("cancelAnimationFrame",vi.fn());
  vi.spyOn(HTMLMediaElement.prototype,"play").mockImplementation(function(this:HTMLMediaElement){this.dispatchEvent(new Event("play"));return Promise.resolve();});
  vi.spyOn(HTMLMediaElement.prototype,"pause").mockImplementation(function(this:HTMLMediaElement){this.dispatchEvent(new Event("pause"));});
});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
function setup(duration=10) {
  const close=vi.fn(),refresh=vi.fn();
  render(<CoachingClip src="/signed" fromUs={600000} toUs={2600000} label="왼손 확인" onClose={close} onSourceError={refresh}/>);
  const video=screen.getByLabelText("왼손 확인 구간 재생") as HTMLVideoElement;
  Object.defineProperty(video,"duration",{value:duration,configurable:true});
  fireEvent.loadedMetadata(video);
  return {video,close,refresh};
}

it("starts at the selected time, stops at the end, and replays only that interval",async()=>{
  const {video}=setup();
  expect(video.currentTime).toBe(.6);
  expect(video.play).toHaveBeenCalledOnce();
  video.currentTime=2.7;
  fireEvent.timeUpdate(video);
  expect(video.currentTime).toBe(2.6);
  expect(video.pause).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button",{name:"이 구간 다시 보기"}));
  expect(video.currentTime).toBe(.6);
  expect(video.play).toHaveBeenCalledTimes(2);
});

it("keeps seeking inside the selected interval and can close without navigation",async()=>{
  const {video,close}=setup();
  video.currentTime=0;fireEvent.seeking(video);expect(video.currentTime).toBe(.6);
  video.currentTime=8;fireEvent.seeking(video);expect(video.currentTime).toBe(2.6);
  await userEvent.click(screen.getByRole("button",{name:"영상 닫기"}));
  expect(close).toHaveBeenCalledOnce();
});

it("explains playback rejection without an unhandled error",async()=>{
  vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new Error("blocked"));
  setup();
  expect(await screen.findByText("재생 버튼을 눌러 다시 시도해 주세요.")).toBeInTheDocument();
});

it("does not try to seek or play beyond the actual video duration",()=>{
  const {video}=setup(.3);
  expect(video.play).not.toHaveBeenCalled();
  expect(video.currentTime).toBe(0);
  expect(screen.getByText("이 영상에는 해당 시간 구간이 없습니다.")).toBeInTheDocument();
});
