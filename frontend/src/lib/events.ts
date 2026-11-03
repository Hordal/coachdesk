import type { Event } from "../api/client";

/** 화면 묶음용 분류. 사건 형식은 팀 동작 검출 모델 형식이라 분류 필드가 없어 라벨에서 정한다(서버 domain/events.py와 같은 규칙). */
export type EventCategory = "action" | "contact" | "ground";

const GROUND_LABELS = new Set(["TAKEDOWN", "GROUND_RELATION", "TOP_CONTROL", "SUBMISSION_FINISH"]);

export function eventCategory(event: Pick<Event, "event_label">): EventCategory {
  if (GROUND_LABELS.has(event.event_label)) return "ground";
  if (event.event_label.toUpperCase().includes("CONTACT")) return "contact";
  return "action";
}
