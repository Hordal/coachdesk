"""manual 모드 가져오기(상세 설계 27의 3.3)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.settings import Settings
from app.storage.db import Database, now_rfc3339

FIXTURE = Path(__file__).resolve().parents[2] / "Notion" / "02A_가상_사건_데이터.json"
DURATION_US = 540_000_000
COMPUTED = ("denominator_refs", "numerator_refs", "unknown_refs", "excluded_refs", "counts", "ratio")


@pytest.fixture()
def env(tmp_path):
    settings = Settings(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data")
    app = create_app(settings)
    return app, TestClient(app), Database(settings.db_path)


def add_video(db: Database, video_id="vid1", *, status="ready", owner="dev-user", duration=DURATION_US) -> str:
    with db.tx(immediate=True) as conn:
        conn.execute(
            "INSERT INTO videos (video_id, owner_id, filename, sport, source_path, size_bytes, sha256, duration_us, width, height, status, created_at) "
            "VALUES (?, ?, 'a.mp4', 'boxing', 'x', 1, 'h', ?, 320, 180, ?, ?)",
            (video_id, owner, duration, status, now_rfc3339()),
        )
    return video_id


def payload(video_id="vid1") -> dict:
    """가상 예제에서 입력 파일 모양을 만든다. 서버가 채우는 값과 집계 계산값은 뺀다."""
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for key in ("schema_version", "dataset_kind", "expert_validated", "video_id", "duration_us", "sport", "time_unit", "interval_convention", "expected_summary"):
        doc.pop(key, None)
    for ev in doc["evidence"]:
        ev["video_id"] = video_id
    for agg in doc["aggregates"]:
        agg["scope"]["video_id"] = video_id
        for key in COMPUTED:
            agg.pop(key)
    doc["annotation_source"] = "labeler-a"
    return doc


def post(client, body, video_id="vid1"):
    return client.post(f"/api/v1/videos/{video_id}/imports", json=body)


def test_import_creates_a_manual_run_with_server_computed_aggregates(env):
    _, client, db = env
    add_video(db)
    r = post(client, payload())
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["mode"] == "manual" and run["dataset_kind"] == "manual" and run["status"] == "completed"
    assert run["expert_validated"] is False and run["video_id"] == "vid1" and run["duration_us"] == DURATION_US
    assert run["counts"] == {"events": 10, "assessments": run["counts"]["assessments"], "aggregates": 2, "findings": 3}

    # 집계는 입력이 아니라 판정에서 계산한 값이며, 가상 예제의 기대값과 같다.
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["aggregates"]
    for agg in expected:
        got = client.get(f"/api/v1/runs/{run['run_id']}/aggregates/{agg['aggregate_id']}").json()["aggregate"]
        assert got["counts"] == agg["counts"] and got["ratio"] == agg["ratio"]
        assert got["numerator_refs"] == agg["numerator_refs"] and got["unknown_refs"] == agg["unknown_refs"]


def test_findings_text_is_generated_from_the_recomputed_values(env):
    _, client, db = env
    add_video(db)
    run_id = post(client, payload()).json()["run_id"]
    imported = client.get(f"/api/v1/runs/{run_id}/findings").json()
    reference = client.get("/api/v1/runs/fixture-counter/findings").json()
    assert [f["text"] for f in imported] == [f["text"] for f in reference]
    assert imported[0]["text"] and imported[1]["text"] is None  # 보류된 주장은 문장이 없다


def test_each_import_is_a_new_run_and_keeps_the_earlier_ones(env):
    _, client, db = env
    add_video(db)
    first = post(client, payload()).json()["run_id"]
    second = post(client, payload()).json()["run_id"]
    assert first != second
    ids = [r["run_id"] for r in client.get("/api/v1/runs").json()]
    assert first in ids and second in ids
    assert client.get("/api/v1/videos/vid1/summary").json()["run_id"] == second


def test_server_owned_fields_cannot_be_supplied(env):
    _, client, db = env
    add_video(db)
    for extra in ({"dataset_kind": "model"}, {"video_id": "other"}, {"duration_us": 5}, {"expert_validated": True}):
        r = post(client, {**payload(), **extra})
        assert r.status_code == 422 and r.json()["code"] == "invalid_request", extra
    body = payload()
    body["aggregates"][0]["counts"] = {"candidates": 1, "denominator": 1, "numerator": 1, "unknown": 0, "excluded": 0}
    assert post(client, body).status_code == 422  # 집계 값은 받지 않는다


def contract_errors(client, body) -> list[str]:
    r = post(client, body)
    assert r.status_code == 422 and r.json()["code"] == "invalid_import", r.text
    return r.json()["errors"]


def test_contract_violations_are_rejected_with_a_list_and_nothing_is_saved(env):
    _, client, db = env
    add_video(db)
    before = len(client.get("/api/v1/runs").json())

    body = payload()
    body["aggregates"][0]["candidate_refs"].append("E99")  # 없는 사건
    assert any("E99" in e for e in contract_errors(client, body))

    body = payload()
    body["events"][0]["end_timestamp_us"] = DURATION_US + 1  # 영상 길이를 넘는 구간
    assert contract_errors(client, body)

    body = payload()
    body["events"][0]["start_timestamp_us"], body["events"][0]["end_timestamp_us"] = 5, 5  # 길이 0 구간
    assert contract_errors(client, body)

    body = payload()
    body["events"].append(copy.deepcopy(body["events"][0]))  # ID 중복
    assert any("중복" in e for e in contract_errors(client, body))

    assert len(client.get("/api/v1/runs").json()) == before


def test_evidence_and_scope_must_belong_to_this_video(env):
    _, client, db = env
    add_video(db)
    body = payload()
    body["evidence"][0]["video_id"] = "someone-elses"
    assert any("EV01" in e for e in contract_errors(client, body))

    body = payload()
    body["aggregates"][0]["scope"]["video_id"] = "someone-elses"
    assert any("AG01" in e for e in contract_errors(client, body))


def test_a_finding_that_does_not_match_its_template_is_rejected(env):
    _, client, db = env
    add_video(db)
    body = payload()
    body["findings"][0]["input_refs"] = ["AG01"]  # 템플릿이 필요로 하는 두 번째 집계가 없음
    assert any("F01" in e for e in contract_errors(client, body))

    body = payload()
    body["findings"][0]["template_id"] = "made_up_template"
    assert any("F01" in e for e in contract_errors(client, body))


def test_withheld_findings_still_need_a_reason_and_valid_ones_must_not_have_one(env):
    _, client, db = env
    add_video(db)
    body = payload()
    body["findings"][1]["hold_reason"] = None
    assert contract_errors(client, body)


def test_many_errors_are_capped_but_counted(env):
    _, client, db = env
    add_video(db)
    body = payload()
    body["aggregates"][0]["candidate_refs"] = [f"X{i}" for i in range(40)]
    r = post(client, body)
    assert r.status_code == 422 and len(r.json()["errors"]) <= 20 and r.json()["error_count"] >= 40


def test_import_requires_a_ready_owned_video(env):
    app, client, db = env
    add_video(db, "busy", status="processing")
    r = post(client, payload("busy"), "busy")
    assert r.status_code == 409 and r.json()["code"] == "video_not_ready"
    assert post(client, payload("nope"), "nope").status_code == 404

    from app.api.deps import User, current_user

    add_video(db, "mine")
    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    assert post(TestClient(app), payload("mine"), "mine").status_code == 404  # 남의 영상은 없는 것과 같다
    assert TestClient(app).get("/api/v1/runs").json() == []


def test_imported_run_opens_in_every_result_endpoint(env):
    _, client, db = env
    add_video(db)
    run_id = post(client, payload()).json()["run_id"]
    base = f"/api/v1/runs/{run_id}"
    assert client.get(f"{base}/timeline", params={"from_us": 0, "to_us": DURATION_US}).status_code == 200
    assert client.get(f"{base}/events/E01").status_code == 200
    evidence = client.get(f"{base}/evidence").json()
    assert len(evidence) == 5 and all(e["playable"] is False for e in evidence)  # 영상 파일이 없으면 재생 불가로 표시


def test_import_file_validates_against_the_exported_schema():
    from jsonschema import Draft202012Validator

    from scripts.export_schema import OUT

    schema = json.loads((OUT / "manual_import.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(payload())) == []
    assert list(validator.iter_errors({**payload(), "dataset_kind": "model"}))  # 서버가 정하는 값은 받지 않는다


def test_run_responses_carry_the_uploaded_filename(env):
    _, client, db = env
    add_video(db)  # 파일 이름 a.mp4
    run_id = post(client, payload()).json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["filename"] == "a.mp4"
    assert next(r for r in client.get("/api/v1/runs").json() if r["run_id"] == run_id)["filename"] == "a.mp4"
    example = next(r for r in client.get("/api/v1/runs").json() if r["dataset_kind"] != "manual")
    assert example["filename"] is None  # 올린 영상이 없는 가상 예제
