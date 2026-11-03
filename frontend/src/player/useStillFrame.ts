import { useEffect, useState } from "react";

export interface StillFrame {
  url: string;
  index: number;
  ptsUs: number;
}

/**
 * 디코더가 식별한 정확한 프레임 이미지(정지 프레임)를 서버에서 받는다(05).
 * 브라우저 탐색이 목표 시각의 1프레임 이내로 맞는지 확인할 수 없을 때 근거 확인에 쓴다.
 */
export function useStillFrame(videoId: string, timeUs: number | null) {
  const [frame, setFrame] = useState<StillFrame | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (timeUs === null) {
      setFrame(null);
      setError(false);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setError(false);

    (async () => {
      try {
        const response = await fetch(`/api/v1/videos/${videoId}/frame-image?time_us=${timeUs}`);
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setFrame({
          url: objectUrl,
          index: Number(response.headers.get("X-Frame-Index")),
          ptsUs: Number(response.headers.get("X-Frame-Pts-Us")),
        });
      } catch {
        if (!cancelled) {
          setFrame(null);
          setError(true);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [videoId, timeUs]);

  return { frame, error };
}
