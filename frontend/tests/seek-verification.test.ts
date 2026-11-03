import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { presentedMatches, type Frame } from "../src/player/frames";
import { useSeekVerification } from "../src/player/useSeekVerification";

const target: Frame = { index: 190, pts_us: 19_000_000 };

describe("presentedMatches", () => {
  it("표시된 프레임 시각이 목표와 같으면(반올림 오차 허용) 일치한다", () => {
    expect(presentedMatches(19_000_000, target)).toBe(true);
    expect(presentedMatches(19_000_400, target)).toBe(true);
    expect(presentedMatches(18_933_333, target)).toBe(false); // 직전 프레임
    expect(presentedMatches(null, target)).toBe(false);
  });
});

describe("useSeekVerification", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("목표 프레임이 표시되면 shown으로 알린다", () => {
    const onResolved = vi.fn();
    const { rerender } = renderHook(({ shown }) => useSeekVerification(shown, target, onResolved, 500), { initialProps: { shown: 18_933_333 as number | null } });
    expect(onResolved).not.toHaveBeenCalled();
    rerender({ shown: 19_000_000 });
    expect(onResolved).toHaveBeenCalledWith("shown", target);
    act(() => vi.advanceTimersByTime(2000));
    expect(onResolved).not.toHaveBeenCalledWith("skipped", target);
  });

  it("제한 시간 안에 표시되지 않으면 skipped로 알린다(브라우저가 프레임을 건너뜀)", () => {
    const onResolved = vi.fn();
    renderHook(() => useSeekVerification(18_933_333, target, onResolved, 500));
    act(() => vi.advanceTimersByTime(499));
    expect(onResolved).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(2));
    expect(onResolved).toHaveBeenCalledWith("skipped", target);
  });

  it("직전 프레임이 먼저 보고된 뒤 목표 프레임이 오면 건너뜀으로 처리하지 않는다", () => {
    const onResolved = vi.fn();
    const { rerender } = renderHook(({ shown }) => useSeekVerification(shown, target, onResolved, 500), { initialProps: { shown: null as number | null } });
    act(() => vi.advanceTimersByTime(300));
    rerender({ shown: 18_933_333 }); // 이전 프레임 보고: 기다림을 다시 시작한다
    act(() => vi.advanceTimersByTime(300));
    rerender({ shown: 19_000_000 }); // 목표 프레임 도착
    act(() => vi.advanceTimersByTime(2000));
    expect(onResolved).toHaveBeenCalledTimes(1);
    expect(onResolved).toHaveBeenCalledWith("shown", target);
  });

  it("목표가 없으면 아무것도 하지 않는다", () => {
    const onResolved = vi.fn();
    renderHook(() => useSeekVerification(19_000_000, null, onResolved, 500));
    act(() => vi.advanceTimersByTime(5000));
    expect(onResolved).not.toHaveBeenCalled();
  });
});
