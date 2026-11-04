"""Public, licensed smoke-test video. Never includes user recordings."""
from pathlib import Path
import hashlib

SAMPLE_URL = "https://videos.pexels.com/video-files/4806552/4806552-hd_1280_720_25fps.mp4"
SAMPLE_SOURCE = "https://www.pexels.com/video/a-man-shadow-boxing-in-a-gym-4806552/"
SAMPLE_SHA256 = "5d8f096fc368eeb9fd10c2ce8d9fc32e97d18cdce3d3762324dcbc326c7457b5"


def sample_path() -> Path:
    return Path(__file__).resolve().parents[3] / "runtime" / "samples" / "shadow_boxing.mp4"


def sample_ready() -> bool:
    path = sample_path()
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SAMPLE_SHA256
