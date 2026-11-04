from types import SimpleNamespace
from app.api.schemas import Joint, PoseSample, PoseView
from app.services.coaching import coaching_draft


def view(*, score=.9, gap=50000, participant="A", wrist_y=200):
    return PoseView(available=True,height=600,samples=[PoseSample(participant_id=participant,time_us=i*gap,joints=[
        Joint(name=n,x=x,y=y,score=score) for n,x,y in [("left_shoulder",100,100),("right_shoulder",200,100),("left_hip",110,300),("right_hip",190,300),("left_wrist",100,wrist_y)]]) for i in range(20)])


def test_hand_height_draft_links_measured_user_scene_without_hit_claims():
    draft=coaching_draft(view(),[SimpleNamespace(participant_id="A",role="USER")],2000000)
    card=draft["cards"][0]
    assert card["measured_percent"]==100
    assert card["status"]=="확인할 장면"
    assert card["from_us"]==0 and card["to_us"]<=2000000
    assert "확인하면" in card["practice"]
    assert draft["kind"]=="규칙 기반 코칭 초안"


def test_low_quality_and_opponent_do_not_create_user_coaching():
    roles=[SimpleNamespace(participant_id="A",role="USER")]
    assert not coaching_draft(view(score=.4),roles,2000000)["cards"]
    assert not coaching_draft(view(participant="B"),roles,2000000)["cards"]


def test_observation_gaps_are_not_joined_into_sustained_hand_drop():
    card=coaching_draft(view(gap=200000),[SimpleNamespace(participant_id="A",role="USER")],5000000)["cards"][0]
    assert card["status"]=="위치 관찰"
    assert "판단할 수 없습니다" in card["interpretation"]


def test_no_low_position_is_not_called_good_defence():
    card=coaching_draft(view(wrist_y=50),[SimpleNamespace(participant_id="A",role="USER")],2000000)["cards"][0]
    assert card["measured_percent"]==0 and card["status"]=="위치 관찰"


def test_missing_hips_use_explicit_upper_body_measurement_without_inventing_hips():
    poses=view()
    for sample in poses.samples:
        sample.joints=[j for j in sample.joints if "hip" not in j.name]
    draft=coaching_draft(poses,[SimpleNamespace(participant_id="A",role="USER")],2000000)
    card=draft["cards"][0]
    assert card["measurement_basis"]=="upper_body"
    assert card["eligible_samples"]==20
    assert "35%" in card["measurement_note"]
    assert draft["diagnostics"]["left"]["torso_samples"]==0


def test_side_view_without_usable_shoulder_width_does_not_create_fallback():
    poses=view()
    for sample in poses.samples:
        sample.joints=[j for j in sample.joints if "hip" not in j.name]
        next(j for j in sample.joints if j.name=="right_shoulder").x=101
    draft=coaching_draft(poses,[SimpleNamespace(participant_id="A",role="USER")],2000000)
    assert not draft["cards"]
    assert "12" in draft["insufficient_reason"]
