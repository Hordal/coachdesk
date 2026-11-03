"""업로드·작업 시험용 도우미."""

from __future__ import annotations

import hashlib
import subprocess
import uuid

from app.media import binaries


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_upload(client, data: bytes, *, filename="clip.mp4", sport="boxing", key=None):
    return client.post(
        "/api/v1/uploads",
        json={"filename": filename, "size_bytes": len(data), "sport": sport},
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
    )


def put_part(client, upload: dict, number: int, data: bytes, *, digest: str | None = None):
    size = upload["part_size"]
    part = data[number * size : (number + 1) * size]
    return client.put(
        f"/api/v1/uploads/{upload['upload_id']}/parts/{number}",
        content=part,
        headers={"X-Part-Sha256": digest if digest is not None else sha256(part)},
    )


def upload_all(client, data: bytes, **kwargs) -> dict:
    """업로드를 만들고 모든 조각을 보낸 뒤 완료한다. 완료 응답을 돌려준다."""
    created = create_upload(client, data, **kwargs)
    assert created.status_code in (200, 201), created.text
    upload = created.json()
    for n in range(upload["part_count"]):
        r = put_part(client, upload, n, data)
        assert r.status_code == 200, r.text
    done = client.post(f"/api/v1/uploads/{upload['upload_id']}/complete")
    assert done.status_code == 200, done.text
    return done.json()


def make_video(path, *, vfr: bool = False, codec: str = "libx264", seconds: int = 3, extra: list[str] | None = None) -> None:
    ffmpeg = binaries.find_binary("ffmpeg")
    graph = "[0][1]overlay=x='150+120*sin(t*3)':y=80:eval=frame"
    if vfr:
        graph += ",select='not(mod(n,4))+not(mod(n,6))'"
    cmd = [
        ffmpeg, "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1d3a5f:s=320x180:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"color=c=0xffd400:s=20x20:r=30:d={seconds}",
        "-filter_complex", graph,
    ]
    if vfr:
        cmd += ["-fps_mode", "vfr", "-video_track_timescale", "90000"]
    cmd += ["-c:v", codec, "-pix_fmt", "yuv420p", *(extra or []), str(path)]
    subprocess.run(cmd, check=True)
