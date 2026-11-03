from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from app.api.deps import User, current_user
from app.api.main import create_app
from app.domain.models import FrameTransform, PoseFrame, PoseSet, blank_keypoints
from app.media import binaries
from app.settings import Settings


@pytest.fixture()
def app(tmp_path):
    # 개발용 영상(backend/dev_media)과 분리해, 영상이 없는 상태를 기본으로 시험한다.
    return create_app(Settings(media_dir=tmp_path / "no-media", media_secret="test-secret", data_dir=tmp_path / "data"))


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def other_user_client(app):
    app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    return TestClient(app)


def _make_video(path, vfr: bool) -> None:
    ffmpeg = binaries.find_binary("ffmpeg")
    # 320x180 화면 안에서 움직이게 한다. 화면 밖이면 모든 프레임이 같아져 프레임 구별 시험이 의미가 없다.
    graph = "[0][1]overlay=x='150+120*sin(t*3)':y=80:eval=frame"
    if vfr:
        graph += ",select='not(mod(n,4))+not(mod(n,6))'"
    cmd = [
        ffmpeg, "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x1d3a5f:s=320x180:r=30:d=3",
        "-f", "lavfi", "-i", "color=c=0xffd400:s=20x20:r=30:d=3",
        "-filter_complex", graph,
    ]
    if vfr:
        cmd += ["-fps_mode", "vfr", "-video_track_timescale", "90000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-g", "30", "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    """3초짜리 고정(01)·가변(02) 프레임률 시험 영상과 01의 관절 데이터."""
    if not binaries.available():
        pytest.skip("FFmpeg를 찾을 수 없음")
    directory = tmp_path_factory.mktemp("media")
    _make_video(directory / "demo-video-01.mp4", vfr=False)
    _make_video(directory / "demo-video-02.mp4", vfr=True)
    # 분석 프레임(640×360)은 표시 영상(320×180)의 2배다. 저장은 분석 좌표, 응답은 표시 좌표다.
    frames = [
        PoseFrame(
            timestamp_us=t,
            participant_id="A",
            keypoints={**blank_keypoints(), "nose": (20.0, 40.0, 0.9), "left_shoulder": (60.0, 80.0, 1.0)},
        )
        for t in (0, 33_333, 66_667, 1_000_000, 2_000_000)
    ]
    pose = PoseSet(
        landmark_set_id="test-1",
        coordinate_space_id="analysis_px:640x360",
        transform=FrameTransform(
            coded_width=320, coded_height=180, display_width=320, display_height=180,
            analysis_width=640, analysis_height=360, scale=2.0,
        ),
        connections=[("nose", "left_shoulder"), ("nose", "right_ankle")],
        frames=frames,
    )
    (directory / "demo-video-01.poses.json").write_text(pose.model_dump_json(), encoding="utf-8")
    return directory


@pytest.fixture()
def media_app(media_dir, tmp_path):
    return create_app(Settings(media_dir=media_dir, media_secret="test-secret", data_dir=tmp_path / "data"))


@pytest.fixture()
def media_client(media_app):
    return TestClient(media_app)
