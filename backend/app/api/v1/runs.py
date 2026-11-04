"""분석 실행과 결과 조회 API(상세 설계 27의 3.6). 현재는 fixture 모드의 읽기 전용 조회."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ...domain.findings import ConsistencyError, render_finding
from ...domain.models import Aggregate, Assessment, Event, Evidence, FrameTransform, PoseFrame
from ...media.store import MediaStore, UrlSigner
from ...storage.memory import RunStore, StoredRun
from ...storage.videos import VideoRepo
from ..deps import User, current_user, get_media, get_signer, get_store, get_videos, owned_run
from ..errors import ProblemError
from ..schemas import (
    AggregateDetail,
    EventDetail,
    EvidenceView,
    FindingView,
    MemberView,
    Joint,
    PoseSample,
    PoseView,
    RunSummary,
    TimelineView,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def summarize(run: StoredRun, store: RunStore, videos: VideoRepo) -> RunSummary:
    b = run.bundle
    video = videos.get(b.video_id)
    return RunSummary(
        run_id=run.run_id,
        video_id=b.video_id,
        filename=video.filename if video else None,
        mode=str(run.mode),
        status=run.status,
        dataset_kind=b.dataset_kind,
        expert_validated=b.expert_validated,
        definition_version=b.definition_version,
        sport=b.sport,
        duration_us=b.duration_us,
        participants=b.participants,
        counts={
            "events": len(b.events),
            "assessments": len(b.assessments),
            "aggregates": len(b.aggregates),
            "findings": len(b.findings),
        },
        parent_run_id=store.parent_of(run.run_id),
        superseded_by=store.child_of(run.run_id),
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    user: User = Depends(current_user), store: RunStore = Depends(get_store), videos: VideoRepo = Depends(get_videos)
) -> list[RunSummary]:
    return [summarize(r, store, videos) for r in store.list_for_owner(user.owner_id)]


@router.get("/{run_id}", response_model=RunSummary)
def get_run(
    run: StoredRun = Depends(owned_run), store: RunStore = Depends(get_store), videos: VideoRepo = Depends(get_videos)
) -> RunSummary:
    return summarize(run, store, videos)


@router.get("/{run_id}/findings", response_model=list[FindingView])
def get_findings(run: StoredRun = Depends(owned_run)) -> list[FindingView]:
    views: list[FindingView] = []
    for finding in run.bundle.findings:
        try:
            rendered = render_finding(run.raw, finding.model_dump())
        except ConsistencyError as exc:
            # 저장된 집계와 판정이 어긋나면 틀린 숫자를 내보내지 않는다.
            raise ProblemError(500, "inconsistent_aggregate", "집계 값이 판정과 일치하지 않음", str(exc)) from exc
        views.append(
            FindingView(
                finding_id=finding.finding_id,
                claim_type=finding.claim_type,
                status=finding.status,
                hold_reason=finding.hold_reason,
                template_id=finding.template_id,
                text=rendered.text if rendered else None,
                slots=rendered.slots if rendered else None,
                input_refs=finding.input_refs,
                evidence_refs=finding.evidence_refs,
                rule_ref=finding.rule_ref,
            )
        )
    return views


def _find_aggregate(run: StoredRun, aggregate_id: str) -> Aggregate:
    for g in run.bundle.aggregates:
        if g.aggregate_id == aggregate_id:
            return g
    raise ProblemError(404, "aggregate_not_found", "집계를 찾을 수 없음", f"aggregate_id={aggregate_id}")


@router.get("/{run_id}/aggregates/{aggregate_id}", response_model=AggregateDetail)
def get_aggregate(aggregate_id: str, run: StoredRun = Depends(owned_run)) -> AggregateDetail:
    agg = _find_aggregate(run, aggregate_id)
    events: dict[str, Event] = {e.event_id: e for e in run.bundle.events}
    assessments: dict[tuple[str, str], Assessment] = {
        (a.subject_ref, a.question_code): a for a in run.bundle.assessments
    }

    def members(refs: list[str]) -> list[MemberView]:
        return [
            MemberView(event=events[r], assessment=assessments.get((r, agg.question_code)))
            for r in refs
            if r in events
        ]

    return AggregateDetail(
        aggregate=agg,
        members={
            "numerator": members(agg.numerator_refs),
            "denominator": members(agg.denominator_refs),
            "unknown": members(agg.unknown_refs),
            "excluded": members(agg.excluded_refs),
        },
    )


@router.get("/{run_id}/timeline", response_model=TimelineView)
def get_timeline(
    run: StoredRun = Depends(owned_run),
    from_us: int = Query(0, ge=0),
    to_us: int | None = Query(None, gt=0),
) -> TimelineView:
    b = run.bundle
    end = b.duration_us if to_us is None else to_us
    if not from_us < end:
        raise ProblemError(422, "invalid_range", "구간이 올바르지 않음", f"from_us={from_us}, to_us={end}")

    def overlaps(start_us: int, end_us: int) -> bool:  # [start, end)
        return start_us < end and end_us > from_us

    events = [e for e in b.events if overlaps(e.start_timestamp_us, e.end_timestamp_us)]
    event_ids = {e.event_id for e in events}
    relations = [r for r in b.relations if r.subject_event_id in event_ids or r.object_event_id in event_ids]
    subject_ids = event_ids | {r.relation_id for r in relations}
    return TimelineView(
        from_us=from_us,
        to_us=end,
        states=[s for s in b.state_intervals if overlaps(s.start_us, s.end_us)],
        events=events,
        relations=relations,
        assessments=[a for a in b.assessments if a.subject_ref in subject_ids],
    )


@router.get("/{run_id}/events/{event_id}", response_model=EventDetail)
def get_event(event_id: str, run: StoredRun = Depends(owned_run)) -> EventDetail:
    b = run.bundle
    event = next((e for e in b.events if e.event_id == event_id), None)
    if event is None:
        raise ProblemError(404, "event_not_found", "사건을 찾을 수 없음", f"event_id={event_id}")
    relations = [r for r in b.relations if event_id in (r.subject_event_id, r.object_event_id)]
    related_ids = {event_id} | {r.relation_id for r in relations}
    exchanges = [x for x in b.exchanges if event_id in x.event_refs]
    evidence_ids = {ref for x in exchanges for ref in x.evidence_refs}
    return EventDetail(
        event=event,
        assessments=[a for a in b.assessments if a.subject_ref in related_ids],
        relations=relations,
        exchanges=exchanges,
        evidence=[ev for ev in b.evidence if ev.evidence_id in evidence_ids],
    )


def _evidence_view(ev: Evidence, media: MediaStore, signer: UrlSigner, media_state: str = "present") -> EvidenceView:
    """재생 가능 여부는 저장된 값이 아니라 지금 파일 저장소에 영상이 있는지로 정한다."""
    if media.video_path(ev.video_id) is None:
        # 구조화된 근거(구간·선수)는 그대로 내려가고 재생만 불가하다(19).
        # 영상 기록이 있으면 삭제·만료 여부를 기록에서 가져온다(15). 없으면 결과 묶음의 값을 쓴다.
        availability = media_state if media_state != "present" else ev.availability
        reason = f"evidence_{availability}" if availability != "available" else "evidence_unavailable"
        shown = ev.model_copy(update={"availability": availability if availability != "available" else "unavailable"})
        return EvidenceView(evidence=shown, playable=False, access_url=None, reason_code=reason)
    available = ev.model_copy(update={"availability": "available"})
    return EvidenceView(evidence=available, playable=True, access_url=signer.sign(ev.video_id), reason_code=None)


def _media_state(videos: VideoRepo, video_id: str) -> str:
    record = videos.get(video_id)
    return record.media_state if record else "present"


@router.get("/{run_id}/evidence", response_model=list[EvidenceView])
def list_evidence(
    run: StoredRun = Depends(owned_run),
    media: MediaStore = Depends(get_media),
    signer: UrlSigner = Depends(get_signer),
    videos: VideoRepo = Depends(get_videos),
) -> list[EvidenceView]:
    return [
        _evidence_view(ev, media, signer, _media_state(videos, ev.video_id))
        for ev in sorted(run.bundle.evidence, key=lambda e: (e.start_us, e.evidence_id))
    ]


@router.get("/{run_id}/evidence/{evidence_id}", response_model=EvidenceView)
def get_evidence(
    evidence_id: str,
    run: StoredRun = Depends(owned_run),
    media: MediaStore = Depends(get_media),
    signer: UrlSigner = Depends(get_signer),
    videos: VideoRepo = Depends(get_videos),
) -> EvidenceView:
    ev = next((e for e in run.bundle.evidence if e.evidence_id == evidence_id), None)
    if ev is None:
        raise ProblemError(404, "evidence_not_found", "근거를 찾을 수 없음", f"evidence_id={evidence_id}")
    return _evidence_view(ev, media, signer, _media_state(videos, ev.video_id))


def _pose_sample(frame: PoseFrame, transform: FrameTransform) -> PoseSample:
    """저장된 분석 좌표를 표시 좌표로 되돌린다. 좌표가 없는 관절은 missing으로 두고 0으로 채우지 않는다."""
    joints: list[Joint] = []
    for name, (x, y, score) in frame.keypoints.items():
        if x is None or y is None:
            joints.append(Joint(name=name, x=None, y=None, score=score, state="missing"))
        else:
            dx, dy = transform.to_display(x, y)
            joints.append(Joint(name=name, x=round(dx, 3), y=round(dy, 3), score=score))
    return PoseSample(time_us=frame.timestamp_us, participant_id=frame.participant_id, joints=joints)


@router.get("/{run_id}/poses", response_model=PoseView)
def get_poses(
    request: Request,
    run: StoredRun = Depends(owned_run),
    media: MediaStore = Depends(get_media),
    from_us: int = Query(0, ge=0),
    to_us: int | None = Query(None, gt=0),
) -> PoseView:
    """구간의 관절 관측. 없으면 available=false와 사유를 돌려주며 빈 값을 0으로 채우지 않는다."""
    end = min(run.bundle.duration_us, to_us) if to_us is not None else run.bundle.duration_us
    pose_set = media.pose_set(run.bundle.video_id)
    if not from_us < end:
        raise ProblemError(422, "invalid_range", "구간이 올바르지 않음", f"from_us={from_us}, to_us={end}")
    if pose_set is None:
        from ...services.pose_overlay import research_pose_view
        return research_pose_view(request, run, from_us, end)
    return PoseView(
        available=True,
        landmark_set_id=pose_set.landmark_set_id,
        coordinate_space_id=pose_set.coordinate_space_id,
        width=pose_set.transform.display_width,
        height=pose_set.transform.display_height,
        connections=pose_set.connections,
        samples=[_pose_sample(f, pose_set.transform) for f in pose_set.frames if from_us <= f.timestamp_us < end],
    )


@router.get("/{run_id}/observation-summary")
def observation_summary(request: Request, run: StoredRun = Depends(owned_run), media: MediaStore = Depends(get_media)) -> dict:
    """실행에 연결된 관측 집계와 화면 좌표 기반 코칭 초안. 기술·명중 판정은 하지 않는다."""
    view = get_poses(request=request, run=run, media=media, from_us=0, to_us=None)
    people = []
    for participant in run.bundle.participants:
        times = sorted({s.time_us for s in view.samples if s.participant_id == participant.participant_id})
        people.append({"participant_id": participant.participant_id, "role": str(participant.role),
                       "frame_count": len(times), "first_us": times[0] if times else None,
                       "last_us": times[-1] if times else None})
    shared = set.intersection(*({s.time_us for s in view.samples if s.participant_id == p.participant_id} for p in run.bundle.participants)) if len(run.bundle.participants) >= 2 else set()
    from ...services.coaching import coaching_draft
    return {"available": view.available, "reason_code": view.reason_code, "duration_us": run.bundle.duration_us,
            "sample_count": len(view.samples), "participants": people,
            "video_id": run.bundle.video_id,
            "coaching": coaching_draft(view,run.bundle.participants,run.bundle.duration_us),
            "event_count": len(run.bundle.events), "both_observed_frames": len(shared),
            "suggested_time_us": min(shared) if shared else min((s.time_us for s in view.samples), default=None)}
