"""5단계: 후보 사건 정책과 `candidate:N` 역할 제안."""

from __future__ import annotations

from app.domain.analysis import derive_guard_return
from app.domain.candidates import suggest_candidate_roles
from app.domain.models import TrackAssignment, TrackAssignmentSet
from app.domain.policy import CANDIDATE, CONFIRMED, UNKNOWN, event_state, held_note, split_events
from app.domain.relations import link_counters, link_defenses
from app.domain.scenes import select_scenes

from .test_team_events import TEAM_LINE, env  # noqa: F401

S = 1_000_000


def ev(eid, label="CROSS", pid="A", target="B", start=10 * S, **kw):
    return {"event_id": eid, "event_label": label, "participant_id": pid, "target_participant_id": target,
            "start_timestamp_us": start, "end_timestamp_us": start + 500_000, **kw}


def test_event_state_rules():
    assert event_state({"status": "VALID", "verified": True}) == CONFIRMED
    assert event_state({"status": "VALID"}) == CONFIRMED
    assert event_state({"status": "VALID", "verified": False}) == CANDIDATE
    assert event_state(TEAM_LINE) == CANDIDATE
    assert event_state({"status": "UNKNOWN"}) == UNKNOWN
    assert event_state({}) == CANDIDATE  # 상태가 없으면 확인된 것으로 올리지 않는다
    parts = split_events([TEAM_LINE, {"status": "VALID", "verified": True}, {"status": "UNKNOWN"}])
    assert [len(parts[k]) for k in (CONFIRMED, CANDIDATE, UNKNOWN)] == [1, 1, 1]
    assert held_note([{"status": "VALID", "verified": True}]) is None
    assert held_note([TEAM_LINE, {"status": "UNKNOWN"}]) == "후보 사건 1건, 상태 불명 사건 1건은 확인되지 않아 집계에서 제외함"


def test_candidates_are_not_counted_in_aggregates():
    ok = ev("e1", status="VALID", verified=True)
    cand = ev("e2", status="CANDIDATE", verified=False, start=20 * S)
    out = derive_guard_return([ok, cand], [], video_id="v", duration_us=60 * S)
    assert [a["subject_ref"] for a in out["assessments"]] == ["e1"]
    assert out["aggregates"][0]["candidate_refs"] == ["e1"]  # 분모에 후보를 넣지 않는다


def test_only_candidates_give_nothing_to_aggregate():
    out = derive_guard_return([ev("e1", status="CANDIDATE", verified=False)], [], video_id="v", duration_us=60 * S)
    assert out == {"assessments": [], "evidence": [], "aggregates": [], "findings": []}


def test_candidate_attack_or_defense_is_unknown_not_none():
    obs = {"A": [(0, 100 * S)], "B": [(0, 100 * S)]}
    cand_attack = ev("a1", status="CANDIDATE", verified=False)
    assert link_defenses([cand_attack], observed=obs)[0].reason_code == "candidate_event"
    attack = ev("a2", status="VALID", verified=True)
    cand_def = ev("d1", "BLOCK", "B", "A", start=10 * S, status="CANDIDATE", verified=False)
    link = link_defenses([attack, cand_def], observed=obs)[0]
    assert (link.value, link.reason_code) == ("unknown", "defense_candidate_unconfirmed")  # 방어 후보가 있으면 '없음'이라 하지 않는다
    reply = ev("a3", "HOOK", "B", "A", start=10 * S + 700_000, status="CANDIDATE", verified=False)
    ok_def = ev("d2", "BLOCK", "B", "A", start=10 * S, status="VALID", verified=True)
    evs = [attack, ok_def, reply]
    assert link_counters(evs, link_defenses(evs, observed=obs)) == []  # 후보 반격은 반격으로 연결하지 않는다


def test_candidate_contacts_are_not_scenes():
    contact = ev("c1", "DIRECT_CONTACT", "B", "A", status="CANDIDATE", verified=False, target_surface="head")
    assert select_scenes([contact], [], duration_us=60 * S)["scenes"] == []


def assignment(user, opp):
    return TrackAssignmentSet(state="ASSIGNED", assignments=[TrackAssignment(participant_id="A", role="USER", track_ids=user),
                                                             TrackAssignment(participant_id="B", role="OPPONENT", track_ids=opp)])


def cand(pid, tracks, role="UNRESOLVED"):
    return {"participant_id": pid, "role": role, "track_ids": tracks}


def test_role_suggestion_needs_full_track_overlap_and_ignores_order():
    got = suggest_candidate_roles([cand("candidate:1", [5]), cand("candidate:0", [3, 4])], assignment([3, 4], [5]))
    by = {s["participant_id"]: s for s in got}
    assert (by["candidate:0"]["suggested_role"], by["candidate:0"]["target_participant_id"]) == ("USER", "A")
    assert (by["candidate:1"]["suggested_role"], by["candidate:1"]["target_participant_id"]) == ("OPPONENT", "B")  # 번호·순서로 정하지 않는다


def test_role_suggestion_reasons():
    a = assignment([3], [5])
    reasons = {s["participant_id"]: s["reason_code"] for s in suggest_candidate_roles(
        [cand("c1", [3, 9]), cand("c2", [3, 5]), cand("c3", [9]), cand("c4", []), cand("c5", [3], role="USER")], a)}
    assert reasons == {"c1": "partial_match", "c2": "conflict", "c3": "track_not_designated", "c4": "no_track"}  # 이미 역할이 있는 선수는 제외
    assert {s["reason_code"] for s in suggest_candidate_roles([cand("c1", [3])], TrackAssignmentSet())} == {"no_designation"}


def test_endpoint_suggests_without_saving(env, monkeypatch):  # noqa: F811
    body = {"annotation_source": "t", "definition_version": "phase1b_v1", "participants": [], "events": [TEAM_LINE]}
    run = env.post("/api/v1/videos/vid1/imports", json=body).json()
    url = f"/api/v1/runs/{run['run_id']}/candidate-roles"
    first = env.get(url).json()
    assert first == [{"participant_id": "candidate:0", "suggested_role": None, "target_participant_id": None, "track_ids": [0], "reason_code": "no_designation"}]
    app = env.app
    monkeypatch.setattr(app.state.designations, "current", lambda video_id, owner_id: (assignment([0], [1]), "set"))
    second = env.get(url).json()
    assert second[0]["suggested_role"] == "USER" and second[0]["target_participant_id"] == "A"
    # 제안만 하고 결과의 역할은 바뀌지 않는다
    assert [p["role"] for p in env.get(f"/api/v1/runs/{run['run_id']}").json()["participants"]] == ["UNRESOLVED"]
    assert env.get("/api/v1/runs/nope/candidate-roles").status_code == 404

def test_import_notes_the_excluded_candidates(env):  # noqa: F811
    body = {"annotation_source": "t", "definition_version": "phase1b_v1", "participants": [], "events": [TEAM_LINE], "derive": ["rear_guard_return"]}
    r = env.post("/api/v1/videos/vid1/imports", json=body)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["counts"]["events"] == 1 and run["counts"].get("aggregates", 0) == 0
    stored = env.app.state.store.get_for_owner(run['run_id'], 'dev-user')
    assert any('후보 사건 1건' in n for n in stored.raw['notes'])
