"""영상 준비 작업과 워커(상세 설계 05, 12, 13, 17)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.jobs.runner import FfmpegRunner, MediaDecodeError, StopRequested
from app.jobs.store import JobStore
from app.jobs.worker import Worker
from app.media import binaries
from app.settings import Settings
from app.storage.db import Database

from .fakes import FakeRunner
from .helpers import make_video, upload_all

pytestmark = pytest.mark.skipif(not binaries.available(), reason="FFmpeg를 찾을 수 없음")


@pytest.fixture(scope="module")
def cfr_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("cfr") / "clip.mp4"
    make_video(path, extra=["-preset", "veryfast", "-crf", "30", "-movflags", "+faststart"])
    return path.read_bytes()


@pytest.fixture(scope="module")
def vfr_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("vfr") / "clip.mp4"
    make_video(path, vfr=True, extra=["-video_track_timescale", "90000", "-preset", "veryfast", "-crf", "30", "-movflags", "+faststart"])
    return path.read_bytes()


def settings_for(tmp_path, **overrides) -> Settings:
    base = dict(media_dir=tmp_path / "no-media", media_secret="s", data_dir=tmp_path / "data", upload_part_size=64 * 1024)
    base.update(overrides)
    return Settings(**base)


def make_env(tmp_path, data: bytes, **overrides) -> SimpleNamespace:
    settings = settings_for(tmp_path, **overrides)
    app = create_app(settings)
    client = TestClient(app)
    done = upload_all(client, data)
    return SimpleNamespace(settings=settings, app=app, client=client, job_id=done["job_id"], video_id=done["video_id"], tmp=tmp_path)


def job_of(env) -> dict:
    return env.client.get(f"/api/v1/jobs/{env.job_id}").json()


def stage_status(job: dict) -> dict[str, str]:
    return {s["name"]: s["status"] for s in job["stages"]}


def video_of(env) -> dict:
    return env.client.get(f"/api/v1/videos/{env.video_id}/summary").json()


# --- 실제 FFmpeg로 끝까지 -----------------------------------------------------------

def test_end_to_end_with_real_ffmpeg(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    assert Worker(env.settings).run_until_idle() == 1

    job = job_of(env)
    assert job["status"] == "partial" and job["progress"] == 1.0 and job["attempts"] == 1 and job["error"] is None
    assert stage_status(job) == {
        "decode_check": "completed", "playback_copy": "completed", "frame_table": "completed",
        "register": "completed", "analysis": "unsupported",
    }
    frame_stage = next(s for s in job["stages"] if s["name"] == "frame_table")
    assert "프레임 90개" in frame_stage["message"] and "고정 프레임률" in frame_stage["message"] and "시각 대응 확인" in frame_stage["message"]

    video = video_of(env)
    assert video["status"] == "ready" and video["run_id"] == job["run_id"]
    playback = tmp_path / "data" / "media" / f"{env.video_id}.mp4"
    assert playback.is_file() and not list((tmp_path / "data" / "media").glob("*.tmp"))

    # 결과 화면이 같은 방식으로 열린다: 분석 결과 없음, 영상 재생 가능
    run = env.client.get(f"/api/v1/runs/{job['run_id']}").json()
    assert run["dataset_kind"] == "unanalyzed" and run["status"] == "partial" and run["mode"] == "full"
    assert env.client.get(f"/api/v1/runs/{job['run_id']}/findings").json() == []
    assert env.client.get(f"/api/v1/runs/{job['run_id']}/evidence").json() == []
    assert job["run_id"] in [r["run_id"] for r in env.client.get("/api/v1/runs").json()]

    play = env.client.get(f"/api/v1/videos/{env.video_id}/playback").json()
    assert play["playable"] is True and play["access_url"].startswith(f"/api/v1/media/{env.video_id}?exp=")
    assert env.client.get(play["access_url"]).status_code == 200

    info = env.client.get(f"/api/v1/videos/{env.video_id}").json()
    assert info["frame_count"] == 90 and info["is_vfr"] is False and (info["width"], info["height"]) == (320, 180)
    frames = env.client.get(f"/api/v1/videos/{env.video_id}/frames", params={"from_us": 1_000_000, "to_us": 1_100_000}).json()["frames"]
    assert [f["pts_us"] for f in frames] == [1_000_000, 1_033_333, 1_066_667]
    assert env.client.get(f"/api/v1/videos/{env.video_id}/frame-image", params={"time_us": 1_040_000}).headers["x-frame-index"] == "31"


def test_variable_frame_rate_keeps_every_frame_time(tmp_path, vfr_bytes):
    env = make_env(tmp_path, vfr_bytes)
    Worker(env.settings).run_until_idle()
    job = job_of(env)
    assert job["status"] == "partial"
    message = next(s for s in job["stages"] if s["name"] == "frame_table")["message"]
    assert "프레임 30개" in message and "가변 프레임률" in message
    assert env.client.get(f"/api/v1/videos/{env.video_id}").json()["is_vfr"] is True


def test_playback_copy_waits_for_the_worker(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    assert env.client.get(f"/api/v1/videos/{env.video_id}/playback").json() == {"playable": False, "access_url": None, "reason_code": "evidence_unavailable"}
    assert video_of(env)["status"] == "processing"


# --- 실제 처리기 -------------------------------------------------------------------

def test_real_runner_reports_progress_and_decodes(tmp_path, cfr_bytes):
    source = tmp_path / "in.mp4"
    source.write_bytes(cfr_bytes)
    seen: list[float] = []
    runner = FfmpegRunner()
    runner.decode_check(source, 3_000_000, lambda f, _u: seen.append(f) if f is not None else None, lambda: False)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)
    out = tmp_path / "out.mp4.tmp"
    runner.transcode(source, out, 3_000_000, lambda f, _u: None, lambda: False)
    assert out.stat().st_size > 0


def test_real_runner_flags_unreadable_video(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all" * 100)
    with pytest.raises(MediaDecodeError):
        FfmpegRunner().decode_check(bad, None, lambda f, u: None, lambda: False)


def test_real_runner_stops_when_asked(tmp_path, cfr_bytes):
    source = tmp_path / "in.mp4"
    source.write_bytes(cfr_bytes)
    with pytest.raises(StopRequested):
        FfmpegRunner().transcode(source, tmp_path / "o.mp4.tmp", 3_000_000, lambda f, u: None, lambda: True)


# --- 오류: 영구 / 일시적 -------------------------------------------------------------

def test_permanent_decode_error_fails_without_retry_and_hides_internals(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    runner.decode_error = MediaDecodeError(f"{tmp_path}\\secret.mp4: Invalid data", 5_000_000)
    Worker(env.settings, runner=runner).run_until_idle()

    job = job_of(env)
    assert job["status"] == "failed" and job["attempts"] == 1  # 자동 재시도하지 않는다
    assert job["error"]["code"] == "decode_error" and job["error"]["at_us"] == 5_000_000
    assert job["error"]["retryable"] is False and job["error"]["trace_id"] and job["error"]["stage"] == "decode_check"
    assert stage_status(job)["decode_check"] == "failed"
    assert video_of(env)["status"] == "failed" and video_of(env)["run_id"] is None
    assert str(tmp_path) not in json.dumps(job, ensure_ascii=False) and "secret" not in json.dumps(job)


def test_unexpected_error_does_not_leak_details(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    runner.unexpected = RuntimeError(f"boom at {tmp_path}")
    Worker(env.settings, runner=runner).run_until_idle()
    job = job_of(env)
    assert job["status"] == "failed" and job["error"]["code"] == "internal_error"
    assert "boom" not in json.dumps(job) and str(tmp_path) not in json.dumps(job)


def test_transient_errors_are_retried_from_the_last_completed_stage(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    runner.transcode_failures = 2
    worker = Worker(env.settings, runner=runner)
    assert worker.run_until_idle() == 3  # 처음 시도 + 재시도 2회

    job = job_of(env)
    assert job["status"] == "partial" and job["attempts"] == 3
    assert runner.calls.count("decode") == 1  # 완성한 단계는 다시 하지 않는다
    assert runner.calls.count("transcode") == 3
    events = [e["event"] for e in worker.jobs.events(env.job_id)]
    assert events.count("requeued") == 2 and events.count("claimed") == 3


def test_retries_are_limited_to_two(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    runner.transcode_failures = 10
    Worker(env.settings, runner=runner).run_until_idle()
    job = job_of(env)
    assert job["status"] == "failed" and job["attempts"] == 3
    assert job["error"]["code"] == "retries_exhausted" and job["error"]["retryable"] is False
    assert video_of(env)["status"] == "failed"
    assert not list((tmp_path / "data" / "media").glob("*")) if (tmp_path / "data" / "media").exists() else True


# --- 취소 -----------------------------------------------------------------------

def test_cancel_while_queued_is_immediate(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    r = env.client.post(f"/api/v1/jobs/{env.job_id}/cancel")
    assert r.json()["status"] == "cancelled"
    assert Worker(env.settings, runner=FakeRunner()).run_once() is False  # 처리할 작업이 없다


def test_cancel_during_processing_stops_the_process_and_leaves_no_partial_output(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    runner.block_transcode = True
    worker = Worker(env.settings, runner=runner)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert runner.transcode_started.wait(10)

    assert env.client.post(f"/api/v1/jobs/{env.job_id}/cancel").json()["cancel_requested"] is True
    thread.join(10)
    assert not thread.is_alive() and runner.saw_stop

    job = job_of(env)
    assert job["status"] == "cancelled" and job["run_id"] is None
    assert video_of(env)["status"] == "cancelled" and video_of(env)["run_id"] is None
    assert not list((tmp_path / "data" / "media").glob("*")) if (tmp_path / "data" / "media").exists() else True


def test_cancel_after_completion_changes_nothing(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    Worker(env.settings, runner=FakeRunner()).run_until_idle()
    assert env.client.post(f"/api/v1/jobs/{env.job_id}/cancel").json()["status"] == "partial"


# --- 중단 복구와 소유권 --------------------------------------------------------------

def expire_leases(env) -> None:
    with Database(env.settings.db_path).tx(immediate=True) as conn:
        conn.execute("UPDATE jobs SET lease_expires_at = 0 WHERE status = 'running'")


def test_a_crashed_worker_is_recovered_from_its_last_checkpoint(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    db = Database(env.settings.db_path)
    dead = JobStore(db, lease_seconds=60)
    claimed = dead.claim_next()  # 작업을 가져간 뒤 갑자기 죽은 워커
    dead.set_stage(env.job_id, claimed.token, "decode_check", status="completed", progress=1.0, message="이미 끝남")
    dead.set_stage(env.job_id, claimed.token, "playback_copy", status="running", progress=0.3)
    assert job_of(env)["status"] == "running"
    expire_leases(env)

    runner = FakeRunner()
    assert Worker(env.settings, runner=runner).run_once() is True
    job = job_of(env)
    assert job["status"] == "partial" and job["attempts"] == 2
    assert "decode" not in runner.calls and "transcode" in runner.calls  # 마지막 완성 단계부터 다시 시작


def test_a_worker_that_lost_its_lease_cannot_finish_the_job(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    runner.block_transcode = True
    slow = Worker(env.settings, runner=runner)
    thread = threading.Thread(target=slow.run_once)
    thread.start()
    assert runner.transcode_started.wait(10)

    expire_leases(env)  # 이 워커가 느려서 lease가 만료됨
    other = JobStore(Database(env.settings.db_path), lease_seconds=60)
    assert other.recover_expired() == [(env.job_id, "queued")]
    runner.release.set()
    thread.join(10)

    job = job_of(env)
    assert job["status"] == "queued" and job["attempts"] == 1  # 늦게 끝난 워커는 아무것도 쓰지 못했다
    assert job["run_id"] is None

    assert Worker(env.settings, runner=FakeRunner()).run_once() is True
    job = job_of(env)
    assert job["status"] == "partial" and job["attempts"] == 2  # 새 워커가 이어서 끝냈다


def test_heartbeat_keeps_the_lease_during_a_long_step(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes, heartbeat_seconds=0.05, lease_seconds=0.4)
    runner = FakeRunner()
    runner.block_seconds = 1.0
    worker = Worker(env.settings, runner=runner)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert runner.transcode_started.wait(10)

    time.sleep(0.8)  # 처음 lease(0.4초)보다 오래 지났다
    watcher = JobStore(Database(env.settings.db_path), lease_seconds=0.4)
    assert watcher.recover_expired() == []  # heartbeat가 lease를 늘렸으므로 복구 대상이 아니다
    thread.join(15)
    assert job_of(env)["status"] == "partial" and job_of(env)["attempts"] == 1


def test_without_heartbeat_the_same_long_step_is_recovered(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes, heartbeat_seconds=60, lease_seconds=0.3)
    runner = FakeRunner()
    runner.block_seconds = 1.0
    worker = Worker(env.settings, runner=runner)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert runner.transcode_started.wait(10)
    time.sleep(0.6)
    watcher = JobStore(Database(env.settings.db_path), lease_seconds=0.3)
    assert watcher.recover_expired() == [(env.job_id, "queued")]
    thread.join(15)
    assert job_of(env)["status"] == "queued"  # 소유권을 잃은 워커는 작업을 끝내지 못한다


def test_fast_heartbeat_notices_a_lost_lease_and_stops_the_process(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes, heartbeat_seconds=0.05, lease_seconds=60)
    runner = FakeRunner()
    runner.block_transcode = True
    worker = Worker(env.settings, runner=runner)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert runner.transcode_started.wait(10)

    # 만료 시각을 먼저 덮어쓰면 heartbeat가 복구 직전에 다시 연장할 수 있다.
    # 복구자의 시계를 앞으로 보내 실제 소유권 회수가 일어나도록 한다.
    recovered = JobStore(Database(env.settings.db_path), lease_seconds=60, clock=lambda: time.time() + 120).recover_expired()
    assert recovered == [(env.job_id, "queued")]
    thread.join(10)  # release 없이도 heartbeat가 소유권 상실을 알아채 하위 프로세스를 멈춘다
    assert not thread.is_alive() and runner.saw_stop
    assert job_of(env)["status"] == "queued"


def test_registering_twice_does_not_create_two_runs(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    worker = Worker(env.settings, runner=FakeRunner())
    worker.run_until_idle()
    first = job_of(env)

    # 결과를 등록한 뒤 작업 기록만 되돌려, 등록 단계가 다시 실행되는 상황을 만든다.
    with Database(env.settings.db_path).tx(immediate=True) as conn:
        row = conn.execute("SELECT stages_json FROM jobs WHERE job_id = ?", (env.job_id,)).fetchone()
        stages = json.loads(row["stages_json"])
        for s in stages:
            if s["name"] == "register":
                s["status"], s["progress"] = "pending", 0.0
        conn.execute("UPDATE jobs SET status = 'queued', stages_json = ?, run_id = NULL, finished_at = NULL, max_attempts = 5 WHERE job_id = ?", (json.dumps(stages), env.job_id))
    worker.run_until_idle()

    again = job_of(env)
    assert again["status"] == "partial" and again["run_id"] == first["run_id"]
    assert sum(1 for r in env.client.get("/api/v1/runs").json() if r["video_id"] == env.video_id) == 1


def test_progress_only_moves_forward_and_never_promises_a_finish_time(tmp_path, cfr_bytes):
    env = make_env(tmp_path, cfr_bytes)
    runner = FakeRunner()
    seen: list[float] = []
    worker = Worker(env.settings, runner=runner)
    original = worker.jobs.set_stage

    def spy(*args, **kwargs):
        original(*args, **kwargs)
        seen.append(job_of(env)["progress"])

    worker.jobs.set_stage = spy  # type: ignore[method-assign]
    worker.run_until_idle()
    assert seen == sorted(seen) and seen[-1] > 0.9
    assert not any("eta" in key or "remaining" in key for key in job_of(env))  # 완료 예정 시간을 만들지 않는다
