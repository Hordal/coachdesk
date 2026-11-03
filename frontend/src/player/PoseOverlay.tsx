import type { Participant, PoseView } from "../api/client";
import { participantName } from "../components/TimelineTrack";
import { poseAt, type PoseIndex } from "./poses";

interface Props {
  pose: PoseView;
  index: PoseIndex;
  participants: Participant[];
  /** 지금 화면에 표시된 프레임의 시각(마이크로초) */
  timeUs: number | null;
  /** 이 시각과 관측 시각의 허용 차이. 오래된 관측을 현재 것처럼 그리지 않는다 */
  toleranceUs: number;
}

/**
 * 관절 오버레이. 영상과 같은 화면 좌표계(viewBox)를 쓴다.
 * 값이 없는 관절(x 또는 y가 null)은 그리지 않고 0으로 채우지 않는다.
 * 선수 구별은 색뿐 아니라 선 모양(실선·점선)과 문자 표시를 함께 쓴다.
 */
export function PoseOverlay({ pose, index, participants, timeUs, toleranceUs }: Props) {
  if (!pose.available || pose.width == null || pose.height == null || timeUs === null) return null;

  return (
    <svg
      className="pose-overlay"
      viewBox={`0 0 ${pose.width} ${pose.height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="관절 오버레이"
    >
      {participants.map((p, order) => {
        const sample = poseAt(index, p.participant_id, timeUs, toleranceUs);
        if (!sample) return null;
        const byName = new Map(sample.joints.map((j) => [j.name, j]));
        const drawable = sample.joints.filter((j) => j.x != null && j.y != null);
        const first = drawable[0];
        const lineClass = order === 0 ? "pose-line pose-line--solid" : "pose-line pose-line--dashed";
        return (
          <g key={p.participant_id} data-participant={p.participant_id} className={`pose pose--${order === 0 ? "a" : "b"}`}>
            {pose.connections.map(([from, to]) => {
              const a = byName.get(from);
              const b = byName.get(to);
              if (!a || !b || a.x == null || a.y == null || b.x == null || b.y == null) return null;
              return <line key={`${from}-${to}`} className={lineClass} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
            })}
            {drawable.map((j) => (
              <circle
                key={j.name}
                className="pose-joint"
                cx={j.x!}
                cy={j.y!}
                r={3}
              />
            ))}
            {first ? (
              <text className="pose-label" x={first.x! + 4} y={first.y! - 6}>
                {participantName(p.participant_id, participants)}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
