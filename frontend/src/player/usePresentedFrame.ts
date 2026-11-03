import { useEffect, useState, type RefObject } from "react";

interface FrameCallbackMetadata {
  mediaTime: number;
}

type VideoWithFrameCallback = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: (now: number, metadata: FrameCallbackMetadata) => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

export function supportsFrameCallback(video: HTMLVideoElement | null): boolean {
  return video !== null && typeof (video as VideoWithFrameCallback).requestVideoFrameCallback === "function";
}

/**
 * 화면에 실제로 표시된 프레임의 시각(마이크로초)을 알려준다.
 * requestVideoFrameCallback의 mediaTime을 쓰며, 지원하지 않는 브라우저에서는 재생 위치(currentTime)로
 * 대신하되 정확한 프레임 시각은 아니므로 supported=false로 알린다(05).
 */
export function usePresentedFrame(videoRef: RefObject<HTMLVideoElement | null>, resetKey: unknown) {
  const [timeUs, setTimeUs] = useState<number | null>(null);
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    const video = videoRef.current as VideoWithFrameCallback | null;
    if (!video) return;
    setTimeUs(null);

    if (supportsFrameCallback(video)) {
      setSupported(true);
      let handle = 0;
      const onFrame = (_now: number, metadata: FrameCallbackMetadata) => {
        setTimeUs(Math.round(metadata.mediaTime * 1_000_000));
        handle = video.requestVideoFrameCallback!(onFrame);
      };
      handle = video.requestVideoFrameCallback!(onFrame);
      return () => video.cancelVideoFrameCallback?.(handle);
    }

    setSupported(false);
    const update = () => setTimeUs(Math.round(video.currentTime * 1_000_000));
    video.addEventListener("timeupdate", update);
    video.addEventListener("seeked", update);
    video.addEventListener("loadeddata", update);
    return () => {
      video.removeEventListener("timeupdate", update);
      video.removeEventListener("seeked", update);
      video.removeEventListener("loadeddata", update);
    };
  }, [videoRef, resetKey]);

  return { timeUs, supported };
}
