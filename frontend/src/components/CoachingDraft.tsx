import { Link } from "react-router-dom";
import { useState } from "react";
import { CoachingClip } from "./CoachingClip";
import { useObservationStats } from "./ObservationSummary";
import { usePlayback } from "../api/hooks";
import { formatTime } from "../lib/format";
import { ProblemNotice } from "./ProblemNotice";

export function CoachingDraft({runId}:{runId:string}) {
  const [activeClip,setActiveClip]=useState<string|null>(null);
  const stats=useObservationStats(runId);
  const s=stats.data;
  const playback=usePlayback(s?.video_id ?? "",!!s?.video_id,240000);
  if(stats.isPending) return <p role="status">영상 관측을 바탕으로 코칭 초안을 정리하고 있습니다…</p>;
  if(stats.error || !s) return <ProblemNotice error={stats.error} onRetry={()=>void stats.refetch()}/>;
  const draft=s.coaching;
  return <>
    <section className="coaching-intro"><p className="overline">이번 훈련의 확인 과제</p><h2>공격 전후의 손 위치를 비교해 보세요</h2>
      <p>{draft?.cards.length ? "분석 선수의 손 위치를 확인할 장면을 골랐습니다. 관찰 내용과 영상을 비교한 뒤, 해당하는 연습 제안을 선택하세요." : "현재 관측만으로 개인별 기술 평가를 확정하기 어렵습니다. 아래 분석 상태에서 부족한 관측과 선수 지정 범위를 확인해 주세요. 메모 작성은 선택 사항입니다."}</p>
      <p className="report-boundary">{draft?.scope ?? "화면 좌표로 만든 초안입니다. 최종 판단은 영상을 확인한 코치가 합니다."}</p>
    </section>
    {draft?.observed_window ? <section className="workspace-panel panel-pad" aria-label="분석에 사용된 구간"><h2>분석에 사용된 구간</h2><p>{draft.observed_window.first_us===null?"선택한 선수의 관측이 없습니다.":`${formatTime(draft.observed_window.first_us)}–${formatTime(draft.observed_window.last_us ?? draft.observed_window.first_us)} · ${draft.observed_window.sample_count}개 관측`}</p><p className="muted">이 구간 사이에도 관측 공백이 있을 수 있습니다. 영상 전체와 범위가 크게 다르면 선수 지정에서 뒤쪽 장면의 같은 선수 번호를 추가로 연결하세요.</p><Link to={`/videos/${s.video_id}/observations`}>선수 지정·추적 끊김 확인 →</Link></section>:null}<section aria-label="장면별 코칭"><h2>관찰한 장면과 연습 제안</h2>
      {draft?.cards.map((card,i)=><article className="coaching-card" key={card.id}>
        <div className="coaching-card__image">{activeClip===card.id&&playback.data?.playable&&playback.data.access_url?<CoachingClip key={`${card.id}-${playback.data.access_url}`} src={playback.data.access_url} fromUs={card.from_us} toUs={Math.min(s.duration_us,card.to_us)} label={card.title} onClose={()=>setActiveClip(null)} onSourceError={()=>void playback.refetch()}/>:<><button type="button" className="coaching-clip__poster" aria-label={`${card.title} 구간 영상 재생`} onClick={()=>setActiveClip(card.id)}><div className="report-shot"><img src={`/api/v1/videos/${s.video_id}/frame-image?time_us=${card.image_us}`} alt={`${formatTime(card.image_us)} 분석 선수의 ${card.title} 확인 장면`} loading="lazy"/>{card.subject_box ? <div className="report-shot__subject" style={{left:`${card.subject_box.left}%`,top:`${card.subject_box.top}%`,width:`${card.subject_box.width}%`,height:`${card.subject_box.height}%`}}><b>분석 선수</b></div>:null}</div><span>▶ {formatTime(card.from_us)}–{formatTime(card.to_us)} · 여기서 구간 재생</span></button>{activeClip===card.id?<p role="status">{playback.isPending?"영상을 불러오고 있습니다…":"현재 영상을 재생할 수 없습니다. 영상 보관 상태를 확인해 주세요."}</p>:null}{activeClip===card.id&&playback.error?<ProblemNotice error={playback.error} onRetry={()=>void playback.refetch()}/>:null}</>}</div>
        <div><p className="overline">확인 과제 {i+1} · {card.status}</p><h3>{card.title}</h3><dl className="coaching-detail"><dt>관찰</dt><dd>{card.observation}</dd><dt>측정 기준</dt><dd>{card.measurement_note ?? "어깨·골반·손목을 이용한 화면상 위치 비교입니다."}</dd><dt>확인할 점</dt><dd>{card.interpretation}</dd><dt>연습 제안</dt><dd>{card.practice}</dd><dt>다음 촬영에서 확인</dt><dd>{card.check}</dd></dl></div>
      </article>)}
      {!draft?.cards.length ? <><p>{draft?.insufficient_reason ?? "자세를 평가할 관측이 부족합니다."}</p><h3>영상에서 직접 확인할 항목</h3><ul>{(draft?.manual_checks ?? ["공격 전후 손 위치 비교","동작 후 준비 자세 확인"]).map(t=><li key={t}>{t}</li>)}</ul></> : null}
    </section>
    <section aria-label="훈련 영상"><h2>영상 보면서 확인하기</h2><p>위 장면은 손 위치를 확인하기 위해 고른 것입니다. 펀치나 실수로 확정한 사건이 아닙니다.</p>
      {playback.data?.playable && playback.data.access_url ? <video className="report-video" src={playback.data.access_url} controls muted playsInline preload="metadata" onError={()=>void playback.refetch()}/> : playback.isPending ? <p>영상을 불러오고 있습니다…</p> : playback.error ? <ProblemNotice error={playback.error} onRetry={()=>void playback.refetch()}/> : <p>현재 원본 영상을 재생할 수 없습니다. 영상 보관 상태를 확인해 주세요.</p>}
      <p><Link to={`/runs/${runId}/scene`}>관절 표시와 함께 자세히 보기 →</Link></p>
    </section>
    <details className="report-method"><summary>측정 방식·분석 구간 자세히 보기</summary><p>선택된 선수 관절 관측 {s.sample_count}개 · 두 선수 동시 관측 {s.both_observed_frames}프레임. 공격 후보 {s.event_count}건. 후보 0건은 공격이 없었다는 뜻이 아닙니다.</p><p>손목·어깨·골반이 보이면 몸통 길이의 20%를 기준으로 비교합니다. 골반 관측이 부족하면 양 어깨·손목을 이용해 어깨 너비의 35%를 기준으로 별도 측정하고, 카드에 사용한 기준을 표시합니다. 최소 12개 관측과 0.5초 이상의 관측 범위가 필요합니다. 가림이 0.15초를 넘으면 연속 장면으로 연결하지 않습니다. 비율은 시간 비율이나 실수율이 아닙니다.</p><ul>{s.participants.map(p=><li key={p.participant_id}>{p.role==="USER"?"분석 선수":"상대"}: {p.first_us===null?"관측 없음":`${formatTime(p.first_us)}–${formatTime(p.last_us ?? p.first_us)}`} · {p.frame_count}프레임</li>)}</ul></details>
  </>;
}
