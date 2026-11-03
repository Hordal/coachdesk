"""코치의 실사용 기록·검수·보고서. 계정 소유권과 낙관적 잠금을 서버에서 강제한다."""
from __future__ import annotations

import csv
from datetime import date
import io
from pathlib import Path
import shutil
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from ...domain.findings import ConsistencyError, render_finding
from ...domain.models import Strict
from ...jobs.ingest import initial_stages
from ...media.binaries import available
from ...storage.db import now_rfc3339
from ..deps import User, current_user
from ..errors import ProblemError

router = APIRouter(prefix="/workspace", tags=["coach-workspace"])
ReviewState = Literal["pending", "in_review", "reviewed", "changes_requested"]


class AthleteCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    discipline: Literal["boxing", "kickboxing", "muay_thai", "mma"] = "mma"


class SessionUpdate(Strict):
    title: str = Field(min_length=1, max_length=160)
    athlete_id: str | None = None
    opponent: str = Field(default="", max_length=80)
    session_date: date | None = None
    session_type: Literal["sparring", "match", "training"] = "sparring"
    notes: str = Field(default="", max_length=4000)
    expected_revision: int = Field(ge=0)


class ReviewUpdate(Strict):
    state: ReviewState
    note: str = Field(default="", max_length=4000)
    expected_revision: int = Field(ge=0)


class ReviewView(BaseModel):
    run_id: str
    state: ReviewState
    note: str
    reviewer: str | None
    revision: int
    updated_at: str | None


class WorkItem(BaseModel):
    video_id: str
    filename: str
    title: str
    sport: str
    status: str
    size_bytes: int
    duration_us: int | None
    created_at: str
    media_state: str
    athlete_id: str | None
    athlete_name: str | None
    opponent: str
    session_date: str | None
    session_type: str
    notes: str
    revision: int
    run_id: str | None
    dataset_kind: str | None
    event_count: int
    review_state: ReviewState
    progress: float
    analysis_status: str | None = None


class WorkPage(BaseModel):
    items: list[WorkItem]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


def _activity(conn, user: User, action: str, target: str) -> None:
    conn.execute("INSERT INTO workspace_activity(owner_id,actor,action,target_id,at) VALUES(?,?,?,?,?)",
                 (user.owner_id, user.display_name or "코치", action, target, now_rfc3339()))


def _video(request: Request, user: User, video_id: str):
    video = request.app.state.videos.get_for_owner(video_id, user.owner_id)
    if video is None:
        raise ProblemError(404, "video_not_found", "영상을 찾을 수 없음")
    return video


JOINS = """FROM videos v
LEFT JOIN session_details s ON s.video_id=v.video_id
LEFT JOIN athletes a ON a.athlete_id=s.athlete_id
LEFT JOIN runs r ON r.run_id=(SELECT run_id FROM runs WHERE video_id=v.video_id ORDER BY created_at DESC,run_id DESC LIMIT 1)
LEFT JOIN coach_reviews cr ON cr.run_id=r.run_id
LEFT JOIN analysis_tasks atask ON atask.task_id=(SELECT task_id FROM analysis_tasks WHERE video_id=v.video_id ORDER BY created_at DESC,task_id DESC LIMIT 1)
LEFT JOIN jobs j ON j.job_id=(SELECT job_id FROM jobs WHERE video_id=v.video_id ORDER BY created_at DESC,job_id DESC LIMIT 1)
"""
SELECT_ITEM = """SELECT v.video_id,v.filename,COALESCE(s.title,v.filename) title,v.sport,v.status,v.size_bytes,v.duration_us,
v.created_at,v.media_state,s.athlete_id,a.name athlete_name,COALESCE(s.opponent,'') opponent,s.session_date,
COALESCE(s.session_type,'sparring') session_type,COALESCE(s.notes,'') notes,COALESCE(s.revision,0) revision,
r.run_id,json_extract(r.bundle_json,'$.dataset_kind') dataset_kind,
COALESCE(json_array_length(r.bundle_json,'$.events'),0) event_count,
COALESCE(cr.state,'pending') review_state,CASE WHEN atask.status IN ('queued','running') THEN atask.progress ELSE COALESCE(j.progress,0) END progress,atask.status analysis_status """


@router.get("/sessions", response_model=WorkPage)
def sessions(request: Request, user: User = Depends(current_user), q: str = Query("", max_length=120),
             status: Literal["all", "processing", "ready", "failed", "pending", "reviewed"] = "all",
             athlete_id: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=100)):
    where = ["v.owner_id=?", "v.deleting=0"]
    args: list = [user.owner_id]
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(COALESCE(s.title,v.filename) LIKE ? ESCAPE '\\' OR v.filename LIKE ? ESCAPE '\\' OR a.name LIKE ? ESCAPE '\\')")
        args.extend([pattern] * 3)
    if athlete_id:
        where.append("s.athlete_id=?")
        args.append(athlete_id)
    if status == "processing":
        where.append("(v.status='processing' OR atask.status IN ('queued','running'))")
    elif status == "failed":
        where.append("(v.status IN ('failed','cancelled') OR atask.status='failed')")
    elif status == "ready":
        where.append("v.status=?")
        args.append(status)
    elif status == "reviewed":
        where.append("cr.state='reviewed'")
    elif status == "pending":
        where.extend(["COALESCE(cr.state,'pending')!='reviewed'", "(json_array_length(r.bundle_json,'$.events')>0 OR json_extract(r.bundle_json,'$.dataset_kind')='model')"])
    clause = " WHERE " + " AND ".join(where)
    with request.app.state.db.tx() as conn:
        total = conn.execute("SELECT COUNT(*) " + JOINS + clause, args).fetchone()[0]
        rows = conn.execute(SELECT_ITEM + JOINS + clause + " ORDER BY v.created_at DESC,v.video_id DESC LIMIT ? OFFSET ?",
                            [*args, page_size, (page - 1) * page_size]).fetchall()
        counts = dict(conn.execute("SELECT COUNT(*) total,COALESCE(SUM(v.status='processing' OR atask.status IN ('queued','running')),0) processing,COALESCE(SUM(v.status IN ('failed','cancelled') OR atask.status='failed'),0) failed,"
                                  "COALESCE(SUM(cr.state='reviewed'),0) reviewed,COALESCE(SUM(COALESCE(cr.state,'pending')!='reviewed' AND (json_array_length(r.bundle_json,'$.events')>0 OR json_extract(r.bundle_json,'$.dataset_kind')='model')),0) pending "
                                  + JOINS + " WHERE v.owner_id=? AND v.deleting=0", (user.owner_id,)).fetchone())
    return WorkPage(items=[WorkItem(**dict(row)) for row in rows], total=total, page=page, page_size=page_size, counts=counts)


@router.get("/sessions/{video_id}", response_model=WorkItem)
def session(video_id: str, request: Request, user: User = Depends(current_user)):
    _video(request, user, video_id)
    with request.app.state.db.tx() as conn:
        row = conn.execute(SELECT_ITEM + JOINS + " WHERE v.video_id=? AND v.owner_id=? AND v.deleting=0", (video_id, user.owner_id)).fetchone()
    return WorkItem(**dict(row))


@router.put("/sessions/{video_id}", response_model=WorkItem)
def update_session(video_id: str, body: SessionUpdate, request: Request, user: User = Depends(current_user)):
    _video(request, user, video_id)
    if not body.title.strip():
        raise ProblemError(422, "title_required", "기록 제목을 입력하세요")
    with request.app.state.db.tx(immediate=True) as conn:
        row = conn.execute("SELECT revision FROM session_details WHERE video_id=?", (video_id,)).fetchone()
        if (row[0] if row else 0) != body.expected_revision:
            raise ProblemError(409, "stale_session", "다른 화면에서 기록이 바뀌었습니다. 새로고침 후 다시 저장하세요")
        if body.athlete_id and conn.execute("SELECT 1 FROM athletes WHERE athlete_id=? AND owner_id=?", (body.athlete_id, user.owner_id)).fetchone() is None:
            raise ProblemError(422, "invalid_athlete", "이 분석실에 등록된 선수를 선택하세요")
        conn.execute("INSERT INTO session_details(video_id,owner_id,title,athlete_id,opponent,session_date,session_type,notes,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(video_id) DO UPDATE SET title=excluded.title,athlete_id=excluded.athlete_id,opponent=excluded.opponent,session_date=excluded.session_date,session_type=excluded.session_type,notes=excluded.notes,revision=excluded.revision,updated_at=excluded.updated_at",
                     (video_id,user.owner_id,body.title.strip(),body.athlete_id,body.opponent.strip(),body.session_date.isoformat() if body.session_date else None,body.session_type,body.notes,body.expected_revision+1,now_rfc3339()))
        _activity(conn, user, "session.updated", video_id)
    return session(video_id, request, user)


@router.get("/athletes")
def athletes(request: Request, user: User = Depends(current_user)) -> list[dict]:
    with request.app.state.db.tx() as conn:
        return [dict(row) for row in conn.execute("SELECT athlete_id,name,discipline,created_at FROM athletes WHERE owner_id=? ORDER BY name LIMIT 500", (user.owner_id,))]


@router.post("/athletes", status_code=201)
def create_athlete(body: AthleteCreate, request: Request, user: User = Depends(current_user)) -> dict:
    if not body.name.strip():
        raise ProblemError(422, "name_required", "선수 이름을 입력하세요")
    item = {"athlete_id": uuid.uuid4().hex, "name": body.name.strip(), "discipline": body.discipline, "created_at": now_rfc3339()}
    with request.app.state.db.tx(immediate=True) as conn:
        if conn.execute("SELECT COUNT(*) FROM athletes WHERE owner_id=?", (user.owner_id,)).fetchone()[0] >= 500:
            raise ProblemError(409, "athlete_limit", "분석실당 선수 등록 한도는 500명입니다")
        conn.execute("INSERT INTO athletes VALUES(?,?,?,?,?)", (item["athlete_id"],user.owner_id,item["name"],item["discipline"],item["created_at"]))
        _activity(conn, user, "athlete.created", item["athlete_id"])
    return item


def _run(request: Request, user: User, run_id: str):
    run = request.app.state.store.get_for_owner(run_id, user.owner_id)
    if run is None:
        raise ProblemError(404, "run_not_found", "분석 결과를 찾을 수 없음")
    _video(request, user, run.bundle.video_id)
    return run


def _review(conn, run_id: str) -> ReviewView:
    row = conn.execute("SELECT run_id,state,note,reviewer,revision,updated_at FROM coach_reviews WHERE run_id=?", (run_id,)).fetchone()
    return ReviewView(**dict(row)) if row else ReviewView(run_id=run_id,state="pending",note="",reviewer=None,revision=0,updated_at=None)


@router.get("/runs/{run_id}/review", response_model=ReviewView)
def review(run_id: str, request: Request, user: User = Depends(current_user)):
    _run(request, user, run_id)
    with request.app.state.db.tx() as conn:
        return _review(conn, run_id)


@router.get("/runs/{run_id}/review-history", response_model=list[ReviewView])
def review_history(run_id: str, request: Request, user: User = Depends(current_user)):
    _run(request,user,run_id)
    with request.app.state.db.tx() as conn:
        return [ReviewView(**dict(row)) for row in conn.execute("SELECT run_id,state,note,reviewer,revision,updated_at FROM coach_review_history WHERE run_id=? AND owner_id=? ORDER BY revision DESC",(run_id,user.owner_id))]


@router.put("/runs/{run_id}/review", response_model=ReviewView)
def update_review(run_id: str, body: ReviewUpdate, request: Request, user: User = Depends(current_user)):
    run = _run(request, user, run_id)
    if body.state == "reviewed" and (run.bundle.dataset_kind == "unanalyzed" or (not run.bundle.events and run.bundle.dataset_kind != "model")):
        raise ProblemError(409, "no_results", "검토할 사건이 없습니다. 분석 결과를 연결한 뒤 검수하세요")
    if body.state in {"reviewed", "changes_requested"} and not body.note.strip():
        raise ProblemError(422, "review_note_required", "검수 의견을 남겨 주세요")
    with request.app.state.db.tx(immediate=True) as conn:
        latest = conn.execute("SELECT run_id FROM runs WHERE video_id=? ORDER BY created_at DESC,run_id DESC LIMIT 1", (run.bundle.video_id,)).fetchone()
        if latest is None or latest[0] != run_id:
            raise ProblemError(409, "stale_run", "새 분석 결과가 있습니다. 최신 결과에서 검수하세요")
        previous = _review(conn, run_id)
        if previous.revision != body.expected_revision:
            raise ProblemError(409, "stale_review", "다른 화면에서 검수 상태가 바뀌었습니다. 새로고침 후 다시 확인하세요")
        conn.execute("INSERT INTO coach_reviews VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET state=excluded.state,note=excluded.note,reviewer=excluded.reviewer,revision=excluded.revision,updated_at=excluded.updated_at",
                     (run_id,user.owner_id,body.state,body.note.strip(),user.display_name or "코치",previous.revision+1,now_rfc3339()))
        conn.execute("INSERT INTO coach_review_history SELECT run_id,owner_id,state,note,reviewer,revision,updated_at FROM coach_reviews WHERE run_id=?",(run_id,))
        _activity(conn, user, "review."+body.state, run_id)
        return _review(conn, run_id)


@router.get("/runs/{run_id}/report")
def report(run_id: str, request: Request, user: User = Depends(current_user)) -> dict:
    run = _run(request, user, run_id)
    with request.app.state.db.tx() as conn:
        checked = _review(conn, run_id)
    findings = []
    for f in run.raw["findings"]:
        try:
            rendered = render_finding(run.raw, f)
        except ConsistencyError as exc:
            raise ProblemError(409,"inconsistent_report","집계와 판정이 일치하지 않아 보고서를 만들 수 없습니다") from exc
        findings.append({"finding_id":f["finding_id"],"status":f["status"],"text":rendered.text if rendered else None,"hold_reason":f.get("hold_reason")})
    return {"run_id":run_id,"session":session(run.bundle.video_id,request,user).model_dump(),"review":checked.model_dump(),
            "dataset_kind":run.bundle.dataset_kind,"expert_validated":run.bundle.expert_validated,
            "events":[e.model_dump(mode="json") for e in run.bundle.events],"findings":findings,"notes":run.bundle.notes}


def _csv_safe(value: object) -> str:
    text = str(value) if value is not None else ""
    return "'"+text if text.lstrip().startswith(("=","+","-","@")) or text.startswith(("\t","\r","\n")) else text


@router.get("/runs/{run_id}/report.csv")
def export_report(run_id: str, request: Request, user: User = Depends(current_user)):
    result = report(run_id,request,user)
    if result["review"]["state"] != "reviewed" or result["session"]["run_id"] != run_id:
        raise ProblemError(409,"report_not_ready","최신 분석 결과를 검수 완료한 뒤 보고서를 내려받을 수 있습니다")
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["기록","선수","분석 ID","데이터 종류","전문가 검증","검수 의견","사건","시작(us)","끝(us)","대상","후보 상태","판정 검증","결과"])
    for event in result["events"]:
        writer.writerow([_csv_safe(x) for x in [result["session"]["title"],result["session"]["athlete_name"],run_id,result["dataset_kind"],result["expert_validated"],result["review"]["note"],event["event_label"],event["start_timestamp_us"],event["end_timestamp_us"],event["participant_id"],event["status"],event["verified"],event["outcome"]]])
    with request.app.state.db.tx(immediate=True) as conn:
        _activity(conn,user,"report.exported",run_id)
    return Response(content="\ufeff"+out.getvalue(),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="coach-report-{run_id}.csv"'})


@router.post("/sessions/{video_id}/retry", status_code=201)
def retry(video_id: str, request: Request, user: User = Depends(current_user)) -> dict:
    video = _video(request,user,video_id)
    if video.status not in {"failed","cancelled"}:
        raise ProblemError(409,"retry_not_allowed","실패하거나 취소한 영상 준비 작업만 다시 시작할 수 있습니다")
    if not Path(video.source_path).is_file():
        raise ProblemError(409,"source_missing","원본이 없어 다시 처리할 수 없습니다. 영상을 다시 올려 주세요")
    with request.app.state.db.tx(immediate=True) as conn:
        if conn.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')",(video_id,)).fetchone():
            raise ProblemError(409,"already_processing","이미 대기하거나 처리 중인 작업이 있습니다")
        job_id = request.app.state.jobs.create(conn,owner_id=user.owner_id,kind="ingest",video_id=video_id,stages=initial_stages(),max_attempts=request.app.state.settings.max_attempts)
        conn.execute("UPDATE videos SET status='processing' WHERE video_id=?",(video_id,))
        _activity(conn,user,"job.retried",video_id)
    return {"job_id":job_id,"video_id":video_id}


@router.get("/activity")
def activity(request: Request, user: User = Depends(current_user)) -> list[dict]:
    with request.app.state.db.tx() as conn:
        return [dict(row) for row in conn.execute("SELECT activity_id,actor,action,target_id,at FROM workspace_activity WHERE owner_id=? ORDER BY activity_id DESC LIMIT 100",(user.owner_id,))]


@router.get("/status")
def operational_status(request: Request, user: User = Depends(current_user)) -> dict:
    settings = request.app.state.settings
    with request.app.state.db.tx() as conn:
        workers = conn.execute("SELECT COUNT(*) FROM worker_presence WHERE seen_at>?",(time.time()-45,)).fetchone()[0]
        analysis_workers = conn.execute("SELECT COUNT(*) FROM analysis_keys WHERE owner_id=? AND revoked_at IS NULL AND internal=0 AND last_seen>?",(user.owner_id,time.time()-60)).fetchone()[0]
    disk = shutil.disk_usage(settings.data_dir)
    tools = available()
    return {"database":"ok","video_tools":tools,"active_workers":workers,"disk_free_bytes":disk.free,
            "disk_total_bytes":disk.total,"auth_mode":settings.auth_mode,"profile":settings.app_profile,
            "retention_days":settings.retention_days,"fixtures_enabled":settings.enable_fixtures,
            "analysis_workers":analysis_workers,"automatic_inference":False,"status":"ready" if workers and tools and disk.free>1024**3 else "attention"}
