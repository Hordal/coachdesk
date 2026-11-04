"""로컬 분석 모델의 고정 출처·무결성. 사용자 영상은 외부로 전송하지 않는다."""
import hashlib
import importlib.util
from pathlib import Path

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task"
MODEL_SHA256 = "5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1"
MODEL_FILE = "pose_landmarker_full.task"
MODEL_NAME = "MediaPipe Pose Full · CPU"
ENGINE_VERSION = "coachdesk-local-pose-v2"


def model_path() -> Path:
    from ..settings import REPO_ROOT
    import os
    return Path(os.environ.get("LOCAL_POSE_MODEL",REPO_ROOT/"runtime"/"models"/MODEL_FILE))


def model_ready(path: Path | None = None) -> bool:
    path=path or model_path()
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==MODEL_SHA256


def installed() -> bool:
    return importlib.util.find_spec("mediapipe") is not None and importlib.util.find_spec("numpy") is not None and model_ready()
