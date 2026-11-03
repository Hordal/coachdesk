"""근거 영상: 서명 주소, 범위 요청, 프레임 표, 정확한 프레임 이미지, 관절 데이터."""

from __future__ import annotations

import hashlib
import time

from app.api.deps import User, current_user
from app.media.store import MediaStore, UrlSigner

COUNTER = "/api/v1/runs/fixture-counter"      # demo-video-01 (고정 프레임률)
SCENARIO6 = "/api/v1/runs/fixture-scenario6"  # demo-video-02 (가변 프레임률)
PROBLEM = "application/problem+json"


# --- 서명 주소 -------------------------------------------------------------

def test_signature_roundtrip_and_tamper():
    signer = UrlSigner("s", 300)
    url = signer.sign("v1", now=1000)
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert exp == 1300
    assert signer.verify("v1", exp, sig, now=1100)
    assert not signer.verify("v2", exp, sig, now=1100)          # 다른 영상
    assert not signer.verify("v1", exp + 1, sig, now=1100)      # 만료 시각 변조
    assert not signer.verify("v1", exp, sig[:-1] + "0", now=1100)
    assert not signer.verify("v1", exp, sig, now=1301)          # 만료


def test_store_rejects_path_traversal(tmp_path):
    (tmp_path / "secret.mp4").write_bytes(b"x")
    inner = tmp_path / "media"
    inner.mkdir()
    store = MediaStore(inner)
    for bad in ("../secret", "..\\secret", "a/b", "", ".hidden"):
        assert store.video_path(bad) is None


# --- 근거 재생 가능 여부 ---------------------------------------------------

def test_evidence_is_playable_only_when_the_video_exists(client, media_client):
    without = client.get(f"{COUNTER}/evidence/EV01").json()
    assert without["playable"] is False and without["access_url"] is None
    assert without["evidence"]["availability"] == "unavailable"

    with_media = media_client.get(f"{COUNTER}/evidence/EV01").json()
    assert with_media["playable"] is True and with_media["reason_code"] is None
    assert with_media["evidence"]["availability"] == "available"
    assert with_media["access_url"].startswith("/api/v1/media/demo-video-01?exp=")


# --- 영상 제공 -------------------------------------------------------------

def test_signed_url_serves_video_with_range_support(media_client):
    url = media_client.get(f"{COUNTER}/evidence/EV01").json()["access_url"]
    full = media_client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    assert "no-store" in full.headers["cache-control"]

    part = media_client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"].startswith("bytes 0-99/")


def test_media_rejects_missing_wrong_and_expired_signatures(media_client, media_app):
    url = media_client.get(f"{COUNTER}/evidence/EV01").json()["access_url"]
    exp = url.split("exp=")[1].split("&")[0]

    bad = media_client.get(f"/api/v1/media/demo-video-01?exp={exp}&sig=deadbeef")
    assert bad.status_code == 403 and bad.headers["content-type"].startswith(PROBLEM)
    assert bad.json()["code"] == "invalid_signature"

    assert media_client.get("/api/v1/media/demo-video-01").status_code == 422  # 서명 없음

    signer: UrlSigner = media_app.state.signer
    expired = signer.sign("demo-video-01", now=time.time() - 10_000)
    assert media_client.get(expired).status_code == 403

    other = signer.sign("demo-video-02")  # 다른 영상의 서명을 이 영상에 쓸 수 없다
    swapped = other.replace("demo-video-02", "demo-video-01")
    assert media_client.get(swapped).status_code == 403


# --- 영상 정보와 프레임 표 -------------------------------------------------

def test_video_info_cfr(media_client):
    body = media_client.get("/api/v1/videos/demo-video-01").json()
    assert (body["width"], body["height"]) == (320, 180)
    assert body["frame_count"] == 90 and body["is_vfr"] is False
    assert body["average_fps"] == 30.0


def test_video_info_vfr(media_client):
    body = media_client.get("/api/v1/videos/demo-video-02").json()
    assert body["is_vfr"] is True and body["frame_count"] == 30
    assert body["average_fps"] < 15  # 명목 fps(15)나 원본 fps(30)로 시각을 만들 수 없다


def test_frames_use_actual_presentation_times(media_client):
    cfr = media_client.get("/api/v1/videos/demo-video-01/frames", params={"from_us": 1_000_000, "to_us": 1_100_000}).json()
    assert [f["pts_us"] for f in cfr["frames"]] == [1_000_000, 1_033_333, 1_066_667]
    assert [f["index"] for f in cfr["frames"]] == [30, 31, 32]

    vfr = media_client.get("/api/v1/videos/demo-video-02/frames", params={"from_us": 0, "to_us": 500_000}).json()
    pts = [f["pts_us"] for f in vfr["frames"]]
    assert pts == sorted(pts)
    gaps = {b - a for a, b in zip(pts, pts[1:])}
    assert len(gaps) > 1  # 간격이 일정하지 않다


def test_frames_range_starts_with_the_frame_visible_at_from_us(media_client):
    body = media_client.get("/api/v1/videos/demo-video-01/frames", params={"from_us": 1_010_000, "to_us": 1_100_000}).json()
    assert body["frames"][0]["pts_us"] == 1_000_000  # 1.01초에 보이던 프레임


def test_frames_rejects_empty_range(media_client):
    r = media_client.get("/api/v1/videos/demo-video-01/frames", params={"from_us": 5, "to_us": 5})
    assert r.status_code == 422 and r.json()["code"] == "invalid_range"


# --- 정확한 프레임 이미지 --------------------------------------------------

def test_frame_image_is_the_decoder_identified_frame(media_client):
    r = media_client.get("/api/v1/videos/demo-video-01/frame-image", params={"time_us": 1_040_000})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    # 1.04초에 표시되는 프레임은 31번(1.033333초)이다.
    assert r.headers["x-frame-index"] == "31" and r.headers["x-frame-pts-us"] == "1033333"


def test_consecutive_frames_give_different_images(media_client):
    a = media_client.get("/api/v1/videos/demo-video-01/frame-image", params={"time_us": 1_000_000})
    b = media_client.get("/api/v1/videos/demo-video-01/frame-image", params={"time_us": 1_033_333})
    c = media_client.get("/api/v1/videos/demo-video-01/frame-image", params={"time_us": 1_040_000})
    assert a.headers["x-frame-index"] == "30" and b.headers["x-frame-index"] == "31"
    assert hashlib.md5(a.content).hexdigest() != hashlib.md5(b.content).hexdigest()
    assert b.content == c.content  # 같은 프레임

    # 서로 다른 프레임 여러 개가 모두 다른 이미지여야 한다(항상 같은 프레임을 뽑는 오류를 잡는다).
    times = [0, 400_000, 800_000, 1_200_000, 1_600_000, 2_000_000]
    digests = {hashlib.md5(media_client.get("/api/v1/videos/demo-video-01/frame-image", params={"time_us": t}).content).hexdigest() for t in times}
    assert len(digests) == len(times)


def test_frame_image_on_vfr_returns_the_earlier_frame_between_samples(media_client):
    frames = media_client.get("/api/v1/videos/demo-video-02/frames", params={"from_us": 0, "to_us": 1_000_000}).json()["frames"]
    first, second = frames[1], frames[2]
    mid = (first["pts_us"] + second["pts_us"]) // 2
    r = media_client.get("/api/v1/videos/demo-video-02/frame-image", params={"time_us": mid})
    assert r.headers["x-frame-pts-us"] == str(first["pts_us"])


# --- 관절 데이터 -----------------------------------------------------------

def test_poses_are_filtered_by_half_open_range_and_keep_missing_joints_null(media_client):
    body = media_client.get(f"{COUNTER}/poses", params={"from_us": 0, "to_us": 1_000_000}).json()
    assert body["available"] is True and body["landmark_set_id"] == "test-1"
    assert body["coordinate_space_id"] == "analysis_px:640x360" and (body["width"], body["height"]) == (320, 180)
    assert [s["time_us"] for s in body["samples"]] == [0, 33_333, 66_667]  # 끝(1.0초)은 제외
    joints = {j["name"]: j for j in body["samples"][0]["joints"]}
    assert len(joints) == 13  # 핵심 관절 13개
    missing = joints["right_ankle"]
    assert missing["x"] is None and missing["y"] is None and missing["state"] == "missing"
    # 분석 좌표(2배)가 표시 좌표로 되돌아온다
    assert (joints["nose"]["x"], joints["nose"]["y"], joints["nose"]["score"]) == (10.0, 20.0, 0.9)
    assert (joints["left_shoulder"]["x"], joints["left_shoulder"]["y"]) == (30.0, 40.0)
    assert body["connections"] == [["nose", "left_shoulder"], ["nose", "right_ankle"]]


def test_poses_unavailable_is_explicit(client, media_client):
    assert client.get(f"{COUNTER}/poses").json() == {
        "available": False, "reason_code": "no_pose_data", "landmark_set_id": None,
        "coordinate_space_id": None, "width": None, "height": None, "connections": [], "samples": [],
    }
    assert media_client.get(f"{SCENARIO6}/poses").json()["available"] is False  # 02는 관절 파일이 없다


# --- 접근 제어 -------------------------------------------------------------

def test_other_users_cannot_use_video_endpoints(media_app, media_client):
    media_app.dependency_overrides[current_user] = lambda: User(user_id="other-user")
    for path in (
        "/api/v1/videos/demo-video-01",
        "/api/v1/videos/demo-video-01/frames?from_us=0&to_us=1000",
        "/api/v1/videos/demo-video-01/frame-image?time_us=0",
    ):
        r = media_client.get(path)
        assert r.status_code == 404 and r.json()["code"] == "video_not_found", path
    assert media_client.get(f"{COUNTER}/evidence").status_code == 404


def test_unknown_video_and_missing_file_are_not_found(client, media_client):
    assert client.get("/api/v1/videos/demo-video-01").json()["code"] == "media_not_found"  # 실행은 있으나 파일이 없다
    assert media_client.get("/api/v1/videos/nope").json()["code"] == "video_not_found"
