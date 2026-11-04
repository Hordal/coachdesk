from types import SimpleNamespace
import pytest

from app.api.schemas import Joint, PoseSample, PoseView
from app.services.analysis_bridge import analyze_rules
from app.domain.states import StateParams, compute_features
from .test_states import pose
from .test_analysis import attack, state
from .test_workflow import env, prepare  # noqa: F401
from .test_manual_import import add_video


def view(score=.9,gap=50000):
    return PoseView(available=True,samples=[PoseSample(time_us=i*gap,participant_id="A",joints=[Joint(name=n,x=x,y=y,score=score) for n,x,y in [
        ("nose",150,50),("left_shoulder",100,100),("right_shoulder",200,100),("left_hip",110,300),("right_hip",190,300),("left_wrist",140,70),("right_wrist",160,70)]]) for i in range(20)])


def bundle(events=None,states=None):
    def record(value):return SimpleNamespace(model_dump=lambda **kw:value)
    return SimpleNamespace(events=[record(v) for v in (events or [])],state_intervals=[record(v) for v in (states or [])],video_id="v",duration_us=10000000)


def test_bridge_reuses_guard_rules_without_guessing_stance_or_confirming_attacks():
    result=analyze_rules(view(),bundle([{**attack(),"status":"CANDIDATE","verified":False}]))
    assert result["states"]
    assert {s["body_part"] for s in result["states"]}=={"left_hand","right_hand"}
    assert result["guard_return_status"]=="held" and result["guard_return"] is None
    assert result["confirmed_attacks"]==0 and result["candidate_events"]==1


def test_low_quality_and_long_gaps_cannot_make_guard_intervals():
    assert not analyze_rules(view(score=.4),bundle())["states"]
    assert not analyze_rules(view(gap=500000),bundle())["states"]


def test_confirmed_inputs_can_use_upstream_guard_return_engine():
    result=analyze_rules(view(),bundle([attack()],[state("s1","low_guard",1000000,1600000),state("s2","high_guard",1600000,2000000)]))
    assert result["guard_return_status"]=="calculated"
    assert result["guard_return"]["assessments"][0]["value"]=="present"


@pytest.mark.parametrize("invalid",["presence","state","coordinate","rejected","contaminated"])
def test_original_state_engine_rejects_bad_quality_fields(invalid):
    row=pose(0,wrist_y=130)
    if invalid=="presence":row.landmarks["nose"].presence=.1
    elif invalid=="state":row.landmarks["nose"].state="UNKNOWN"
    elif invalid=="coordinate":row.landmarks["nose"].x_px=float("nan")
    elif invalid=="rejected":row.status="REJECTED"
    else:row= row.model_copy(update={"reasons":["CROSS_PERSON_CONTAMINATION_SUSPECTED"]})
    assert compute_features(row,StateParams()).unusable in {"torso_invalid","pose_rejected"}


def test_rule_endpoint_preserves_ownership_and_existing_results(env):
    app,c=env
    prepare(app)
    run=c.post("/api/v1/videos/vid1/actions",json={"events":[]}).json()["run_id"]
    response=c.get(f"/api/v1/runs/{run}/rule-analysis")
    assert response.status_code==200,response.text
    assert response.json()["guard_return_status"]=="held"
    from app.api.deps import User,authenticated_user
    app.dependency_overrides[authenticated_user]=lambda:User(user_id="someone-else")
    assert c.get(f"/api/v1/runs/{run}/rule-analysis").status_code==404
    assert c.get(f"/api/v1/runs/{run}/timeline").status_code==404


def test_timeline_connects_frozen_guard_states_and_pose_observation_spans(env):
    from app.domain.models import PoseImport
    from app.services.poses import content_digest
    app,c=env
    prepare(app)
    rows=[]
    for i in range(20):
        row=pose(i,wrist_y=130).model_dump(mode="json")
        row["track_id"]=1
        rows.append(row)
    payload=PoseImport.model_validate({"source":"test","poses":rows})
    app.state.poses.create(owner_id="dev-user",video_id="vid1",payload=payload,digest=content_digest(payload))
    run=c.post("/api/v1/videos/vid1/actions",json={"events":[]}).json()["run_id"]
    response=c.get(f"/api/v1/runs/{run}/timeline")
    assert response.status_code==200,response.text
    result=response.json()
    assert result["states"] and result["derived_state_count"]==len(result["states"])
    assert result["observations"][0]["participant_id"]=="A"
    assert result["observations"][0]["sample_count"]==20
    empty=c.get(f"/api/v1/runs/{run}/timeline?from_us=900000&to_us=1000000").json()
    assert not empty["states"] and not empty["observations"]


def test_pose_observation_spans_do_not_fill_long_gaps():
    from app.services.analysis_bridge import observation_spans
    spans=observation_spans(view(gap=500000),10000000)
    assert len(spans)==20
    assert all(s["sample_count"]==1 and s["end_us"]-s["start_us"]==1 for s in spans)
