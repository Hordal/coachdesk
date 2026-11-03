"""모델·JSON Schema·OpenAPI 정합성."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.domain.models import ResultBundle
from app.settings import Settings
from scripts.export_schema import FILES, OUT, render

SETTINGS = Settings()
FIXTURE_PATHS = list(SETTINGS.resolved_fixture_runs().values())


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_fixture_parses_into_strict_models(path):
    ResultBundle.model_validate(load(path))


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_fixture_validates_against_exported_schema(path):
    schema = json.loads((OUT / "result_bundle.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(load(path)))
    assert errors == []


def test_exported_files_are_up_to_date():
    for name, build in FILES.items():
        assert (OUT / name).read_text(encoding="utf-8") == render(build()), (
            f"{name}이 모델과 다름: python scripts/export_schema.py 로 다시 내보낼 것"
        )


def test_schema_uses_draft_2020_12():
    schema = json.loads((OUT / "result_bundle.schema.json").read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_unknown_field_is_rejected():
    raw = load(FIXTURE_PATHS[0])
    raw["participants"][0]["surprise"] = 1
    with pytest.raises(ValidationError):
        ResultBundle.model_validate(raw)
    schema = json.loads((OUT / "result_bundle.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(raw))


def test_event_keeps_unknown_fields_from_the_detection_model():
    """팀 동작 검출 모델이 필드를 추가해도 사건은 버리지 않고 보존한다."""
    raw = load(FIXTURE_PATHS[0])
    raw["events"][0]["new_team_field"] = {"a": 1}
    bundle = ResultBundle.model_validate(raw)
    assert bundle.events[0].model_dump()["new_team_field"] == {"a": 1}
    schema = json.loads((OUT / "result_bundle.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(raw)) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["assessments"][0].__setitem__("value", "maybe"),
        lambda r: r["assessments"][0].__setitem__("status", "ok"),
        lambda r: r["events"][0].__setitem__("start_timestamp_us", -1),
        lambda r: r["events"][0].__setitem__("status", "bogus"),
        lambda r: r["aggregates"][0].__setitem__("ratio", 1.5),
        lambda r: r.__setitem__("sport", "judo"),
    ],
)
def test_invalid_values_are_rejected_by_model_and_schema(mutate):
    raw = copy.deepcopy(load(FIXTURE_PATHS[0]))
    mutate(raw)
    with pytest.raises(ValidationError):
        ResultBundle.model_validate(raw)
    schema = json.loads((OUT / "result_bundle.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(raw))
