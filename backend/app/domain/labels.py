"""화면·문장에 쓰는 한국어 표기. 분류 항목 이름은 ‘분석 항목 및 판정 구조’를 따른다."""

COUNTING_UNIT = {
    "kick_attempt": "발차기",
    "cross_attempt": "크로스",
    "punch_attempt": "펀치",
}

QUESTION = {
    "counter_followed": "후속 반격",
    "counter_contact": "반격에서의 접촉",
    "contact_occurred": "접촉",
    "rear_guard_return_delayed": "공격 후 뒷손 가드 복귀 지연",
    "balance_recovery_delayed": "공격 후 밸런스 회복 지연",
}

CONTACT_REGION = {
    "head": "머리",
    "body": "몸통",
    "leg": "다리",
    "guard": "가드",
    "unclear": "부위 불명확",
}

# 판단 불가·제외 사유 코드 → 문장 앞에 붙이는 말
REASON_PHRASE = {
    "occluded": "가림 때문에 ",
    "missing_input": "필요한 입력이 없어 ",
}
