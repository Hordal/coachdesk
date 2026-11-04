import { Link } from "react-router-dom";
import { useState } from "react";

import { ApiError, type ObservationSetView } from "../../api/client";
import { useDesignation, useDesignationFrames, useSaveDesignation } from "../../api/hooks";
import { Loading } from "../../components/ProblemNotice";
import { formatTime } from "../../lib/format";
import { DESIGNATION_ERROR } from "../../strings/ko";

type Choice = "none" | "user" | "opponent";

function messageOf(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    const base = DESIGNATION_ERROR[error.code] ?? error.message;
    return error.details.length > 0 ? `${base} ${error.details.join(" ")}` : base;
  }
  return "저장하지 못했습니다";
}

/**
 * 선수 지정. 후보 프레임에서 트랙을 보고 분석 선수와 상대를 직접 고른다.
 * 번호 순서나 화면 위치로 미리 정해 두지 않으며, 선택하기 전에는 배정이 시작되지 않은 상태다.
 */
export function DesignationPanel({ videoId, set }: { videoId: string; set: ObservationSetView }) {
  const frames = useDesignationFrames(videoId, set.set_id);
  const current = useDesignation(videoId);
  const save = useSaveDesignation(videoId);
  const [picked, setPicked] = useState<Record<number, Choice>>({});
  const [activeRole, setActiveRole] = useState<"user" | "opponent">("user");
  const [frameIndex, setFrameIndex] = useState(0);
  const [soloChoice, setSoloChoice] = useState<boolean | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);

  if (frames.isPending || current.isPending) return <Loading />;
  if (!frames.data) return null;

  const assigned = current.data?.state === "ASSIGNED" && current.data.set_id === set.set_id;
  const saved = (role: "USER" | "OPPONENT") => current.data?.assignments.find((a) => a.role === role)?.track_ids ?? [];
  const choiceOf = (trackId: number): Choice => {
    if (trackId in picked) return picked[trackId]!;
    if (assigned) return saved("USER").includes(trackId) ? "user" : saved("OPPONENT").includes(trackId) ? "opponent" : "none";
    return "none";
  };
  const ids = (choice: Choice) => set.tracks.map((t) => t.track_id).filter((id) => choiceOf(id) === choice);
  const user = ids("user");
  const opponent = ids("opponent");
  const savedSolo = assigned && saved("OPPONENT").length === 0;
  const solo = soloChoice ?? savedSolo;
  const dirty = Object.keys(picked).length > 0 || (soloChoice !== null && soloChoice !== savedSolo);
  const insufficient = solo ? set.tracks.length < 1 || frames.data.frames.length === 0 : frames.data.candidate_state === "INSUFFICIENT";
  const selectedFrame = frames.data.frames[Math.min(frameIndex, frames.data.frames.length - 1)];
  const ready = user.length > 0 && (solo || opponent.length > 0);

  function pickPerson(trackId: number) {
    const other = activeRole === "user" ? "opponent" : "user";
    if (choiceOf(trackId) === other) {
      setSelectionError("같은 사람을 두 번 고를 수 없습니다. 다른 사람의 상자를 누르세요. 역할을 반대로 골랐다면 ‘분석 선수와 상대 바꾸기’를 누르세요.");
      return;
    }
    const next: Record<number, Choice> = {};
    for (const t of set.tracks) next[t.track_id] = choiceOf(t.track_id) === activeRole ? "none" : choiceOf(t.track_id);
    next[trackId] = activeRole;
    setPicked(next);
    save.reset();
    setSelectionError(null);
    if (activeRole === "user" && !solo) setActiveRole("opponent");
  }

  function swap() {
    const next: Record<number, Choice> = {};
    for (const id of user) next[id] = "opponent";
    for (const id of opponent) next[id] = "user";
    for (const t of set.tracks) if (!(t.track_id in next)) next[t.track_id] = "none";
    setPicked(next);
  }

  return (
    <section className="card person-picker" aria-labelledby="designation-title">
      <h2 id="designation-title">{solo ? "사진에서 분석할 선수를 골라 주세요" : "사진에서 두 선수를 골라 주세요"}</h2>
      <label>영상 유형 <select aria-label="영상 유형" value={solo ? "solo" : "sparring"} onChange={e => { setSoloChoice(e.target.value === "solo"); setPicked(Object.fromEntries(set.tracks.map(t => [t.track_id, "none" as Choice]))); setActiveRole("user"); setSelectionError(null); save.reset(); }}><option value="sparring">스파링 · 두 선수</option><option value="solo">개인 훈련 · 한 선수</option></select></label>
      <p>아래 사진의 <strong>사람을 둘러싼 상자</strong>를 누르세요. {solo ? "분석할 선수 한 명만 고르면 됩니다." : "먼저 분석할 선수, 다음으로 상대를 고릅니다."} 번호를 외울 필요는 없습니다.</p>
      <div className="person-picker__steps" aria-label="선수 선택 순서">
        <button type="button" aria-pressed={activeRole === "user"} onClick={() => setActiveRole("user")}><strong>1. 분석할 선수</strong><span>{user.length ? `선택됨 · 번호 ${user.join(", ")}` : "아직 선택하지 않았어요"}</span></button>
        {!solo ? <button type="button" aria-pressed={activeRole === "opponent"} onClick={() => setActiveRole("opponent")}><strong>2. 상대 선수</strong><span>{opponent.length ? `선택됨 · 번호 ${opponent.join(", ")}` : "아직 선택하지 않았어요"}</span></button> : null}
      </div>
      <p className="person-picker__instruction" role="status">{ready ? "선수를 골랐습니다. 사진의 색 표시를 확인하고 아래에서 저장하세요. 바꾸려면 위에서 역할을 고른 뒤 다른 사람을 누르세요." : activeRole === "user" ? "① 분석하고 싶은 선수의 상자를 누르세요." : "② 분석 선수와 맞서는 상대의 상자를 누르세요."}</p>
      {insufficient ? (
        <p className="notice notice--error" role="alert">{solo ? "분석할 후보가 없습니다. 검출·추적을 다시 요청해 주세요." : "후보 트랙이 두 개 미만입니다. 혼자 나온 영상이면 개인 훈련으로 변경하세요."}</p>
      ) : null}
      {!solo && frames.data.frames.length > 0 && !frames.data.frames.some((f) => f.separated) ? (
        <p className="notice notice--hold" role="note">두 후보가 서로 떨어져 보이는 프레임이 없어 겹친 장면을 보여 줍니다. 상자 번호를 잘 확인해 주세요.</p>
      ) : null}

      <div className="person-picker__actions">
        <p>{!user.length ? "다음 할 일: 사진에서 분석할 선수를 선택하세요." : !solo && !opponent.length ? "다음 할 일: 사진에서 상대 선수를 선택하세요." : assigned && !dirty ? "선수 선택이 저장되어 있습니다. 동작 분석 단계로 이동하세요." : "다음 할 일: 선택을 저장하고 동작 분석 단계로 이동하세요."}</p>
        <button type="button" disabled={insufficient || !ready || (assigned && !dirty) || save.isPending} onClick={() => save.mutate({set_id:set.set_id,user_track_ids:user,opponent_track_ids:solo ? [] : opponent,mode:solo ? "solo" : "sparring"},{onSuccess:()=>setPicked({})})}>{save.isPending ? "선수 선택 저장 중…" : "선택 저장"}</button>
        {assigned && !dirty ? <Link className="action-link" to={`/videos/${videoId}`}>다음: 동작 분석 →</Link> : null}
      </div>

      <div className="person-picker__preview">
        {selectedFrame ? [selectedFrame].map((f) => (
          <figure key={f.frame_index} className="designation-frame">
            <div className="designation-frame__stage" style={{ aspectRatio: `${frames.data.display_width} / ${frames.data.display_height}` }}>
              <img src={`/api/v1/videos/${videoId}/frame-image?time_us=${f.time_us}`} alt={`${formatTime(f.time_us)} 장면`} />
              {f.boxes.map((b) => <button key={b.track_id} type="button" className={`person-picker__hit person-picker__hit--${choiceOf(b.track_id)}`} aria-label={`사진 속 번호 ${b.track_id} 선수를 ${activeRole === "user" ? "분석 선수" : "상대"}로 선택`} aria-pressed={choiceOf(b.track_id) === activeRole} disabled={insufficient || save.isPending} onClick={() => pickPerson(b.track_id)} style={{left:`${b.bbox[0]/frames.data.display_width*100}%`,top:`${b.bbox[1]/frames.data.display_height*100}%`,width:`${(b.bbox[2]-b.bbox[0])/frames.data.display_width*100}%`,height:`${(b.bbox[3]-b.bbox[1])/frames.data.display_height*100}%`}}><span>{choiceOf(b.track_id) === "user" ? "✓ 분석 선수" : choiceOf(b.track_id) === "opponent" ? "✓ 상대" : "이 선수 선택"}</span></button>)}
            </div>
            <figcaption className="muted">{formatTime(f.time_us)}</figcaption>
          </figure>
        )) : <p className="notice">선수를 선택할 사진이 없습니다. 검출·추적을 다시 실행하거나 아래의 추가 설정을 확인하세요.</p>}
      </div>
      {frames.data.frames.length > 1 ? <div className="person-picker__scenes"><span>가려져서 구분하기 어렵다면 다른 장면을 보세요.</span>{frames.data.frames.map((f,i) => <button key={f.frame_index} type="button" aria-pressed={frameIndex === i} onClick={()=>setFrameIndex(i)}>장면 {i+1} · {formatTime(f.time_us)}</button>)}</div> : null}
      {selectionError ? <p role="alert" className="notice notice--error">{selectionError}</p> : null}

      <details className="person-picker__advanced"><summary>추가 설정: 같은 선수가 여러 번호로 나뉜 경우</summary>
      <p className="muted">일반 선택은 위 사진에서 하면 됩니다. 영상 앞뒤에서 같은 선수가 다른 번호로 나뉜 것을 직접 확인한 경우에만 여러 번호를 연결하세요. 확실하지 않은 번호는 ‘지정 안 함’으로 두세요.</p>

      <table className="stage-table">
        <caption className="sr-only">트랙별 선택</caption>
        <thead>
          <tr>
            <th scope="col">트랙</th>
            <th scope="col">선택</th>
          </tr>
        </thead>
        <tbody>
          {set.tracks.map((t) => (
            <tr key={t.track_id}>
              <th scope="row">{t.track_id}</th>
              <td>
                <select
                  aria-label={`트랙 ${t.track_id}`}
                  value={choiceOf(t.track_id)}
                  disabled={insufficient}
                  onChange={(e) => {
                    setPicked({ ...picked, [t.track_id]: e.target.value as Choice });
                    save.reset();
                  }}
                >
                  <option value="none">지정 안 함</option>
                  <option value="user">분석 선수</option>
                  {!solo ? <option value="opponent">상대</option> : null}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p>
        <button type="button" onClick={swap} disabled={solo || insufficient || (user.length === 0 && opponent.length === 0)}>
          분석 선수와 상대 바꾸기
        </button>{" "}
      </p>
      {user.length === 0 || (!solo && opponent.length === 0) ? <p className="muted">분석 선수와 상대의 트랙을 하나 이상씩 골라야 확정할 수 있습니다.</p> : null}
      </details>
      {messageOf(save.error) ? (
        <p className="notice notice--error" role="alert">{messageOf(save.error)}</p>
      ) : null}
      {assigned ? (
        <p className="notice notice--muted" role="status">
          저장된 선택: 분석 선수 = 번호 {saved("USER").join(", ")} · 상대 = {saved("OPPONENT").length ? `번호 ${saved("OPPONENT").join(", ")}` : "없음 · 개인 훈련"}. 이전 선택은 이력으로 남습니다.
        </p>
      ) : null}
    </section>
  );
}
