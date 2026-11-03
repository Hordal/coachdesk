"""연구 산출물 연결에서 잘못된 확정·소유자 접근·계약 위반을 막는다."""
import copy
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from app.api.main import create_app
from app.settings import Settings
from app.storage.db import Database
from .test_manual_import import add_video
from .test_team_events import TEAM_LINE


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    db = Database(settings.db_path)
    add_video(db, duration=17_000_000)
    return TestClient(app), db


def test_contracts_validate_and_accept_team_payload(env):
    client, _ = env
    catalog = client.get("/api/v1/data-contracts").json()
    assert catalog["time_unit"] == "us"
    for item in catalog["items"]:
        response = client.get(item["schema_url"])
        Draft202012Validator.check_schema(response.json())
        assert "attachment" in response.headers["content-disposition"]
    schema = client.get("/api/v1/data-contracts/actions").json()
    Draft202012Validator(schema).validate({"events": [TEAM_LINE]})


def test_action_candidate_is_saved_with_unknown_identity_and_fields_preserved(env):
    client, _ = env
    response = client.post("/api/v1/videos/vid1/actions", json={"events": [{**TEAM_LINE, "custom_research": "preserved"}]})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["dataset_kind"] == "model" and run["expert_validated"] is False
    assert run["participants"][0]["role"] == "UNRESOLVED"
    event = client.get(f"/api/v1/runs/{run['run_id']}/events/ev_8299958_PUNCH_CANDIDATE_candidate-0").json()["event"]
    assert event["custom_research"] == "preserved" and not event["verified"]


@pytest.mark.parametrize("change", [{"verified": True}, {"role": "USER"}, {"status": "VALID"}, {"outcome": "HIT"}, {"end_timestamp_us": 99_000_000}])
def test_invalid_candidates_do_not_create_results(env, change):
    client, _ = env
    before = client.get("/api/v1/runs").json()
    response = client.post("/api/v1/videos/vid1/actions", json={"events": [{**copy.deepcopy(TEAM_LINE), **change}]})
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/runs").json() == before


def test_action_import_does_not_expose_other_users_video(env):
    client, db = env
    add_video(db, "other", owner="another-user")
    response = client.post("/api/v1/videos/other/actions", json={"events": [TEAM_LINE]})
    assert response.status_code == 404
