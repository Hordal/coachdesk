import { ASSESSMENT_VALUE, AVAILABILITY, label, T } from "../strings/ko";

type Tone = "present" | "absent" | "unknown" | "na" | "ok" | "hold" | "muted";

interface BadgeProps {
  tone: Tone;
  icon: string;
  text: string;
}

/** 색만으로 구별하지 않도록 아이콘과 텍스트를 함께 쓴다(19). */
export function StatusBadge({ tone, icon, text }: BadgeProps) {
  return (
    <span className={`badge badge--${tone}`}>
      <span aria-hidden="true" className="badge__icon">
        {icon}
      </span>
      {text}
    </span>
  );
}

const ASSESSMENT_STYLE: Record<string, { tone: Tone; icon: string }> = {
  present: { tone: "present", icon: "●" },
  absent: { tone: "absent", icon: "○" },
  unknown: { tone: "unknown", icon: "?" },
  not_applicable: { tone: "na", icon: "–" },
};

export function AssessmentBadge({ value }: { value: string }) {
  const style = ASSESSMENT_STYLE[value] ?? { tone: "muted" as const, icon: "·" };
  return <StatusBadge {...style} text={label(ASSESSMENT_VALUE, value)} />;
}

export function FindingStatusBadge({ status }: { status: string }) {
  return status === "valid" ? (
    <StatusBadge tone="ok" icon="✓" text="제공" />
  ) : (
    <StatusBadge tone="hold" icon="!" text={T.withheld} />
  );
}

export function AvailabilityBadge({ availability }: { availability: string }) {
  const playable = availability === "available";
  return (
    <StatusBadge
      tone={playable ? "ok" : "muted"}
      icon={playable ? "▶" : "■"}
      text={label(AVAILABILITY, availability)}
    />
  );
}

export function SyntheticBadge() {
  return <StatusBadge tone="hold" icon="◇" text="가상 예제" />;
}
