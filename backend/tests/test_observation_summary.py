from types import SimpleNamespace
from app.api.v1.runs import observation_summary
from app.api.schemas import PoseSample, PoseView


def test_summary_counts_only_observed_unique_frames_without_inventing_findings(monkeypatch):
    run = SimpleNamespace(bundle=SimpleNamespace(video_id="v1",duration_us=1000000, events=[], participants=[
        SimpleNamespace(participant_id="A", role="USER"), SimpleNamespace(participant_id="B", role="OPPONENT")]))
    view = PoseView(available=True, samples=[PoseSample(time_us=t,participant_id=p,joints=[]) for t,p in [(100,"A"),(200,"A"),(200,"B")]])
    monkeypatch.setattr("app.api.v1.runs.get_poses", lambda **kwargs: view)
    result = observation_summary(None,run,None)
    assert result["sample_count"] == 3
    assert result["participants"][0]["frame_count"] == 2
    assert result["participants"][1]["frame_count"] == 1
    assert result["both_observed_frames"] == 1
    assert result["suggested_time_us"] == 200
    assert result["event_count"] == 0
    assert "findings" not in result


def test_summary_without_poses_keeps_missing_values(monkeypatch):
    run = SimpleNamespace(bundle=SimpleNamespace(video_id="v1",duration_us=1000000, events=[], participants=[SimpleNamespace(participant_id="A",role="USER")]))
    monkeypatch.setattr("app.api.v1.runs.get_poses",lambda **kwargs: PoseView(available=False,reason_code="no_pose_data"))
    result = observation_summary(None,run,None)
    assert result["suggested_time_us"] is None
    assert result["participants"][0]["first_us"] is None
    assert result["available"] is False
