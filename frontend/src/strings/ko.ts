/** 화면 문구와 코드 → 한국어 표기. 사전에 없는 코드는 코드 그대로 보여준다. */

type Dict = Readonly<Record<string, string>>;

export function label(dict: Dict, key: string | null | undefined): string {
  if (key == null) return "";
  return dict[key] ?? key;
}

export const EVENT_TYPE: Dict = {
  KICK_ATTEMPT: "발차기 시도",
  PUNCH_ATTEMPT: "펀치 시도",
  PUNCH_CANDIDATE: "펀치 후보",
  CROSS: "크로스",
  DIRECT_CONTACT: "접촉",
  TAKEDOWN: "테이크다운",
  GROUND_RELATION: "그라운드 관계",
  TOP_CONTROL: "탑 컨트롤",
  SUBMISSION_FINISH: "서브미션 종료",
};

export const EVENT_STATUS: Dict = {
  CANDIDATE: "후보",
  VALID: "확인됨",
  UNKNOWN: "판단 불가",
};

export const TAKEDOWN_OUTCOME: Dict = {
  SUCCESS: "성공",
  DEFENDED: "방어됨",
  UNKNOWN: "판단 불가",
};

export const GROUND_RELATION: Dict = {
  CLEAR_TOP_BOTTOM: "상하 관계 명확",
  NEUTRAL_ENTANGLED: "얽힘(우위 없음)",
  SCRAMBLE_TRANSITION: "전환 중",
  UNKNOWN: "상하 관계 판단 불가",
};

export const FINISH_CONTEXT: Dict = {
  MATCH_WIN: "경기 종료(탭·심판 중단)",
  SPARRING_TAP: "스파링 탭",
  UNKNOWN: "종료 맥락 판단 불가",
};

export const COUNTING_UNIT: Dict = {
  kick_attempt: "발차기",
  cross_attempt: "크로스",
  punch_attempt: "펀치",
};

export const QUESTION: Dict = {
  counter_followed: "후속 반격 여부",
  counter_contact: "반격에서의 접촉 여부",
  contact_occurred: "접촉 여부",
  rear_guard_return_delayed: "공격 후 뒷손 가드 복귀 지연",
  counter_relation: "반격 관계",
  contact_attribution: "접촉의 귀속",
  distance_condition: "거리 조건",
};

export const CONTACT_REGION: Dict = {
  head: "머리",
  body: "몸통",
  leg: "다리",
  guard: "가드",
  unclear: "부위 불명확",
};

export const ASSESSMENT_VALUE: Dict = {
  present: "있음",
  absent: "없음",
  unknown: "판단 불가",
  not_applicable: "해당 없음",
};

/** 판단 불가·해당 없음·미산출의 사유 */
export const REASON: Dict = {
  occluded: "가림",
  missing_input: "필요한 입력 없음",
  not_annotated_in_fixture: "가상 예제에서 판정하지 않음",
  no_counter: "반격이 없어 해당 없음",
  evidence_unavailable: "원본 영상이 연결되어 있지 않음",
  evidence_expired: "보관 기간이 지나 원본 영상이 삭제됨",
  evidence_deleted: "사용자 삭제 요청으로 원본 영상이 삭제됨",
  signing_not_implemented: "영상 접근 주소 발급이 아직 구현되지 않음",
  stage_not_implemented: "아직 구현되지 않은 단계",
};

export const MEDIA_STATE: Dict = {
  present: "원본 보관 중",
  expired: "보관 기간이 지나 원본 삭제됨",
  deleted: "원본 삭제됨",
};

export const REPORT_CATEGORY: Dict = {
  position: "위치",
  technique: "기술",
  value: "수치",
  evidence_mismatch: "근거 불일치",
  other: "기타",
};

export const CORRECTION_ERROR: Dict = {
  no_change: "이미 같은 선수가 본인으로 지정되어 있습니다.",
  stale_run: "이 결과는 이미 정정되어 최신이 아닙니다. 최신 결과에서 다시 정정해 주세요.",
  nothing_to_designate: "선수가 두 명인 결과에서만 본인을 지정할 수 있습니다.",
  unknown_participant: "이 결과에 없는 선수입니다.",
  invalid_range: "구간은 시작과 끝을 함께 입력하고, 시작이 끝보다 앞서야 하며 영상 길이를 넘을 수 없습니다.",
};

export const DESIGNATION_ERROR: Dict = {
  selection_incomplete: "나와 상대의 트랙을 모두 골라야 합니다.",
  unknown_track: "가져온 결과에 없는 트랙 번호입니다.",
  track_in_both: "한 트랙을 나와 상대에 모두 지정할 수 없습니다.",
  inconsistent_selection: "고른 트랙이 서로 맞지 않습니다. 한 선수의 트랙은 같은 시각에 동시에 보이면 안 됩니다.",
  stale_observations: "그 사이 검출·추적 결과가 바뀌었습니다. 화면을 새로 고쳐 다시 선택해 주세요.",
  insufficient_candidates: "후보 트랙이 두 개 미만이라 선수를 지정할 수 없습니다.",
};

export const AUTH_ERROR: Dict = {
  invalid_credentials: "이메일 또는 비밀번호가 올바르지 않습니다.",
  too_many_attempts: "로그인 시도가 너무 많습니다. 잠시 뒤 다시 시도해 주세요.",
  account_blocked: "사용할 수 없는 계정입니다. 관리자에게 문의해 주세요.",
  weak_password: "비밀번호는 10자 이상 128자 이하여야 합니다.",
  consent_required: "이용 안내와 영상 이용에 동의해야 계정을 만들 수 있습니다.",
  email_taken: "이미 계정이 있는 이메일입니다.",
  invite_invalid: "유효하지 않거나 만료된 초대입니다.",
};

export const ROLE_NAME: Dict = { participant: "참여자", admin: "관리자" };

export const INVITE_STATE: Dict = { pending: "대기 중", accepted: "수락됨", revoked: "폐기됨", expired: "만료됨" };

export const VIDEO_STATUS: Dict = {
  processing: "처리 중",
  ready: "준비됨",
  failed: "실패",
  cancelled: "취소됨",
};

export const STAGE_STATUS: Dict = {
  pending: "대기",
  running: "진행 중",
  completed: "완료",
  failed: "실패",
  skipped: "건너뜀",
  unsupported: "미지원",
  cancelled: "취소됨",
};

/** 업로드와 영상 준비에서 서버가 돌려주는 오류 코드 */
export const UPLOAD_ERROR: Dict = {
  unsupported_extension: "mp4 또는 mov 파일만 올릴 수 있습니다.",
  too_large: "파일이 너무 큽니다.",
  insufficient_storage: "서버 저장 공간이 부족합니다.",
  invalid_media: "영상 파일로 읽을 수 없습니다.",
  unsupported_codec: "지원하지 않는 영상 형식입니다. H.264 또는 H.265 영상을 올려 주세요.",
  too_long: "영상이 허용 길이보다 깁니다.",
  resolution_too_high: "해상도가 허용 범위보다 큽니다.",
  checksum_mismatch: "전송 중 데이터가 손상되었습니다. 다시 시도해 주세요.",
};

/** 주장을 제공하지 않은 사유 */
export const HOLD_REASON: Dict = {
  insufficient_behavior_evidence_and_unvalidated_rule:
    "원인을 설명할 행동 근거가 부족하고 검토된 교정 규칙이 없어 제공하지 않았습니다.",
  distance_unavailable: "실제 거리 측정값이 없어 거리와 관련된 내용은 제공하지 않았습니다.",
  repeated_problem_rule_unvalidated: "반복 문제 기준이 검토되지 않아 제공하지 않았습니다.",
  correction_rule_unvalidated: "교정 규칙이 검토되지 않아 제공하지 않았습니다.",
};

export const CLAIM_TYPE: Dict = {
  observation: "관찰",
  aggregate: "집계",
  causal_hypothesis: "원인 가설",
  correction: "교정 방향",
};

export const RELATION: Dict = {
  counter_to: "반격",
  contact_of: "접촉 사건",
  followed_by: "후속 공격",
};

export const STATE_CATEGORY: Dict = {
  guard_hand: "가드",
  distance: "거리",
  stance: "스탠스",
  balance: "밸런스",
};

export const STATE_LABEL: Dict = {
  high_guard: "하이가드",
  low_guard: "로우가드",
  neutral: "중립",
};

export const BODY_PART: Dict = {
  rear_hand: "뒷손",
  lead_hand: "앞손",
};

export const DATASET_KIND: Dict = {
  synthetic: "가상 예제",
  manual: "수동 입력",
  model: "모델 분석",
  unanalyzed: "분석 전",
};

export const SPORT: Dict = {
  boxing: "복싱",
  kickboxing: "킥복싱",
  muay_thai: "무에타이",
  mma: "MMA",
};

export const RUN_STATUS: Dict = {
  queued: "대기",
  running: "처리 중",
  completed: "완료",
  partial: "부분 완료",
  failed: "실패",
  cancelled: "취소",
};

export const ROLE: Dict = {
  USER: "사용자",
  OPPONENT: "상대",
  UNRESOLVED: "미지정",
};

export const AVAILABILITY: Dict = {
  available: "재생 가능",
  expired: "기간 만료",
  deleted: "삭제됨",
  unavailable: "영상 없음",
};

export const T = {
  appName: "격투기 영상 복기",
  skipToContent: "본문으로 건너뛰기",
  loading: "불러오는 중",
  retry: "다시 시도",
  missing: "확인 불가",
  noRatio: "비율 없음",
  runsTitle: "분석 실행",
  runsEmpty: "표시할 분석 실행이 없습니다.",
  unanalyzedNotice: "영상만 준비된 상태입니다. 분석은 아직 구현되지 않아 사건과 주장은 없습니다.",
  manualNotice: "사람이 입력한 사건·판정을 가져온 결과입니다. 집계와 문장은 입력한 판정에서 다시 계산했으며, 전문가 검수를 거치지 않았습니다.",
  consentLabel: "이용 안내(영상 보관 기간과 삭제 방법 포함)를 확인했으며 영상 이용에 동의합니다.",
  syntheticNotice: "가상 예제입니다. 실제 영상을 분석한 결과가 아닙니다.",
  tabs: { summary: "요약", scene: "장면 복기", corrections: "정정·기록" },
  sections: {
    observation: "관찰·집계",
    cause: "원인 가설",
    correction: "교정 방향",
    evidence: "근거 장면",
  },
  noCause: "원인 가설을 제공하지 않았습니다.",
  noCorrection:
    "교정 방향을 제공하지 않았습니다. 이는 문제가 없다는 뜻이 아니라 제공할 근거가 부족하다는 뜻입니다.",
  withheld: "제공하지 않음",
  evidenceNoVideo: "원본 영상이 없어 재생할 수 없습니다. 구간과 선수 정보는 확인할 수 있습니다.",
  gapNotice: "빈 구간은 해당 상태나 사건이 없다는 뜻이 아니라 기록된 내용이 없다는 뜻입니다.",
  countLabels: {
    candidates: "확인된 전체",
    denominator: "판정 가능",
    numerator: "해당 결과 발생",
    unknown: "판단 불가",
    excluded: "제외",
  },
} as const;
