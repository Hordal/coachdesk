"""작업 큐(상세 설계 12): 소유권 토큰, lease 만료 복구, 취소, 재시도 한도."""

from __future__ import annotations

import pytest

from app.jobs.store import JobStore, LeaseLost, overall_progress
from app.storage.db import Database

STAGES = [
    {"name": "a", "label": "A", "weight": 0.5, "status": "pending", "progress": 0.0, "message": None, "reason_code": None},
    {"name": "b", "label": "B", "weight": 0.5, "status": "pending", "progress": 0.0, "message": None, "reason_code": None},
    {"name": "info", "label": "안내", "weight": 0.0, "status": "unsupported", "progress": 0.0, "message": None, "reason_code": "stage_not_implemented"},
]


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "t.sqlite3")


@pytest.fixture()
def store(db, clock):
    return JobStore(db, lease_seconds=60, clock=clock)


def make_job(db, store, owner="u1", max_attempts=3):
    with db.tx(immediate=True) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO videos (video_id, owner_id, filename, sport, source_path, size_bytes, sha256, status, created_at) "
            "VALUES ('v1', ?, 'a.mp4', 'boxing', 'x', 1, 'h', 'processing', 't')", (owner,)
        )
        return store.create(conn, owner_id=owner, kind="ingest", video_id="v1", stages=[dict(s) for s in STAGES], max_attempts=max_attempts)


def test_weighted_progress_ignores_zero_weight_stages():
    stages = [dict(s) for s in STAGES]
    assert overall_progress(stages) == 0.0
    stages[0]["progress"] = 1.0
    assert overall_progress(stages) == 0.5
    stages[1]["progress"] = 0.5
    assert overall_progress(stages) == 0.75


def test_new_job_is_queued_with_pending_stages_and_an_unsupported_info_stage(db, store):
    job = store.get(make_job(db, store))
    assert job.status == "queued" and job.attempts == 0 and job.progress == 0.0
    assert [s["status"] for s in job.stages] == ["pending", "pending", "unsupported"]


def test_claim_takes_oldest_queued_job_and_gives_a_token(db, store):
    first, second = make_job(db, store), make_job(db, store)
    a = store.claim_next()
    assert a.job_id == first and a.attempts == 1 and a.record.status == "running"
    b = store.claim_next()
    assert b.job_id == second and b.token != a.token
    assert store.claim_next() is None


def test_only_the_token_holder_may_write(db, store):
    job_id = make_job(db, store)
    claimed = store.claim_next()
    store.set_stage(job_id, claimed.token, "a", status="running", progress=0.4)
    assert store.get(job_id).progress == 0.2
    with pytest.raises(LeaseLost):
        store.set_stage(job_id, "someone-else", "a", progress=1.0)
    with pytest.raises(LeaseLost):
        store.heartbeat(job_id, "someone-else")
    with pytest.raises(LeaseLost):
        store.complete(job_id, "someone-else", status="partial")
    store.complete(job_id, claimed.token, status="partial")
    job = store.get(job_id)
    assert job.status == "partial" and job.progress == 1.0 and job.finished_at
    with pytest.raises(LeaseLost):  # 끝난 작업에는 원래 토큰으로도 쓸 수 없다
        store.set_stage(job_id, claimed.token, "a", progress=0.1)


def test_unknown_stage_is_rejected(db, store):
    job_id = make_job(db, store)
    claimed = store.claim_next()
    with pytest.raises(KeyError):
        store.set_stage(job_id, claimed.token, "nope", progress=0.1)


def test_heartbeat_extends_the_lease(db, store, clock):
    job_id = make_job(db, store)
    claimed = store.claim_next()
    clock.now += 50
    store.heartbeat(job_id, claimed.token)
    clock.now += 50  # 처음 lease(60초)는 지났지만 heartbeat로 늘어났다
    assert store.recover_expired() == []
    clock.now += 20
    assert store.recover_expired() == [(job_id, "queued")]


def test_expired_lease_is_requeued_from_the_last_completed_stage(db, store, clock):
    job_id = make_job(db, store)
    claimed = store.claim_next()
    store.set_stage(job_id, claimed.token, "a", status="completed", progress=1.0)
    store.set_stage(job_id, claimed.token, "b", status="running", progress=0.6)
    clock.now += 61
    assert store.recover_expired() == [(job_id, "queued")]
    job = store.get(job_id)
    assert job.status == "queued"
    assert [s["status"] for s in job.stages] == ["completed", "pending", "unsupported"]  # 완성한 단계는 유지
    assert job.stages[1]["progress"] == 0.0 and job.progress == 0.5

    again = store.claim_next()
    assert again.attempts == 2 and again.token != claimed.token
    with pytest.raises(LeaseLost):  # 이전 워커의 늦은 쓰기는 거부된다
        store.set_stage(job_id, claimed.token, "b", status="completed", progress=1.0)


def test_attempts_are_limited(db, store, clock):
    job_id = make_job(db, store, max_attempts=2)
    for expected in ("queued", "failed"):
        store.claim_next()
        clock.now += 61
        assert store.recover_expired() == [(job_id, expected)]
    job = store.get(job_id)
    assert job.status == "failed" and job.attempts == 2
    assert job.error["code"] == "retries_exhausted" and job.error["retryable"] is False
    assert job.error["last_code"] == "lease_expired"


def test_release_for_retry_requeues_until_attempts_run_out(db, store):
    job_id = make_job(db, store, max_attempts=2)
    err = {"code": "io_error", "title": "t", "stage": "a", "retryable": True}
    first = store.claim_next()
    assert store.release_for_retry(job_id, first.token, err) == "queued"
    second = store.claim_next()
    assert store.release_for_retry(job_id, second.token, err) == "failed"
    assert store.get(job_id).error["code"] == "retries_exhausted"


def test_cancel_queued_job_is_immediate_and_running_job_gets_a_flag(db, store):
    queued = make_job(db, store)
    assert store.request_cancel(queued, "u1").status == "cancelled"
    assert store.claim_next() is None

    running = make_job(db, store)
    claimed = store.claim_next()
    job = store.request_cancel(running, "u1")
    assert job.status == "running" and job.cancel_requested is True
    assert store.cancel_requested(running) is True
    store.complete(running, claimed.token, status="cancelled")
    assert store.get(running).status == "cancelled"


def test_cancel_after_finish_changes_nothing(db, store):
    job_id = make_job(db, store)
    claimed = store.claim_next()
    store.complete(job_id, claimed.token, status="partial")
    assert store.request_cancel(job_id, "u1").status == "partial"


def test_other_owner_cannot_see_or_cancel(db, store):
    job_id = make_job(db, store, owner="u1")
    assert store.get_for_owner(job_id, "u2") is None
    assert store.request_cancel(job_id, "u2") is None
    assert store.get(job_id).status == "queued"


def test_events_record_the_job_history(db, store):
    job_id = make_job(db, store)
    claimed = store.claim_next()
    store.set_stage(job_id, claimed.token, "a", status="running")
    store.complete(job_id, claimed.token, status="partial")
    names = [e["event"] for e in store.events(job_id)]
    assert names == ["created", "claimed", "stage_running", "job_partial"]


def test_cancel_requested_job_with_expired_lease_ends_cancelled(db, store, clock):
    job_id = make_job(db, store)
    store.claim_next()
    store.request_cancel(job_id, "u1")
    clock.now += 61
    assert store.recover_expired() == [(job_id, "cancelled")]
    assert store.get(job_id).status == "cancelled"
