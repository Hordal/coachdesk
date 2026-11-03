"""정정, 오류 신고, 의도 설명(상세 설계 11, 27의 3.6).

정정은 새 실행을 만들고 최초 결과를 보존한다. 신고와 의도 설명은 기존 계산을 바꾸지 않고
따로 보관하며 학습 정답에 자동 편입하지 않는다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status

from ...services.corrections import CorrectionError, CorrectionService, plan_designation
from ...storage.db import Database, now_rfc3339
from ...storage.memory import RunStore, StoredRun
from ...storage.videos import VideoRepo
from ...domain.candidates import suggest_candidate_roles
from ..deps import User, current_user, get_designations, get_store, get_videos, owned_run
from ...services.designation import DesignationRepo
from ..errors import ProblemError
from ..schemas import (
    ContextNoteRequest,
    ContextNoteView,
    CorrectionEntry,
    CandidateRoleSuggestion,
    CorrectionHistory,
    CorrectionPreview,
    DesignationRequest,
    ParticipantChange,
    ReportRequest,
    ReportView,
    RunSummary,
)
from .runs import summarize

router = APIRouter(prefix="/runs", tags=["corrections"])


def _db(request: Request) -> Database:
    return request.app.state.db


def _service(request: Request, store: RunStore = Depends(get_store)) -> CorrectionService:
    return CorrectionService(request.app.state.db, store)


def _raise(exc: CorrectionError) -> ProblemError:
    return ProblemError(exc.status, exc.code, exc.title, None, **exc.extra)


@router.post("/{run_id}/corrections/preview", response_model=CorrectionPreview)
def preview_correction(body: DesignationRequest, run: StoredRun = Depends(owned_run)) -> CorrectionPreview:
    """정정을 제출하기 전에 바뀌는 내용과 재계산 영향을 보여 준다. 아무것도 저장하지 않는다."""
    try:
        changes = plan_designation(run, body.user_participant_id)
    except CorrectionError as exc:
        raise _raise(exc) from exc
    note = (
        "선수 번호 기준으로 계산한 집계·판정·문장은 바뀌지 않고 선수의 역할 표시만 바뀝니다. 이전 결과는 그대로 남습니다."
        if changes
        else "이미 같은 선수가 본인으로 지정되어 있어 바뀌는 내용이 없습니다."
    )
    return CorrectionPreview(
        kind="designation",
        changes=[ParticipantChange(participant_id=c.participant_id, role_before=c.role_before, role_after=c.role_after) for c in changes],
        recalculation="not_required",
        note=note,
    )


@router.post("/{run_id}/corrections", response_model=RunSummary, status_code=status.HTTP_201_CREATED)
def create_correction(
    body: DesignationRequest,
    run: StoredRun = Depends(owned_run),
    store: RunStore = Depends(get_store),
    service: CorrectionService = Depends(_service),
    videos: VideoRepo = Depends(get_videos),
) -> RunSummary:
    """본인 지정 정정. 새 실행을 만들어 이전 실행과 연결하고, 이미 정정된 실행이면 최신 실행을 알려 주며 거절한다."""
    try:
        new = service.apply_designation(run, body.user_participant_id)
    except CorrectionError as exc:
        raise _raise(exc) from exc
    return summarize(new, store, videos)


@router.get("/{run_id}/candidate-roles", response_model=list[CandidateRoleSuggestion])
def candidate_roles(
    run: StoredRun = Depends(owned_run),
    user: User = Depends(current_user),
    designations: DesignationRepo = Depends(get_designations),
) -> list[CandidateRoleSuggestion]:
    """역할 미정(`candidate:N`) 선수에 대한 제안. 사용자가 고른 트랙 지정과 후보의 트랙이 완전히 겹칠 때만 제안하며 아무것도 저장하지 않는다."""
    assignment, _ = designations.current(run.bundle.video_id, user.owner_id)
    return [CandidateRoleSuggestion(**s) for s in suggest_candidate_roles(run.raw["participants"], assignment)]


@router.get("/{run_id}/corrections", response_model=CorrectionHistory)
def correction_history(
    request: Request,
    run: StoredRun = Depends(owned_run),
    store: RunStore = Depends(get_store),
) -> CorrectionHistory:
    """이 실행이 속한 정정 이력. 최초 실행부터 최신 실행까지 순서대로."""
    root = run.run_id
    while (parent := store.parent_of(root)) is not None:
        root = parent
    entries: list[CorrectionEntry] = []
    latest = root
    with _db(request).tx() as conn:
        while True:
            row = conn.execute("SELECT * FROM corrections WHERE parent_run_id = ?", (latest,)).fetchone()
            if row is None:
                break
            entries.append(
                CorrectionEntry(correction_id=row["correction_id"], kind=row["kind"], from_run_id=row["parent_run_id"], run_id=row["run_id"], created_at=row["created_at"])
            )
            latest = row["run_id"]
    return CorrectionHistory(original_run_id=root, latest_run_id=latest, entries=entries)


@router.post("/{run_id}/reports", response_model=ReportView, status_code=status.HTTP_201_CREATED)
def create_report(
    request: Request,
    body: ReportRequest,
    run: StoredRun = Depends(owned_run),
    user: User = Depends(current_user),
) -> ReportView:
    """분석 오류 신고. 접수만 하며 결과를 바꾸지 않는다."""
    view = ReportView(report_id=uuid.uuid4().hex, run_id=run.run_id, category=body.category, subject_ref=body.subject_ref, text=body.text, created_at=now_rfc3339())
    with _db(request).tx(immediate=True) as conn:
        conn.execute(
            "INSERT INTO reports (report_id, owner_id, video_id, run_id, category, subject_ref, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (view.report_id, user.owner_id, run.bundle.video_id, run.run_id, view.category, view.subject_ref, view.text, view.created_at),
        )
    return view


@router.get("/{run_id}/reports", response_model=list[ReportView])
def list_reports(request: Request, run: StoredRun = Depends(owned_run), user: User = Depends(current_user)) -> list[ReportView]:
    with _db(request).tx() as conn:
        rows = conn.execute("SELECT * FROM reports WHERE run_id = ? AND owner_id = ? ORDER BY created_at, report_id", (run.run_id, user.owner_id)).fetchall()
    return [ReportView(report_id=r["report_id"], run_id=r["run_id"], category=r["category"], subject_ref=r["subject_ref"], text=r["text"], created_at=r["created_at"]) for r in rows]


@router.post("/{run_id}/context-notes", response_model=ContextNoteView, status_code=status.HTTP_201_CREATED)
def create_context_note(
    request: Request,
    body: ContextNoteRequest,
    run: StoredRun = Depends(owned_run),
    user: User = Depends(current_user),
) -> ContextNoteView:
    """의도 설명(self_report). AI 관측과 분리해 보관하고 기존 계산을 바꾸지 않는다."""
    if (body.start_us is None) != (body.end_us is None) or (body.start_us is not None and body.end_us is not None and body.start_us >= body.end_us):
        raise ProblemError(422, "invalid_range", "구간은 시작과 끝을 함께 주어야 하며 시작이 끝보다 앞서야 함")
    if body.end_us is not None and body.end_us > run.bundle.duration_us:
        raise ProblemError(422, "invalid_range", "구간이 영상 길이를 넘음")
    if body.participant_id is not None and body.participant_id not in {p.participant_id for p in run.bundle.participants}:
        raise ProblemError(422, "unknown_participant", "이 결과에 없는 선수")
    view = ContextNoteView(
        note_id=uuid.uuid4().hex, run_id=run.run_id, participant_id=body.participant_id,
        start_us=body.start_us, end_us=body.end_us, text=body.text, created_at=now_rfc3339(),
    )
    with _db(request).tx(immediate=True) as conn:
        conn.execute(
            "INSERT INTO context_notes (note_id, owner_id, video_id, run_id, participant_id, start_us, end_us, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (view.note_id, user.owner_id, run.bundle.video_id, run.run_id, view.participant_id, view.start_us, view.end_us, view.text, view.created_at),
        )
    return view


@router.get("/{run_id}/context-notes", response_model=list[ContextNoteView])
def list_context_notes(request: Request, run: StoredRun = Depends(owned_run), user: User = Depends(current_user)) -> list[ContextNoteView]:
    with _db(request).tx() as conn:
        rows = conn.execute("SELECT * FROM context_notes WHERE run_id = ? AND owner_id = ? ORDER BY created_at, note_id", (run.run_id, user.owner_id)).fetchall()
    return [
        ContextNoteView(note_id=r["note_id"], run_id=r["run_id"], participant_id=r["participant_id"], start_us=r["start_us"], end_us=r["end_us"], text=r["text"], created_at=r["created_at"])
        for r in rows
    ]
