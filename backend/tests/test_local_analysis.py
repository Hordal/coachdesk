"""가짜 추론을 운영 화면에 넣지 않고 내장 분석의 규칙·큐 경계·표시 계약을 시험한다."""
import json
import time

from fastapi import Request
from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.api.v1.analysis import Claim, claim_for_engine
from app.services.local_inference import BoxTracker, punch_candidates
from app.services.local_model import model_ready
from app.settings import Settings
from .test_manual_import import add_video
from .test_observations import make_payload


def frame(index,extension,*,state="VALID"):
    marks={name:{"x_px":x,"y_px":100.,"state":"VALID"} for name,x in
           [("left_shoulder",100.),("right_shoulder",200.),("left_elbow",140.),("left_wrist",100.+extension),
            ("right_elbow",230.),("right_wrist",230.)]}
    marks["left_wrist"]["state"]=state
    return {"frame_index":index,"timestamp_us":index*50_000,"track_id":1,"landmarks":marks}


def test_extension_and_retraction_is_only_an_unverified_candidate():
    rows=[frame(i,v) for i,v in enumerate([20,25,40,80,130,170,120,70])]
    events=punch_candidates(rows,{1},1_000_000)
    assert len(events)==1
    event=events[0]
    assert event["status"]=="CANDIDATE" and event["outcome"]=="UNKNOWN" and event["verified"] is False
    assert event["role"]=="UNRESOLVED" and event["track_id"]==1
    assert event["synthetic"] is False
    assert "HEURISTIC_NOT_A_CLASSIFIER" in event["limitations"]
    assert event["start_timestamp_us"]<event["end_timestamp_us"]<=1_000_000
    assert punch_candidates(rows,{2},1_000_000)==[]


def test_missing_joint_time_gaps_or_missing_retraction_do_not_invent_events():
    rows=[frame(i,v) for i,v in enumerate([20,25,40,80,130,170,120,70])]
    rows[5]=frame(5,170,state="UNKNOWN")
    assert punch_candidates(rows,{1},1_000_000)==[]
    rows=[frame(i,v) for i,v in enumerate([20,25,40,80,130,170])]
    assert punch_candidates(rows,{1},1_000_000)==[]
    rows=[frame(i,v) for i,v in enumerate([20,25,40,80,130,170,120,70])]
    for row in rows[6:]:
        row["timestamp_us"]+=500_000
    assert punch_candidates(rows,{1},2_000_000)==[]


def test_tracker_uses_observations_and_never_reuses_long_lost_identity():
    tracker=BoxTracker()
    def detection(x,color):
        return {"bbox_norm":(x,.1,x+.1,.9),"center":(x+.05,.5),"color":color}
    a,b=detection(.1,(1,0,0)),detection(.7,(0,0,1))
    assert tracker.assign([a,b],0)==[1,2]
    assert tracker.assign([b,a],40_000)==[2,1]  # 출력 순서가 신원을 결정하지 않음
    assert tracker.assign([],80_000)==[]
    assert tracker.assign([a],1_000_000)==[3]  # 긴 가림을 추측 연결하지 않음


@pytest.fixture
def env(tmp_path,monkeypatch):
    app=create_app(Settings(data_dir=tmp_path/"data",enable_fixtures=False,media_secret="test"))
    monkeypatch.setattr("app.services.local_model.installed",lambda:True)
    monkeypatch.setattr("app.api.v1.observations.reference_pts",lambda *a:([i*33333 for i in range(90)],2))
    return app,TestClient(app)


def test_local_jobs_are_isolated_from_external_claims_and_keys(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    task=c.post("/api/v1/videos/vid1/analysis",json={"kind":"tracking","engine":"local","request_key":"local-request"})
    assert task.status_code==201,task.text
    key=c.post("/api/v1/analysis/keys",json={"label":"external"}).json()
    assert c.post("/api/v1/worker/claim",headers={"Authorization":"Bearer "+key["token"]},json={}).json()["task"] is None
    from app.jobs.local_analysis import LocalWorker
    worker=LocalWorker(app.state.settings)
    internal=worker.key_for("dev-user")
    request=Request({"type":"http","method":"POST","path":"/internal","headers":[],"app":app})
    claimed=claim_for_engine(Claim(),request,internal,"local")["task"]
    assert claimed["task_id"]==task.json()["task_id"]
    assert len(c.get("/api/v1/analysis/keys").json())==1


def test_queued_external_job_can_be_explicitly_switched_to_local(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    task=c.post("/api/v1/videos/vid1/analysis",json={"kind":"tracking","request_key":"external-request"}).json()
    response=c.post(f"/api/v1/analysis/tasks/{task['task_id']}/use-local")
    assert response.json()=={"status":"queued","engine":"local"}
    assert c.get("/api/v1/analysis/tasks").json()[0]["engine"]=="local"
    assert c.post("/api/v1/analysis/tasks/unknown/use-local").status_code==404


def test_local_worker_submits_real_contract_path_and_writes_pose_cache(env,monkeypatch):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    c.post("/api/v1/videos/vid1/analysis",json={"kind":"tracking","engine":"local","request_key":"local-worker-request"})
    from app.jobs.local_analysis import LocalWorker
    output={"observations":make_payload([i*33333 for i in range(90)]),"poses":{"source":"unit-test","poses":[],"quality":[],"windows":[]}}
    monkeypatch.setattr("app.jobs.local_analysis.infer_video",lambda path,progress:output)
    worker=LocalWorker(app.state.settings)
    assert worker.run_once() is True
    task=c.get("/api/v1/analysis/tasks").json()[0]
    assert task["status"]=="completed",task
    obs=task["result"]["observation_set_id"]
    assert json.loads((app.state.settings.data_dir/"local-analysis/vid1"/(obs+".json")).read_text())["poses"]==[]
    assert task["result"]["track_count"]==3
    c.delete("/api/v1/videos/vid1",params={"scope":"all"})
    assert not (app.state.settings.data_dir/"local-analysis/vid1").exists()


def test_absent_or_corrupt_model_is_not_treated_as_available(tmp_path):
    missing=tmp_path/"missing.task"
    assert not model_ready(missing)
    missing.write_bytes(b"not-a-model")
    assert not model_ready(missing)
