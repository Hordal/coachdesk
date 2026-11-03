"""개발용 시험 영상과 관절 데이터를 만든다(상세 설계 27의 5단계 시험용).

    python scripts/make_demo_media.py            backend/dev_media/ 에 파일을 만든다

영상은 어두운 파랑 배경 위를 움직이는 정사각형 두 개다. 사각형의 위치는 표시 시각 t(초)의 함수이고,
관절 데이터도 같은 식으로 각 프레임의 실제 표시 시각에서 계산한다. 그래서 화면의 오버레이가 영상의
사각형과 겹치는지로 시간 동기화를 눈과 수치로 확인할 수 있다.

    demo-video-01  30fps 고정 프레임률 (상대 반격 예제)
    demo-video-02  일부 프레임을 버린 가변 프레임률 (시나리오 6 예제)

관절 데이터는 두 예제의 근거 구간(evidence)에서만 만든다. 그 밖의 구간은 관측이 없는 구간이다.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import CORE_JOINTS, FrameTransform, PoseFrame, PoseSet  # noqa: E402
from app.media.binaries import find_binary  # noqa: E402
from app.media.probe import probe_video  # noqa: E402
from app.settings import Settings  # noqa: E402

WIDTH, HEIGHT, SIDE = 640, 360, 40
DURATION_S = 540
BACKGROUND = "0x1d3a5f"
COLORS = {"A": "0xffd400", "B": "0x00d4ff"}
LANDMARK_SET = "core13-demo-square-1"
ANALYSIS_W, ANALYSIS_H = 1280, 720  # 분석 프레임. 시험 영상(640×360)의 2배라 좌표 변환이 실제로 일어난다
# 정사각형 안의 상대 위치(0~1). 어깨·골반이 네 모서리라 오버레이가 사각형과 겹치는지로 동기화를 확인할 수 있다.
LAYOUT = {
    "nose": (0.5, 0.0),
    "left_shoulder": (0.0, 0.0), "right_shoulder": (1.0, 0.0),
    "left_elbow": (0.0, 0.35), "right_elbow": (1.0, 0.35),
    "left_wrist": (0.25, 0.65), "right_wrist": (0.75, 0.65),
    "left_hip": (0.0, 1.0), "right_hip": (1.0, 1.0),
    "left_knee": (0.25, 0.85), "right_knee": (0.75, 0.85),
    "left_ankle": (0.25, 1.0), "right_ankle": (0.75, 1.0),
}
assert set(LAYOUT) == set(CORE_JOINTS)
CONNECTIONS = [
    ("left_shoulder", "right_shoulder"), ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"), ("left_hip", "right_hip"),
    ("left_hip", "left_knee"), ("left_knee", "left_ankle"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
]
TRANSFORM = FrameTransform.fit(WIDTH, HEIGHT, 0, ANALYSIS_W, ANALYSIS_H)

# 사각형 왼쪽 위 모서리의 위치(초 t의 함수). FFmpeg 식과 같은 식이다.
FFMPEG_POS = {"A": ("300+200*sin(t*2)", "160"), "B": ("300+150*cos(t*1.5)", "60")}


def position(participant: str, t: float) -> tuple[float, float]:
    if participant == "A":
        return 300 + 200 * math.sin(t * 2), 160.0
    return 300 + 150 * math.cos(t * 1.5), 60.0


def build_video(out: Path, vfr: bool) -> None:
    ffmpeg = find_binary("ffmpeg")
    graph = (
        f"[0][1]overlay=x='{FFMPEG_POS['A'][0]}':y='{FFMPEG_POS['A'][1]}':eval=frame[a];"
        f"[a][2]overlay=x='{FFMPEG_POS['B'][0]}':y='{FFMPEG_POS['B'][1]}':eval=frame"
    )
    if vfr:
        graph += ",select='not(mod(n,4))+not(mod(n,6))'"
    cmd = [
        ffmpeg, "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={BACKGROUND}:s={WIDTH}x{HEIGHT}:r=30:d={DURATION_S}",
        "-f", "lavfi", "-i", f"color=c={COLORS['A']}:s={SIDE}x{SIDE}:r=30:d={DURATION_S}",
        "-f", "lavfi", "-i", f"color=c={COLORS['B']}:s={SIDE}x{SIDE}:r=30:d={DURATION_S}",
        "-filter_complex", graph,
    ]
    if vfr:
        cmd += ["-fps_mode", "vfr", "-video_track_timescale", "90000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-g", "30",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def evidence_ranges(fixture: Path) -> list[tuple[int, int]]:
    data = json.loads(fixture.read_text(encoding="utf-8"))
    return [(e["start_us"], e["end_us"]) for e in data["evidence"]]


def build_poses(video: Path, ranges: list[tuple[int, int]]) -> PoseSet:
    info = probe_video(video)
    frames: list[PoseFrame] = []
    for frame in info.frames:
        if not any(a <= frame.pts_us < b for a, b in ranges):
            continue
        t = frame.pts_us / 1_000_000
        for participant in ("A", "B"):
            x, y = position(participant, t)
            keypoints = {}
            for name, (fx, fy) in LAYOUT.items():
                ax, ay = TRANSFORM.to_analysis(x + fx * SIDE, y + fy * SIDE)  # 분석 프레임 좌표로 저장
                keypoints[name] = (round(ax, 3), round(ay, 3), 1.0)
            frames.append(PoseFrame(timestamp_us=frame.pts_us, participant_id=participant, keypoints=keypoints))
    return PoseSet(
        landmark_set_id=LANDMARK_SET,
        coordinate_space_id=f"analysis_px:{ANALYSIS_W}x{ANALYSIS_H}",
        transform=TRANSFORM,
        connections=CONNECTIONS,
        frames=frames,
    )


def main() -> int:
    settings = Settings()
    out_dir = settings.media_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = settings.resolved_fixture_runs()
    jobs = [("demo-video-01", runs["fixture-counter"], False), ("demo-video-02", runs["fixture-scenario6"], True)]
    for video_id, fixture, vfr in jobs:
        video = out_dir / f"{video_id}.mp4"
        print(f"영상 생성: {video.name} ({'가변' if vfr else '고정'} 프레임률)")
        build_video(video, vfr)
        info = probe_video(video)
        print(f"  프레임 {info.frame_count}개, 가변 프레임률: {info.is_vfr}, 길이 {info.duration_us / 1e6:.1f}초")
        pose = build_poses(video, evidence_ranges(fixture))
        pose_path = out_dir / f"{video_id}.poses.json"
        pose_path.write_text(pose.model_dump_json(), encoding="utf-8")
        print(f"  관절 프레임 {len(pose.frames)}개 → {pose_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
