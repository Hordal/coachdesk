"""JSON Schema와 OpenAPI를 모델에서 내보낸다.

    python scripts/export_schema.py            schemas/ 에 파일을 쓴다
    python scripts/export_schema.py --check    저장된 파일이 최신인지 확인한다(다르면 종료 코드 1)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.main import create_app  # noqa: E402
from pydantic.json_schema import models_json_schema  # noqa: E402

from app.domain.models import DetectionRecord, ManualImport, PoseImport, PoseSet, ResultBundle, TrackAssignmentSet, TrackRecord, VideoMetadata  # noqa: E402
from app.settings import Settings  # noqa: E402

OUT = ROOT / "schemas"
JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def result_bundle_schema() -> dict:
    schema = ResultBundle.model_json_schema()
    return {
        "$schema": JSON_SCHEMA_DRAFT,
        "$id": "urn:fight-review:schema:result-bundle:0.2",
        **schema,
    }


def manual_import_schema() -> dict:
    schema = ManualImport.model_json_schema()
    return {
        "$schema": JSON_SCHEMA_DRAFT,
        "$id": "urn:fight-review:schema:manual-import:0.3",
        **schema,
    }


def observations_schema() -> dict:
    """선수 검출·추적·포즈 파이프라인과 주고받는 관측 데이터(14, 27의 9절)."""
    models = [(m, "validation") for m in (VideoMetadata, DetectionRecord, TrackRecord, TrackAssignmentSet, PoseSet)]
    _, schema = models_json_schema(models, ref_template="#/$defs/{model}")
    return {"$schema": JSON_SCHEMA_DRAFT, "$id": "urn:fight-review:schema:observations:0.2", **schema}


def pose_import_schema() -> dict:
    """팀 연구 포즈(pose_observations·pose_quality·window_candidates 줄)의 가져오기 입력(14, 27의 9절)."""
    schema = PoseImport.model_json_schema()
    return {"$schema": JSON_SCHEMA_DRAFT, "$id": "urn:fight-review:schema:pose-import:0.1", **schema}


def openapi_schema() -> dict:
    # 내보내기가 실제 데이터 폴더를 만들지 않도록 임시 폴더를 쓴다.
    with tempfile.TemporaryDirectory() as tmp:
        return create_app(Settings(data_dir=Path(tmp))).openapi()


def render(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


FILES = {
    "result_bundle.schema.json": result_bundle_schema,
    "manual_import.schema.json": manual_import_schema,
    "observations.schema.json": observations_schema,
    "pose_import.schema.json": pose_import_schema,
    "openapi.json": openapi_schema,
}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for name, build in FILES.items():
        text = render(build())
        path = OUT / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            OUT.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"쓰기: {path}")
    if stale:
        print("최신이 아님:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
