"""결과 조회 API(fixture 모드)."""

from __future__ import annotations

PROBLEM = "application/problem+json"
COUNTER = "/api/v1/runs/fixture-counter"
SCENARIO6 = "/api/v1/runs/fixture-scenario6"


def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_list_runs(client):
    r = client.get("/api/v1/runs")
    assert r.status_code == 200
    assert {x["run_id"] for x in r.json()} == {"fixture-counter", "fixture-scenario6"}


def test_run_summary_marks_synthetic_data(client):
    body = client.get(COUNTER).json()
    assert body["mode"] == "fixture" and body["status"] == "completed"
    assert body["dataset_kind"] == "synthetic" and body["expert_validated"] is False
    assert body["counts"]["events"] == 10


def test_findings_separate_rendered_and_withheld(client):
    findings = {f["finding_id"]: f for f in client.get(f"{COUNTER}/findings").json()}
    assert findings["F01"]["status"] == "valid"
    assert "후속 상황을 판정할 수 있었던 것은 4회" in findings["F01"]["text"]
    assert findings["F01"]["evidence_refs"] == ["EV01", "EV02", "EV03", "EV04", "EV05"]
    for fid, reason in (("F02", "insufficient_behavior_evidence_and_unvalidated_rule"), ("F03", "distance_unavailable")):
        assert findings[fid]["status"] == "withheld"
        assert findings[fid]["text"] is None and findings[fid]["hold_reason"] == reason


def test_aggregate_detail_splits_members(client):
    body = client.get(f"{COUNTER}/aggregates/AG01").json()
    ids = {k: [m["event"]["event_id"] for m in v] for k, v in body["members"].items()}
    assert ids["numerator"] == ["E01", "E04", "E07"]
    assert ids["denominator"] == ["E01", "E04", "E07", "E09"]
    assert ids["unknown"] == ["E10"] and ids["excluded"] == []
    unknown = body["members"]["unknown"][0]["assessment"]
    assert unknown["value"] == "unknown" and unknown["reason_code"] == "occluded"
    assert body["aggregate"]["ratio"] == 0.75


def test_aggregate_with_region_condition(client):
    body = client.get(f"{COUNTER}/aggregates/AG02").json()
    assert body["aggregate"]["contact_region_condition"] == "head"
    assert [m["event"]["event_id"] for m in body["members"]["numerator"]] == ["E01", "E04"]
    absent = next(m for m in body["members"]["denominator"] if m["event"]["event_id"] == "E07")
    assert absent["assessment"]["value"] == "absent"


def test_timeline_returns_overlapping_items_only(client):
    body = client.get(f"{COUNTER}/timeline", params={"from_us": 9_000_000, "to_us": 14_000_000}).json()
    assert [e["event_id"] for e in body["events"]] == ["E01", "E02", "E03"]
    assert {r["relation_id"] for r in body["relations"]} == {"R01", "R02"}
    assert "AS01" in {a["assessment_id"] for a in body["assessments"]}


def test_timeline_interval_is_half_open(client):
    # E01은 [10.0s, 10.7s). 끝 시각 10.7s에서 시작하는 구간에는 포함되지 않는다.
    body = client.get(f"{COUNTER}/timeline", params={"from_us": 10_700_000, "to_us": 10_740_000}).json()
    assert body["events"] == []


def test_timeline_includes_states(client):
    body = client.get(f"{SCENARIO6}/timeline", params={"from_us": 19_000_000, "to_us": 21_000_000}).json()
    assert [s["state_id"] for s in body["states"]] == ["S01", "S02"]
    assert [e["event_id"] for e in body["events"]] == ["E01"]
    assert body["events"][0]["pre_state_refs"] == ["S01"]


def test_timeline_rejects_empty_range(client):
    r = client.get(f"{COUNTER}/timeline", params={"from_us": 5, "to_us": 5})
    assert r.status_code == 422 and r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "invalid_range"


def test_event_detail(client):
    body = client.get(f"{COUNTER}/events/E02").json()
    assert body["event"]["event_label"] == "PUNCH_ATTEMPT"
    assert {r["relation_id"] for r in body["relations"]} == {"R01", "R02"}
    assert body["exchanges"][0]["exchange_id"] == "X01"
    assert [e["evidence_id"] for e in body["evidence"]] == ["EV01"]


def test_unavailable_evidence_keeps_structured_data_but_is_not_playable(client):
    body = client.get(f"{COUNTER}/evidence/EV01").json()
    assert body["evidence"]["start_us"] == 9_000_000
    assert body["playable"] is False and body["access_url"] is None
    assert body["reason_code"] == "evidence_unavailable"


def test_run_summary_includes_participants_with_roles(client):
    body = client.get(COUNTER).json()
    roles = {p["participant_id"]: p["role"] for p in body["participants"]}
    assert roles == {"A": "USER", "B": "OPPONENT"}


def test_evidence_list_is_sorted_by_time(client):
    body = client.get(f"{SCENARIO6}/evidence").json()
    starts = [x["evidence"]["start_us"] for x in body]
    assert starts == sorted(starts) and len(body) == 6
    assert all(x["playable"] is False and x["reason_code"] == "evidence_unavailable" for x in body)


def test_errors_use_problem_details(client):
    for url, code in (
        ("/api/v1/runs/nope", "run_not_found"),
        (f"{COUNTER}/aggregates/AG99", "aggregate_not_found"),
        (f"{COUNTER}/events/E99", "event_not_found"),
        (f"{COUNTER}/evidence/EV99", "evidence_not_found"),
    ):
        r = client.get(url)
        assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)
        body = r.json()
        assert body["code"] == code and body["status"] == 404 and body["trace_id"]
        assert "Traceback" not in r.text and "C:\\" not in r.text


def test_unknown_route_is_problem_details(client):
    r = client.get("/api/v1/nothing")
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)


def test_other_users_cannot_see_the_run(other_user_client):
    c = other_user_client
    assert c.get("/api/v1/runs").json() == []
    for path in ("", "/findings", "/timeline", "/aggregates/AG01", "/events/E01", "/evidence", "/evidence/EV01"):
        r = c.get(COUNTER + path)
        assert r.status_code == 404, path
        assert r.json()["code"] == "run_not_found"  # 존재 여부를 알리지 않는다


def test_missing_value_is_null_not_zero(client):
    body = client.get(f"{COUNTER}/findings").json()
    withheld = next(f for f in body if f["finding_id"] == "F03")
    assert withheld["text"] is None and withheld["slots"] is None
