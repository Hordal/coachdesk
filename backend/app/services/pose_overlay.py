"""가져온 33점 포즈를 실행 시점의 선수 지정·좌표 변환으로 표시한다."""
import json
import math

from ..domain.models import FrameTransform, TrackAssignmentSet

CONNECTIONS = [
    ("nose","left_eye_inner"),("left_eye_inner","left_eye"),("left_eye","left_eye_outer"),("left_eye_outer","left_ear"),
    ("nose","right_eye_inner"),("right_eye_inner","right_eye"),("right_eye","right_eye_outer"),("right_eye_outer","right_ear"),
    ("mouth_left","mouth_right"),("left_shoulder","right_shoulder"),
    ("left_shoulder","left_elbow"),("left_elbow","left_wrist"),("left_wrist","left_pinky"),("left_wrist","left_index"),("left_wrist","left_thumb"),("left_pinky","left_index"),
    ("right_shoulder","right_elbow"),("right_elbow","right_wrist"),("right_wrist","right_pinky"),("right_wrist","right_index"),("right_wrist","right_thumb"),("right_pinky","right_index"),
    ("left_shoulder","left_hip"),("right_shoulder","right_hip"),("left_hip","right_hip"),
    ("left_hip","left_knee"),("left_knee","left_ankle"),("left_ankle","left_heel"),("left_heel","left_foot_index"),("left_ankle","left_foot_index"),
    ("right_hip","right_knee"),("right_knee","right_ankle"),("right_ankle","right_heel"),("right_heel","right_foot_index"),("right_ankle","right_foot_index"),
]


def research_pose_view(request, run, start: int, end: int):
    from ..api.schemas import Joint, PoseSample, PoseView
    state=request.app.state
    with state.db.tx() as conn:
        context=conn.execute("SELECT * FROM run_provenance WHERE run_id=?", (run.run_id,)).fetchone()
        if not context or not context["pose_set_id"] or not context["observation_set_id"]:
            found=state.poses.latest(run.bundle.video_id,run.owner_id)
            return PoseView(available=False,reason_code="no_linked_pose_data" if found else "no_pose_data")
        obs=conn.execute("SELECT metadata_json FROM observation_sets WHERE set_id=? AND owner_id=? AND video_id=?", (context["observation_set_id"],run.owner_id,run.bundle.video_id)).fetchone()
    if obs is None:
        return PoseView(available=False,reason_code="no_coordinate_transform")
    transform=FrameTransform.model_validate(json.loads(obs["metadata_json"])["transform"])
    assignment=TrackAssignmentSet.model_validate_json(context["assignment_json"])
    by_track={tid:a.participant_id for a in assignment.assignments for tid in a.track_ids}
    pose_set=state.poses.get(run.bundle.video_id,run.owner_id,context["pose_set_id"])
    if pose_set is None:
        return PoseView(available=False,reason_code="no_pose_data")
    frames=state.poses.load_poses(run.bundle.video_id,pose_set.set_id)
    selected={}
    for frame in frames:
        if frame.status in {"REJECTED", "NOT_RUN", "IDENTITY_UNRESOLVED"}:
            continue
        reasons = frame.model_extra.get("reasons", []) if frame.model_extra else []
        if any("CONTAMINATION" in str(reason) or "IDENTITY" in str(reason) for reason in reasons):
            continue
        participant=by_track.get(frame.track_id)
        if not participant or not frame.landmarks or not start<=frame.timestamp_us<end:
            continue
        joints=[]
        for name,mark in frame.landmarks.items():
            x,y=mark.x_px,mark.y_px
            missing=mark.finite is False or mark.off_image is True or x is None or y is None or (mark.state is not None and mark.state != "VALID")
            score=min(v for v in (mark.visibility,mark.presence) if v is not None) if mark.visibility is not None or mark.presence is not None else None
            if missing or not math.isfinite(x) or not math.isfinite(y):
                joints.append(Joint(name=name,state="missing",score=score))
                continue
            dx,dy=transform.to_display(x,y)
            if not (0<=dx<=transform.display_width and 0<=dy<=transform.display_height):
                joints.append(Joint(name=name,state="missing",score=score))
            else:
                joints.append(Joint(name=name,x=round(dx,3),y=round(dy,3),score=score))
        sample=PoseSample(time_us=frame.timestamp_us,participant_id=participant,joints=joints)
        key=(frame.timestamp_us,participant)
        # 재시도/다중 pose_index가 같은 트랙에 있으면 관측 점이 가장 많은 샘플 한 개만 표시한다.
        if key not in selected or sum(j.state=="observed" for j in joints)>sum(j.state=="observed" for j in selected[key].joints):
            selected[key]=sample
    return PoseView(available=bool(selected),reason_code=None if selected else "no_assigned_pose_samples",
                    landmark_set_id="mediapipe_pose_33",coordinate_space_id="analysis_px",
                    width=transform.display_width,height=transform.display_height,connections=CONNECTIONS,
                    samples=[selected[k] for k in sorted(selected)])
