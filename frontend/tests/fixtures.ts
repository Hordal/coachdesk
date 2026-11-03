/** 화면 시험용 데이터. 백엔드 fixture 응답과 같은 형태이며 타입은 생성된 API 타입을 따른다. */
import type {
  AggregateCounts,
  Event,
  EvidenceView,
  FindingView,
  Participant,
  RunSummary,
  StateInterval,
} from "../src/api/client";

export const participants: Participant[] = [
  { participant_id: "A", role: "USER", track_ids: [], user_designated: true, body_profile: null },
  { participant_id: "B", role: "OPPONENT", track_ids: [], user_designated: false, body_profile: null },
];

export const run: RunSummary = {
  run_id: "fixture-counter",
  video_id: "demo-video-01",
  filename: null,
  mode: "fixture",
  status: "completed",
  dataset_kind: "synthetic",
  expert_validated: false,
  definition_version: "demo-unvalidated",
  sport: "kickboxing",
  duration_us: 540_000_000,
  participants,
  counts: { events: 10, assessments: 28, aggregates: 2, findings: 3 },
  parent_run_id: null,
  superseded_by: null,
};

export function evidence(id: string, startUs: number, endUs: number, availability: "available" | "unavailable" = "unavailable"): EvidenceView {
  return {
    evidence: { evidence_id: id, video_id: "demo-video-01", start_us: startUs, end_us: endUs, participant_refs: ["A", "B"], availability },
    playable: availability === "available",
    access_url: null,
    reason_code: availability === "available" ? null : "evidence_unavailable",
  };
}

export const evidenceList: EvidenceView[] = [evidence("EV01", 9_000_000, 14_000_000), evidence("EV02", 39_000_000, 44_000_000)];

export const playableEvidenceList: EvidenceView[] = evidenceList.map((v) => ({
  ...v,
  evidence: { ...v.evidence, availability: "available" as const },
  playable: true,
  access_url: "/api/v1/media/demo-video-01?exp=9999999999&sig=test",
  reason_code: null,
}));

export const validFinding: FindingView = {
  finding_id: "F01",
  claim_type: "aggregate",
  status: "valid",
  hold_reason: null,
  template_id: "kick_counter_summary",
  text: "확인된 발차기 5회 중 후속 상황을 판정할 수 있었던 것은 4회입니다.",
  slots: { observed: 5 },
  input_refs: ["AG01", "AG02"],
  evidence_refs: ["EV01", "EV02"],
  rule_ref: null,
};

export const withheldCorrection: FindingView = {
  finding_id: "F02",
  claim_type: "correction",
  status: "withheld",
  hold_reason: "insufficient_behavior_evidence_and_unvalidated_rule",
  template_id: null,
  text: null,
  slots: null,
  input_refs: ["AG01", "AG02"],
  evidence_refs: [],
  rule_ref: null,
};

export const withheldDistance: FindingView = {
  finding_id: "F03",
  claim_type: "aggregate",
  status: "withheld",
  hold_reason: "distance_unavailable",
  template_id: null,
  text: null,
  slots: null,
  input_refs: ["AS24", "AS25"],
  evidence_refs: [],
  rule_ref: null,
};

export const counts = (c: Partial<AggregateCounts> = {}): AggregateCounts => ({
  candidates: 5,
  denominator: 4,
  numerator: 3,
  unknown: 1,
  excluded: 0,
  ...c,
});

export const kick: Event = {
  schema_version: "phase1b_v1",
  event_id: "E01",
  event_label: "KICK_ATTEMPT",
  type: null,
  status: "VALID",
  verified: true,
  outcome: null,
  participant_id: "A",
  target_participant_id: "B",
  role: "USER",
  track_id: null,
  arm: null,
  synthetic: true,
  start_timestamp_us: 10_000_000,
  end_timestamp_us: 10_700_000,
  start_frame_index: null,
  end_frame_index: null,
  evidence: [],
  limitations: [],
  reason_codes: [],
  clip_id: null,
  mode: null,
  tracklet_ref: null,
  rule_version: null,
  contamination_gates: [],
  evidence_path: null,
  input_joint_quality: null,
  evidence_ref: null,
  confidence: null,
  source_surface: null,
  target_surface: null,
  phase_marks: null,
  pre_state_refs: [],
  post_state_refs: [],
  relation: null,
  top_participant_id: null,
  bottom_participant_id: null,
  finish_context: null,
};

export const takedown: Event = {
  ...kick,
  event_id: "G01",
  event_label: "TAKEDOWN",
  outcome: "DEFENDED",
};

export const contact: Event = {
  ...kick,
  event_id: "E03",
  event_label: "DIRECT_CONTACT",
  participant_id: "B",
  target_participant_id: "A",
  role: "OPPONENT",
  start_timestamp_us: 11_020_000,
  end_timestamp_us: 11_060_000,
};

export const guardState: StateInterval = {
  state_id: "S01",
  participant_id: "A",
  body_part: "rear_hand",
  state_category: "guard_hand",
  label: "high_guard",
  start_us: 9_000_000,
  end_us: 10_000_000,
};
