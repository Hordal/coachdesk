"""외부 모델 실행 계약. 서버는 큐·소유권·검증·결과 저장을 담당하고 모델을 흉내 내지 않는다."""
import hashlib
import json
import secrets
import time
import uuid
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field

from ...domain.models import ManualImport, ObservationImport, PoseImport, Strict, TrackAssignmentSet
from ...services.designation import DesignationRepo
from ...services.imports import ImportRejected, import_manual
from ...storage.db import now_rfc3339
from ..deps import User, current_user
from ..errors import ProblemError
from .intake import CandidateEvent
from .observations import _ready_video, import_observations
from .poses import import_poses

router = APIRouter(tags=["analysis-workers"])
LEASE_SECONDS = 120


@router.get("/analysis/local-status")
def local_status(request: Request, user: User = Depends(current_user)) -> dict:
    from ...services.local_model import installed, MODEL_NAME, ENGINE_VERSION
    from ...services.sample_video import sample_ready
    with request.app.state.db.tx() as conn:
        active=conn.execute("SELECT COUNT(*) FROM local_engine_presence WHERE seen_at>?",(time.time()-45,)).fetchone()[0]
    ready=installed()
    return {"installed":ready,"running":bool(active),"name":MODEL_NAME,"version":ENGINE_VERSION,
            "supports":["person_tracks","pose_33","punch_candidates"],"trained_action_classifier":False,
            "available":ready and bool(active), "sample_ready":sample_ready()}


@router.get("/analysis/sample-video")
def download_sample(user: User = Depends(current_user)):
    from ...services.sample_video import sample_path, sample_ready
    if not sample_ready():
        raise ProblemError(404, "sample_not_ready", "테스트 영상을 준비해 주세요. setup_analysis.py를 실행하면 받을 수 있습니다")
    return FileResponse(sample_path(), media_type="video/mp4", filename="coachdesk-shadow-boxing.mp4")


@router.post("/analysis/tasks/{task_id}/use-local")
def use_local(task_id: str, request: Request, user: User = Depends(current_user)) -> dict:
    from ...services.local_model import installed
    if not installed():
        raise ProblemError(409,"local_engine_unavailable","내장 분석을 먼저 설치해 주세요")
    with request.app.state.db.tx(immediate=True) as conn:
        row=conn.execute("SELECT * FROM analysis_tasks WHERE task_id=? AND owner_id=?",(task_id,user.owner_id)).fetchone()
        if row is None:
            raise ProblemError(404,"task_not_found","분석 작업을 찾을 수 없습니다")
        if row["status"]!="queued":
            raise ProblemError(409,"task_not_queued","대기 중인 작업만 내장 분석으로 전환할 수 있습니다")
        conn.execute("UPDATE analysis_tasks SET engine='local',message='내장 분석 대기',updated_at=? WHERE task_id=?",(now_rfc3339(),task_id))
    return {"status":"queued","engine":"local"}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class KeyInput(Strict):
    label: str = Field(min_length=1, max_length=80)


class TaskInput(Strict):
    kind: Literal["tracking", "actions"]
    request_key: str = Field(min_length=8, max_length=100)
    engine: Literal["external", "local"] = "external"


class Claim(Strict):
    kinds: list[Literal["tracking", "actions"]] = Field(default_factory=lambda: ["tracking", "actions"], min_length=1)


class Lease(Strict):
    lease: str = Field(min_length=20, max_length=200)


class Heartbeat(Lease):
    progress: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    message: str = Field(default="분석 중", max_length=300)


class Failure(Lease):
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False


class Result(Lease):
    source: str = Field(min_length=1, max_length=200)
    definition_version: str = Field(default="phase1b_v1", min_length=1, max_length=100)
    observations: ObservationImport | None = None
    poses: PoseImport | None = None
    events: list[CandidateEvent] | None = Field(default=None, max_length=100_000)


def _key(request: Request) -> dict:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(401, "worker_auth_required", "분석 워커 인증이 필요합니다")
    with request.app.state.db.tx(immediate=True) as conn:
        row = conn.execute("SELECT * FROM analysis_keys WHERE token_hash=? AND revoked_at IS NULL", (digest(token),)).fetchone()
        if row is None:
            raise ProblemError(401, "worker_key_invalid", "분석 워커 키가 없거나 폐기되었습니다")
        conn.execute("UPDATE analysis_keys SET last_seen=? WHERE key_id=?", (time.time(), row["key_id"]))
    return dict(row)


def _public(row) -> dict:
    value = dict(row)
    for key in ("lease_hash", "key_id", "assignment_json", "request_key"):
        value.pop(key, None)
    value["result"] = json.loads(value.pop("result_json") or "null")
    return value


def _valid(conn, task_id: str, key: dict, lease: str):
    row = conn.execute("SELECT a.* FROM analysis_tasks a JOIN videos v USING(video_id) JOIN analysis_keys k ON k.key_id=a.key_id WHERE a.task_id=? AND a.owner_id=? AND v.deleting=0 AND v.media_state='present' AND k.revoked_at IS NULL", (task_id, key["owner_id"])).fetchone()
    if row is None or row["key_id"] != key["key_id"] or row["status"] != "running" or row["lease_until"] <= time.time() or not secrets.compare_digest(row["lease_hash"] or "", digest(lease)):
        raise ProblemError(409, "lease_lost", "작업 소유권이 만료되거나 취소되었습니다")
    return row


def _recover(conn, owner: str):
    now = time.time()
    conn.execute("UPDATE analysis_tasks SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END, lease_hash=NULL, lease_until=NULL, error='워커 연결이 끊겨 재시도 대기', updated_at=? WHERE owner_id=? AND status='running' AND lease_until<?", (now_rfc3339(), owner, now))


@router.get("/analysis/keys")
def keys(request: Request, user: User = Depends(current_user)) -> list[dict]:
    if user.workspace_role != "owner":
        raise ProblemError(403, "owner_required", "연결 키 관리는 공간 소유자만 가능합니다")
    with request.app.state.db.tx() as conn:
        return [dict(r) for r in conn.execute("SELECT key_id,label,created_at,revoked_at,last_seen FROM analysis_keys WHERE owner_id=? AND internal=0 ORDER BY created_at DESC", (user.owner_id,))]


@router.get("/analysis/worker-script")
def worker_script(user: User = Depends(current_user)):
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parents[3]/"scripts"/"analysis_worker.py",filename="analysis_worker.py",media_type="text/plain")


@router.get("/analysis/result-contract")
def result_contract(response: Response, user: User = Depends(current_user)) -> dict:
    response.headers["Content-Disposition"]='attachment; filename="worker-result.schema.json"'
    return {"$schema":"https://json-schema.org/draft/2020-12/schema",**Result.model_json_schema()}


@router.post("/analysis/keys", status_code=201)
def new_key(body: KeyInput, request: Request, user: User = Depends(current_user)) -> dict:
    if user.workspace_role != "owner":
        raise ProblemError(403, "owner_required", "연결 키 관리는 공간 소유자만 가능합니다")
    if not body.label.strip():
        raise ProblemError(422,"label_required","연결 이름을 입력해 주세요")
    token, kid = "coach_" + secrets.token_urlsafe(36), uuid.uuid4().hex
    with request.app.state.db.tx(immediate=True) as conn:
        if conn.execute("SELECT COUNT(*) FROM analysis_keys WHERE owner_id=? AND revoked_at IS NULL AND internal=0", (user.owner_id,)).fetchone()[0] >= 20:
            raise ProblemError(409, "key_limit", "활성 연결 키가 너무 많습니다")
        conn.execute("INSERT INTO analysis_keys(key_id,owner_id,label,token_hash,created_at) VALUES(?,?,?,?,?)", (kid, user.owner_id, body.label.strip(), digest(token), now_rfc3339()))
    return {"key_id": kid, "token": token, "label": body.label}


@router.delete("/analysis/keys/{key_id}", status_code=204)
def revoke_key(key_id: str, request: Request, user: User = Depends(current_user)) -> None:
    if user.workspace_role != "owner":
        raise ProblemError(403, "owner_required", "공간 소유자만 키를 폐기할 수 있습니다")
    with request.app.state.db.tx(immediate=True) as conn:
        conn.execute("UPDATE analysis_keys SET revoked_at=? WHERE key_id=? AND owner_id=?", (now_rfc3339(), key_id, user.owner_id))
        conn.execute("UPDATE analysis_tasks SET status='failed',lease_hash=NULL,error='연결 키 폐기',updated_at=? WHERE key_id=? AND owner_id=? AND status='running'", (now_rfc3339(), key_id, user.owner_id))


@router.get("/analysis/tasks")
def tasks(request: Request, video_id: str | None = None, user: User = Depends(current_user)) -> list[dict]:
    with request.app.state.db.tx(immediate=True) as conn:
        _recover(conn, user.owner_id)
        rows = conn.execute("SELECT a.* FROM analysis_tasks a JOIN videos v USING(video_id) WHERE a.owner_id=? AND v.deleting=0 AND (? IS NULL OR a.video_id=?) ORDER BY a.created_at DESC LIMIT 100", (user.owner_id, video_id, video_id)).fetchall()
    return [_public(r) for r in rows]


@router.post("/videos/{video_id}/analysis", status_code=201)
def enqueue(video_id: str, body: TaskInput, request: Request, response: Response, user: User = Depends(current_user)) -> dict:
    state = request.app.state
    if body.engine == "local":
        from ...services.local_model import installed
        if not installed():
            raise ProblemError(409,"local_engine_unavailable","내장 분석을 먼저 준비해 주세요. setup_analysis.py 실행이 필요합니다")
    video = _ready_video(video_id, user, state.videos)
    if body.engine == "local" and (video.duration_us or 0)>180_000_000:
        raise ProblemError(422,"local_clip_too_long","내장 시험 분석은 3분 이하 구간으로 나누어 사용해 주세요")
    if video.media_state != "present":
        raise ProblemError(409, "media_missing", "원본 영상이 없어 분석을 요청할 수 없습니다")
    assignment, set_id = state.designations.current(video_id, user.owner_id)
    if body.kind == "actions" and assignment.state != "ASSIGNED":
        raise ProblemError(409, "designation_required", "먼저 검출·추적 결과에서 분석 선수와 상대를 지정해 주세요")
    tid, now = uuid.uuid4().hex, now_rfc3339()
    with state.db.tx(immediate=True) as conn:
        _recover(conn, user.owner_id)
        prior = conn.execute("SELECT * FROM analysis_tasks WHERE owner_id=? AND request_key=?", (user.owner_id, body.request_key)).fetchone()
        if prior:
            if prior["video_id"] != video_id or prior["kind"] != body.kind or prior["engine"] != body.engine:
                raise ProblemError(409, "idempotency_conflict", "같은 요청 키에 다른 작업을 지정할 수 없습니다")
            response.status_code = 200
            return _public(prior)
        if conn.execute("SELECT 1 FROM analysis_tasks WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone():
            raise ProblemError(409, "task_active", "이 영상의 분석 작업이 이미 진행 중입니다")
        conn.execute("INSERT INTO analysis_tasks(task_id,owner_id,video_id,kind,status,request_key,assignment_json,observation_set_id,created_at,updated_at,engine) VALUES(?,?,?,?,'queued',?,?,?,?,?,?)", (tid, user.owner_id, video_id, body.kind, body.request_key, assignment.model_dump_json(), set_id, now, now, body.engine))
        conn.execute("INSERT INTO workspace_activity(owner_id,actor,action,target_id,at) VALUES(?,?,?,?,?)", (user.owner_id, user.display_name or user.user_id, "analysis_requested", tid, now))
        return _public(conn.execute("SELECT * FROM analysis_tasks WHERE task_id=?", (tid,)).fetchone())


@router.post("/analysis/tasks/{task_id}/cancel")
def cancel(task_id: str, request: Request, user: User = Depends(current_user)) -> dict:
    with request.app.state.db.tx(immediate=True) as conn:
        row = conn.execute("SELECT * FROM analysis_tasks WHERE task_id=? AND owner_id=?", (task_id, user.owner_id)).fetchone()
        if row is None:
            raise ProblemError(404, "task_not_found", "분석 작업을 찾을 수 없습니다")
        conn.execute("UPDATE analysis_tasks SET status='cancelled',lease_hash=NULL,updated_at=? WHERE task_id=? AND status IN ('queued','running')", (now_rfc3339(), task_id))
    return {"status": "ok"}


@router.post("/worker/claim")
def claim(body: Claim, request: Request, key: dict = Depends(_key)) -> dict:
    return claim_for_engine(body,request,key,"external")


def claim_for_engine(body: Claim, request: Request, key: dict, engine: str) -> dict:
    token, now = secrets.token_urlsafe(32), now_rfc3339()
    with request.app.state.db.tx(immediate=True) as conn:
        _recover(conn, key["owner_id"])
        placeholders = ",".join("?" for _ in body.kinds)
        row = conn.execute(f"SELECT a.* FROM analysis_tasks a JOIN videos v USING(video_id) WHERE a.owner_id=? AND a.engine=? AND a.status='queued' AND a.kind IN ({placeholders}) AND v.deleting=0 AND v.media_state='present' ORDER BY a.created_at LIMIT 1", (key["owner_id"], engine, *body.kinds)).fetchone()
        if row is None:
            return {"task": None}
        conn.execute("UPDATE analysis_tasks SET status='running',lease_hash=?,lease_until=?,key_id=?,attempts=attempts+1,progress=0,error=NULL,updated_at=? WHERE task_id=?", (digest(token), time.time() + LEASE_SECONDS, key["key_id"], now, row["task_id"]))
        video = conn.execute("SELECT video_id,filename,sport,duration_us,width,height,sha256 FROM videos WHERE video_id=?", (row["video_id"],)).fetchone()
    return {"task": {"task_id": row["task_id"], "kind": row["kind"], "video": dict(video), "lease": token,
                     "lease_seconds": LEASE_SECONDS, "assignment": json.loads(row["assignment_json"]), "observation_set_id": row["observation_set_id"], "engine": engine,
                     "input_path": f"/api/v1/worker/tasks/{row['task_id']}/input"}}


@router.get("/worker/tasks/{task_id}/input")
def input_video(task_id: str, request: Request, key: dict = Depends(_key)):
    lease = request.headers.get("X-Analysis-Lease", "")
    with request.app.state.db.tx() as conn:
        row = _valid(conn, task_id, key, lease)
        video = conn.execute("SELECT source_path FROM videos WHERE video_id=?", (row["video_id"],)).fetchone()
    from pathlib import Path
    path = Path(video["source_path"]).resolve()
    if not path.is_relative_to(request.app.state.settings.originals_dir.resolve()) or not path.is_file():
        raise ProblemError(410, "source_missing", "원본 파일이 없습니다")
    return FileResponse(path, filename=path.name)


@router.post("/worker/tasks/{task_id}/heartbeat")
def heartbeat(task_id: str, body: Heartbeat, request: Request, key: dict = Depends(_key)) -> dict:
    with request.app.state.db.tx(immediate=True) as conn:
        _valid(conn, task_id, key, body.lease)
        conn.execute("UPDATE analysis_tasks SET lease_until=?,progress=?,message=?,updated_at=? WHERE task_id=?", (time.time() + LEASE_SECONDS, body.progress, body.message, now_rfc3339(), task_id))
    return {"lease_seconds": LEASE_SECONDS}


@router.post("/worker/tasks/{task_id}/fail")
def fail(task_id: str, body: Failure, request: Request, key: dict = Depends(_key)) -> dict:
    with request.app.state.db.tx(immediate=True) as conn:
        row = _valid(conn, task_id, key, body.lease)
        status = "queued" if body.retryable and row["attempts"] < 3 else "failed"
        conn.execute("UPDATE analysis_tasks SET status=?,lease_hash=NULL,lease_until=NULL,error=?,updated_at=? WHERE task_id=?", (status, body.message, now_rfc3339(), task_id))
    return {"status": status}


@router.post("/worker/tasks/{task_id}/result")
def result(task_id: str, body: Result, request: Request, key: dict = Depends(_key)) -> dict:
    state = request.app.state
    payload_digest = digest(body.model_dump_json(exclude={"lease"}))
    with state.db.tx() as conn:
        previous = conn.execute("SELECT * FROM analysis_tasks WHERE task_id=? AND owner_id=?", (task_id, key["owner_id"])).fetchone()
        if previous and previous["status"] == "completed" and previous["key_id"] == key["key_id"]:
            saved = json.loads(previous["result_json"])
            if saved["digest"] != payload_digest:
                raise ProblemError(409, "result_conflict", "이미 저장한 결과와 내용이 다릅니다")
            return saved
        row = dict(_valid(conn, task_id, key, body.lease))
    if (row["kind"] == "tracking" and (body.observations is None or body.events is not None or body.poses is not None)) or (row["kind"] == "actions" and (body.events is None or body.observations is not None)):
        raise ProblemError(422, "result_kind_mismatch", "tracking 작업은 observations, actions 작업은 events와 선택 poses를 보내야 합니다")
    assignment = TrackAssignmentSet.model_validate_json(row["assignment_json"])
    if row["kind"] == "actions":
        current, set_id = state.designations.current(row["video_id"], row["owner_id"])
        if set_id != row["observation_set_id"] or current != assignment:
            raise ProblemError(409, "stale_designation", "작업 중 선수 지정이 변경되었습니다. 작업을 취소하고 다시 요청해 주세요")
    # 기존 파일 입력과 동일한 PTS·좌표·계약 검증을 적용한다.
    worker_user = User(user_id=row["owner_id"])
    video = _ready_video(row["video_id"], worker_user, state.videos)
    saved = {"digest": payload_digest, "run_id": None, "observation_set_id": None, "pose_set_id": None,
             "candidate_count":len(body.events) if body.events is not None else None,
             "pose_rows":len(body.poses.poses) if body.poses is not None else None,
             "track_count":len({t.track_id for f in body.observations.tracks for t in f.tracks}) if body.observations else None}
    data = None
    if body.events is not None:
        # 원본 후보를 남기고 사용자가 지정한 추적 번호에 대해서만 역할을 연결한다.
        mapped = []
        by_track = {t: a for a in assignment.assignments for t in a.track_ids}
        for event in body.events:
            raw = event.model_dump()
            match = by_track.get(getattr(event, "track_id", None))
            if match:
                raw["source_participant_id"] = event.participant_id
                raw["participant_id"], raw["role"] = match.participant_id, match.role
            mapped.append(raw)
        data = ManualImport(annotation_source=body.source, definition_version=body.definition_version,
                            participants=DesignationRepo.participants(assignment), events=mapped,
                            notes=["외부 모델의 검증 전 후보. 역할은 사용자가 지정한 track_id에 한해 연결되었습니다."])
        if row["engine"]=="local":
            data.notes.extend(["내장 시험 분석: MediaPipe Pose Full의 관절 관측과 팔 확장·복귀 규칙입니다. 명중·득점 판정 모델이 아닙니다.", f"관절 관측 {len(body.poses.poses) if body.poses else 0}개, 동작 후보 {len(body.events)}건. 후보가 없다는 것이 실제 동작이 없었다는 뜻은 아닙니다."])
        # 사건의 구간·참조 검증이 실패하면 포즈 파일도 저장하지 않는다.
        from ...services.imports import build_bundle, validate_bundle
        try:
            validate_bundle(video,build_bundle(video,data))
        except ImportRejected as exc:
            raise ProblemError(422,"invalid_import","결과가 데이터 계약에 맞지 않습니다",errors=exc.errors[:100],error_count=len(exc.errors)) from exc
    if body.observations is not None:
        view = import_observations(video.video_id, body.observations, request, Response(), worker_user, state.videos, state.media, state.observations)
        saved["observation_set_id"] = view.set_id
    if body.poses is not None:
        view = import_poses(video.video_id, body.poses, request, Response(), worker_user, state.videos, state.media, state.poses)
        saved["pose_set_id"] = view.set_id

    def commit(conn):
        _valid(conn, task_id, key, body.lease)
        if row["kind"] == "actions":
            latest = conn.execute("SELECT assignment_json,set_id FROM designations WHERE video_id=? AND owner_id=? ORDER BY created_at DESC,designation_id DESC LIMIT 1", (video.video_id, row["owner_id"])).fetchone()
            obs = conn.execute("SELECT set_id FROM observation_sets WHERE video_id=? ORDER BY created_at DESC,set_id DESC LIMIT 1", (video.video_id,)).fetchone()
            if latest is None or obs is None or latest["set_id"] != row["observation_set_id"] or obs["set_id"] != row["observation_set_id"] or TrackAssignmentSet.model_validate_json(latest["assignment_json"]) != assignment:
                raise ProblemError(409, "stale_designation", "선수 지정 또는 추적 결과가 바뀌었습니다")
            conn.execute("INSERT INTO run_provenance VALUES(?,?,?,?)", (saved["run_id"], row["observation_set_id"], saved["pose_set_id"], row["assignment_json"]))
        message="결과 수신 완료"
        if row["engine"]=="local":
            message=f"내장 분석 완료 · 후보 트랙 {saved['track_count']}개 · 선수를 직접 지정해 주세요" if body.observations else f"내장 분석 완료 · 관절 관측 {saved['pose_rows']}개 · 팔 동작 후보 {saved['candidate_count']}건"
        conn.execute("UPDATE analysis_tasks SET status='completed',progress=1,lease_hash=NULL,lease_until=NULL,result_json=?,error=NULL,message=?,updated_at=? WHERE task_id=?", (json.dumps(saved),message, now_rfc3339(), task_id))
        conn.execute("INSERT INTO workspace_activity(owner_id,actor,action,target_id,at) VALUES(?,?,?,?,?)", (row["owner_id"], body.source, "analysis_completed", task_id, now_rfc3339()))

    if body.events is not None:
        saved["run_id"] = uuid.uuid5(uuid.NAMESPACE_URL, f"coach-task:{task_id}").hex
        try:
            import_manual(state.store, video, data, state.videos.latest_run_id(video.video_id), state.poses, dataset_kind="model", extra=commit, run_id=saved["run_id"])
        except sqlite3.IntegrityError:
            with state.db.tx() as conn:
                completed=conn.execute("SELECT result_json,status FROM analysis_tasks WHERE task_id=? AND owner_id=?", (task_id,key["owner_id"])).fetchone()
            if completed and completed["status"]=="completed":
                previous=json.loads(completed["result_json"])
                if previous["digest"]==payload_digest:
                    return previous
            raise ProblemError(409,"result_conflict","이미 수신한 결과와 충돌합니다") from None
        except ImportRejected as exc:
            raise ProblemError(422, "invalid_import", "결과가 데이터 계약에 맞지 않습니다", errors=exc.errors[:100], error_count=len(exc.errors)) from exc
    else:
        try:
            with state.db.tx(immediate=True) as conn:
                commit(conn)
        except ProblemError as exc:
            if exc.code != "lease_lost":
                raise
            with state.db.tx() as conn:
                completed=conn.execute("SELECT result_json,status FROM analysis_tasks WHERE task_id=? AND owner_id=?", (task_id,key["owner_id"])).fetchone()
            if completed and completed["status"]=="completed":
                previous=json.loads(completed["result_json"])
                if previous["digest"]==payload_digest:
                    return previous
            raise
    return saved
