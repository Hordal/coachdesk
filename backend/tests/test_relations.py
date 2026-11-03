"""관계 기반 판정(02B R1·R3·R4·R6)."""

from __future__ import annotations

from app.domain.relations import judge_balance_recovery, judge_range, link_counters, link_defenses

S = 1_000_000


def ev(eid, label, pid, target, start, end, **kw):
    return {"event_id": eid, "event_label": label, "status": "VALID", "verified": True, "participant_id": pid, "target_participant_id": target,
            "start_timestamp_us": start, "end_timestamp_us": end, **kw}


OBS = {"B": [(0, 100 * S)], "A": [(0, 100 * S)]}


def test_defense_link_none_is_only_given_with_observation():
    a = ev("a1", "JAB", "A", "B", 10 * S, 10 * S + 400_000)
    assert link_defenses([a], observed=OBS)[0].value == "none"
    unk = link_defenses([a])[0]
    assert unk.value == "unknown" and unk.reason_code == "unobserved"  # 관측 근거가 없으면 '없음'이 아니다
    assert link_defenses([a], observed={"B": [(0, 10 * S)]})[0].value == "unknown"  # 창을 덮지 못함


def test_defense_link_picks_largest_overlap_and_keeps_auxiliary():
    a = ev("a1", "CROSS", "A", "B", 10 * S, 10 * S + 400_000)
    d1 = ev("d1", "BLOCK", "B", "A", 10 * S + 300_000, 10 * S + 500_000)
    d2 = ev("d2", "PARRY", "B", "A", 10 * S + 50_000, 10 * S + 350_000)
    d3 = ev("d3", "BLOCK", "A", "B", 10 * S, 10 * S + 400_000)  # 공격 선수 자신의 동작은 제외
    link = link_defenses([a, d1, d2, d3], observed=OBS)[0]
    assert (link.value, link.defense_id, link.auxiliary_ids) == ("linked", "d2", ("d1",))


def test_defense_link_respects_delta_link_and_unknown_attack():
    a = ev("a1", "JAB", "A", "B", 10 * S, 10 * S + 400_000)
    far = ev("d1", "BLOCK", "B", "A", 10 * S + 700_000, 10 * S + 900_000)
    assert link_defenses([a, far], observed=OBS)[0].value == "none"
    bad = ev("a2", "JAB", "A", "B", 20 * S, 20 * S + 400_000, status="UNKNOWN")
    assert link_defenses([bad], observed=OBS)[0].reason_code == "attack_unknown"


def test_counter_vs_exchange():
    a1 = ev("a1", "CROSS", "A", "B", 10 * S, 10 * S + 500_000, phase_marks={"max_extension_us": 10 * S + 200_000})
    d = ev("d1", "BLOCK", "B", "A", 10 * S + 100_000, 10 * S + 400_000)
    reply = ev("a2", "HOOK", "B", "A", 10 * S + 700_000, 10 * S + 1_100_000)
    early = ev("a3", "JAB", "B", "A", 10 * S + 150_000, 10 * S + 450_000)  # a1 최대 동작 전 시작 → 교환
    late = ev("a4", "JAB", "B", "A", 12 * S, 12 * S + 300_000)  # δ_counter 밖
    evs = [a1, d, reply, early, late]
    got = {(c.reply_id, c.kind) for c in link_counters(evs, link_defenses(evs, observed=OBS))}
    assert got == {("a2", "COUNTER"), ("a3", "EXCHANGE")}


def test_no_counter_without_defense_link():
    a1 = ev("a1", "CROSS", "A", "B", 10 * S, 10 * S + 500_000)
    a2 = ev("a2", "HOOK", "B", "A", 10 * S + 700_000, 10 * S + 1_100_000)
    assert link_counters([a1, a2], link_defenses([a1, a2])) == []


def test_range_result_and_missing_samples():
    a = ev("a1", "JAB", "A", "B", 10 * S, 10 * S + 400_000)
    near = [(10 * S - 200_000, 0.60), (10 * S + 700_000, 0.40)]
    r = judge_range(a, near)
    assert r.value == "entered" and round(r.delta, 2) == -0.20
    assert judge_range(a, [(10 * S - 200_000, 0.6), (10 * S + 700_000, 0.61)]).value == "maintained"
    assert judge_range(a, [(10 * S - 200_000, 0.4), (10 * S + 700_000, 0.7)]).value == "widened"
    far = judge_range(a, [(0, 0.5), (30 * S, 0.5)])
    assert far.value == "unknown" and far.reason_code == "missing_input"


def test_balance_recovery_reuses_recovery_structure():
    a = {"participant_id": "A", "end_timestamp_us": 10 * S}
    st = lambda i, label, s, e: {"state_id": i, "participant_id": "A", "state_category": "balance", "label": label, "start_us": s, "end_us": e}
    late = [st("b1", "unstable", 10 * S, 10 * S + 800_000), st("b2", "stable", 10 * S + 800_000, 12 * S)]
    r = judge_balance_recovery(a, late)
    assert r.value == "present" and r.delay_us == 800_000
    quick = [st("b1", "unstable", 10 * S, 10 * S + 100_000), st("b2", "stable", 10 * S + 100_000, 12 * S)]
    assert judge_balance_recovery(a, quick).value == "absent"
    assert judge_balance_recovery(a, []).value == "unknown"
