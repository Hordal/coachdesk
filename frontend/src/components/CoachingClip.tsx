import { useEffect, useRef, useState } from "react";
import { formatTime } from "../lib/format";

export function CoachingClip({src,fromUs,toUs,label,onClose,onSourceError,onTimeChange}:{src:string;fromUs:number;toUs:number;label:string;onClose:()=>void;onSourceError:()=>void;onTimeChange?:(us:number)=>void}) {
  const videoRef=useRef<HTMLVideoElement>(null);
  const start=Math.max(0,fromUs/1_000_000);
  const finish=Math.max(start,toUs/1_000_000);
  const [ready,setReady]=useState(false);
  const [playing,setPlaying]=useState(false);
  const [at,setAt]=useState(start);
  const [error,setError]=useState("");
  useEffect(()=>{const video=videoRef.current;return ()=>video?.pause();},[]);
  const end=(video:HTMLVideoElement)=>Number.isFinite(video.duration)?Math.min(finish,video.duration):finish;
  function update() {
    const video=videoRef.current;
    if(!video)return;
    const limit=end(video);
    if(limit<=start){video.pause();setPlaying(false);return;}
    if(video.currentTime>=limit) {
      video.pause();
      if(video.currentTime!==limit)video.currentTime=limit;
      setPlaying(false);
    } else if(video.currentTime<start)video.currentTime=start;
    setAt(Math.max(start,Math.min(limit,video.currentTime)));
    onTimeChange?.(Math.round(Math.max(start,Math.min(limit,video.currentTime))*1_000_000));
  }
  async function play(restart=false) {
    const video=videoRef.current;
    if(!video)return;
    setError("");
    if(end(video)<=start){setError("이 영상에는 해당 시간 구간이 없습니다.");return;}
    if(restart||video.currentTime<start||video.currentTime>=end(video)-.001)video.currentTime=start;
    try {await video.play();} catch {setPlaying(false);setError("재생 버튼을 눌러 다시 시도해 주세요.");}
  }
  useEffect(()=>{
    if(!playing)return;
    let handle=0;
    function tick(){update();handle=requestAnimationFrame(tick);}
    handle=requestAnimationFrame(tick);
    return ()=>cancelAnimationFrame(handle);
  },[playing,start,finish]);
  return <div className="coaching-clip" aria-label={`${label} 구간 영상`}>
    <video ref={videoRef} src={src} aria-label={`${label} 구간 재생`} muted playsInline preload="metadata"
      onLoadedMetadata={()=>{const video=videoRef.current;if(video){if(end(video)<=start){setError("이 영상에는 해당 시간 구간이 없습니다.");return;}video.currentTime=start;setAt(start);setReady(true);void play(true);}}}
      onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)}
      onTimeUpdate={update} onSeeking={update}
      onError={()=>{setError("영상을 불러오지 못했습니다. 재생 주소를 다시 확인하고 있습니다.");onSourceError();}}/>
    <p className="muted">{formatTime(fromUs)}–{formatTime(toUs)} 구간만 재생합니다.</p>
    <div className="coaching-clip__controls"><button type="button" disabled={!ready} onClick={()=>playing?videoRef.current?.pause():void play()}>{playing?"일시정지":"구간 재생"}</button><button type="button" disabled={!ready} onClick={()=>void play(true)}>이 구간 다시 보기</button><button type="button" onClick={onClose}>영상 닫기</button></div>
    <label className="coaching-clip__seek">구간 내 이동<input type="range" aria-label={`${label} 구간 내 이동`} min={start} max={finish} step="0.01" value={Math.max(start,Math.min(finish,at))} disabled={!ready} onChange={e=>{const video=videoRef.current;if(video){video.currentTime=Number(e.target.value);update();}}}/></label>
    <span aria-live="off">{formatTime(Math.round(at*1_000_000))}</span>{error?<p role="status">{error}</p>:null}
  </div>;
}
