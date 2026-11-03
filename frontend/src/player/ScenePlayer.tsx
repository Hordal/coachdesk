import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import type { FramesView, Participant, PoseView } from "../api/client";
import { formatTime } from "../lib/format";
import { firstFrameWithin, frameAt, presentedMatches, seekTimeFor, stepFrame, type Frame } from "./frames";
import { PoseOverlay } from "./PoseOverlay";
import { buildPoseIndex, poseAt } from "./poses";
import { usePresentedFrame } from "./usePresentedFrame";
import { useSeekVerification } from "./useSeekVerification";
import { useStillFrame } from "./useStillFrame";

interface Props {
  videoId: string;
  src: string;
  fromUs: number;
  toUs: number;
  width: number;
  height: number;
  isVfr: boolean;
  frames: FramesView["frames"] | null;
  pose: PoseView | undefined;
  participants: Participant[];
  onTimeChange: (timeUs: number | null) => void;
  onSourceError: () => void;
  /** 타임라인에서 항목을 눌렀을 때의 이동 요청. nonce가 바뀔 때마다 한 번 적용한다 */
  seekRequest?: { us: number; nonce: number } | null;
}

// 프레임 표에 맞춘 뒤 관측과 비교할 때의 허용 차이(반올림 오차 수준)와, 프레임 표가 없을 때의 허용 차이
const SNAPPED_TOLERANCE_US = 500;
const UNSNAPPED_TOLERANCE_US = 20_000;

function safeSeek(video: HTMLVideoElement, seconds: number) {
  try {
    video.currentTime = seconds;
  } catch {
    /* 메타데이터를 읽기 전에는 탐색할 수 없다. 읽은 뒤 다시 시도한다 */
  }
}

export function ScenePlayer(props: Props) {
  const { videoId, src, fromUs, toUs, width, height, isVfr, frames, pose, participants, onTimeChange, onSourceError, seekRequest } = props;
  const videoRef = useRef<HTMLVideoElement>(null);
  const handledNonce = useRef<number | null>(null);
  const [ready, setReady] = useState(false);
  const [stillAtUs, setStillAtUs] = useState<number | null>(null);
  const [pending, setPending] = useState<Frame | null>(null);
  const [skippedUs, setSkippedUs] = useState<number | null>(null);

  const { timeUs, supported } = usePresentedFrame(videoRef, src);
  const still = useStillFrame(videoId, stillAtUs);
  const stillActive = stillAtUs !== null;

  const poseIndex = useMemo(() => buildPoseIndex(pose?.samples ?? []), [pose]);
  const shownUs = stillActive ? (still.frame?.ptsUs ?? stillAtUs) : timeUs;
  // 프레임 표는 [표 시작, 구간 끝)만 담는다. 구간 끝 이후의 프레임을 마지막 프레임으로 잘못 표기하지 않는다.
  const outsideRange = shownUs !== null && shownUs >= toUs;
  const frame: Frame | undefined = frames && shownUs !== null && !outsideRange ? frameAt(frames, shownUs + SNAPPED_TOLERANCE_US) : undefined;
  const poseTimeUs = frame ? frame.pts_us : shownUs;
  const toleranceUs = frame ? SNAPPED_TOLERANCE_US : UNSNAPPED_TOLERANCE_US;
  const firstFrame = frames ? frameAt(frames, fromUs) : undefined;

  useEffect(() => {
    onTimeChange(shownUs);
  }, [shownUs, onTimeChange]);

  // 접근 주소가 바뀌면 새 메타데이터를 읽은 뒤 구간 처음으로 다시 이동한다.
  useEffect(() => {
    setReady(false);
  }, [src]);

  /** 프레임으로 이동하고, 그 프레임이 실제로 표시되는지 확인할 준비를 한다. */
  const seekToFrame = (target: Frame) => {
    const video = videoRef.current;
    if (!video || !frames) return;
    video.pause();
    setStillAtUs(null);
    setSkippedUs(null);
    setPending(presentedMatches(timeUs, target) ? null : target);
    safeSeek(video, seekTimeFor(frames, target));
  };

  /** snap="inside"이면 us 이후의 첫 프레임(구간 안)으로, 아니면 us에 보이는 프레임으로 이동한다. */
  const seekToUs = (us: number, snap: "visible" | "inside" = "visible") => {
    const video = videoRef.current;
    if (!video) return;
    const target = frames ? (snap === "inside" ? firstFrameWithin(frames, us, toUs) : frameAt(frames, us)) : undefined;
    if (target) {
      seekToFrame(target);
    } else {
      video.pause();
      setStillAtUs(null);
      setSkippedUs(null);
      safeSeek(video, us / 1_000_000);
    }
  };

  // 목표 프레임이 표시되지 않으면(브라우저가 건너뜀) 디코더가 식별한 정지 프레임으로 전환한다(05).
  const onVerified = useCallback((outcome: "shown" | "skipped", target: Frame) => {
    setPending(null);
    if (outcome === "skipped") {
      setSkippedUs(target.pts_us);
      setStillAtUs(target.pts_us);
    }
  }, []);
  useSeekVerification(timeUs, pending, onVerified);

  // 구간이 바뀌면 구간의 첫 프레임으로 이동한다.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !ready) return;
    seekToUs(fromUs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromUs, toUs, ready, frames]);

  // 타임라인에서 요청한 시각으로 이동한다. 같은 요청을 두 번 적용하지 않는다.
  useEffect(() => {
    if (!ready || !seekRequest || handledNonce.current === seekRequest.nonce) return;
    handledNonce.current = seekRequest.nonce;
    seekToUs(seekRequest.us, "inside");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seekRequest, ready, frames]);

  // 재생이 구간 끝에 닿으면 멈추고 구간의 마지막 프레임으로 되돌아간다.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || timeUs === null || timeUs < toUs || video.paused) return;
    video.pause();
    const last = frames ? [...frames].reverse().find((f) => f.pts_us < toUs) : undefined;
    if (last) seekToFrame(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeUs, toUs]);

  const step = (delta: 1 | -1) => {
    if (!frames || !frame) return;
    const target = stepFrame(frames, frame, delta);
    if (!target || target.pts_us >= toUs) return;
    if (firstFrame && target.index < firstFrame.index) return;
    seekToFrame(target);
  };

  const goStart = () => seekToUs(fromUs);

  const toggleStill = () => {
    if (stillActive) {
      setStillAtUs(null);
      setSkippedUs(null);
      return;
    }
    videoRef.current?.pause();
    setStillAtUs(shownUs ?? fromUs);
  };

  const nextFrame = frames && frame ? stepFrame(frames, frame, 1) : undefined;
  const canPrev = Boolean(frames && frame && firstFrame && frame.index > firstFrame.index);
  const canNext = Boolean(nextFrame && nextFrame.pts_us < toUs);

  const poseSummary = (() => {
    if (!pose) return "확인 중";
    if (!pose.available) return "관절 데이터가 없습니다";
    if (poseTimeUs === null) return "—";
    const parts = participants.map((p) => {
      const found = poseAt(poseIndex, p.participant_id, poseTimeUs, toleranceUs) !== undefined;
      return `${p.participant_id} ${found ? "있음" : "없음"}`;
    });
    return parts.every((t) => t.endsWith("없음")) ? "이 시각의 관측이 없습니다" : parts.join(" · ");
  })();

  const verification = stillActive ? (skippedUs !== null ? "브라우저가 표시하지 않아 디코더가 식별한 프레임으로 전환" : "디코더가 식별한 프레임") : supported ? "표시된 프레임에서 직접 확인" : "재생 위치로 대신함";

  return (
    <div className="scene-player">
      <div className="player" style={{ aspectRatio: `${width} / ${height}`, "--ar": width / height } as CSSProperties}>
        <video
          ref={videoRef}
          className="player__video"
          src={src}
          controls
          muted
          playsInline
          preload="metadata"
          onLoadedMetadata={() => setReady(true)}
          onError={onSourceError}
        />
        {stillActive && still.frame ? <img className="player__still" src={still.frame.url} alt="디코더가 식별한 정지 프레임" /> : null}
        {pose ? (
          <PoseOverlay pose={pose} index={poseIndex} participants={participants} timeUs={poseTimeUs} toleranceUs={toleranceUs} />
        ) : null}
      </div>

      <div className="player__controls" role="group" aria-label="재생 위치 이동">
        <button type="button" onClick={goStart}>
          구간 처음으로
        </button>
        <button type="button" onClick={() => step(-1)} disabled={!canPrev}>
          이전 프레임
        </button>
        <button type="button" onClick={() => step(1)} disabled={!canNext}>
          다음 프레임
        </button>
        <button type="button" onClick={toggleStill} aria-pressed={stillActive}>
          {stillActive ? "영상으로 돌아가기" : "정지 프레임 보기"}
        </button>
      </div>

      {!frames ? <p className="muted">프레임 단위 이동은 60초 이하 구간에서 사용할 수 있습니다.</p> : null}
      {skippedUs !== null ? (
        <p className="notice notice--hold" role="note">
          {formatTime(skippedUs)} 프레임은 이 브라우저가 표시하지 않아 디코더가 식별한 정지 프레임으로 보여줍니다. 영상 파일의 프레임 길이 정보 때문일 수 있습니다.
        </p>
      ) : null}
      {!supported ? (
        <p className="notice notice--hold" role="note">
          이 브라우저는 화면에 표시된 프레임의 시각을 확인할 수 없어 재생 위치로 대신합니다. 정확한 프레임을 확인하려면 정지 프레임 보기를 사용하세요.
        </p>
      ) : null}
      {stillActive && still.error ? (
        <p className="notice notice--error" role="alert">
          정지 프레임을 불러오지 못했습니다.
        </p>
      ) : null}

      <dl className="meta player__readout">
        <div>
          <dt>표시 중인 프레임</dt>
          <dd>
            {frame
              ? `#${frame.index} · ${formatTime(frame.pts_us)}`
              : shownUs !== null
                ? `${formatTime(shownUs)} (${outsideRange ? "표시 구간 밖" : "프레임 표 없음"})`
                : "—"}
          </dd>
        </div>
        <div>
          <dt>프레임 시각 확인</dt>
          <dd>{verification}</dd>
        </div>
        <div>
          <dt>영상 형식</dt>
          <dd>{isVfr ? "가변 프레임률. 프레임의 실제 표시 시각 사용" : "고정 프레임률"}</dd>
        </div>
        <div>
          <dt>관절 관측</dt>
          <dd>{poseSummary}</dd>
        </div>
      </dl>
    </div>
  );
}
