"""팀 접근 경계와 실제 모델 없이 검증할 수 있는 분석 실행 계약."""
import copy
import hashlib
import json
import time

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.api.deps import User, authenticated_user
from app.domain.models import FrameTransform, ObservationImport, PoseImport
from app.services.observations import content_digest
from app.services.poses import content_digest as pose_digest
from app.settings import Settings
from app.storage.db import now_rfc3339
from .test_manual_import import add_video
from .test_observations import make_payload
from .test_poses import landmarks
from .test_team_events import TEAM_LINE


@pytest.fixture
def env(tmp_path, monkeypatch):
    app=create_app(Settings(data_dir=tmp_path/"data",enable_fixtures=False,media_secret="test"))
    client=TestClient(app)
    # 프레임 대조 자체는 FFmpeg 통합 시험에서 확인한다. 여기서는 고정 PTS로 작업 전달을 검증한다.
    monkeypatch.setattr("app.api.v1.observations.reference_pts",lambda *a:([i*33333 for i in range(90)],2))
    monkeypatch.setattr("app.api.v1.poses.reference_pts",lambda *a:([i*33333 for i in range(90)],2))
    return app,client


def key(client):
    r=client.post("/api/v1/analysis/keys",json={"label":"test-worker"})
    assert r.status_code==201,r.text
    return r.json(),{"Authorization":"Bearer "+r.json()["token"]}


def enqueue(client, kind="tracking", request_key="request-0001"):
    response=client.post("/api/v1/videos/vid1/analysis",json={"kind":kind,"request_key":request_key})
    assert response.status_code in {200,201},response.text
    return response.json()


def prepare(app):
    add_video(app.state.db,duration=3_000_000)
    payload=ObservationImport.model_validate(make_payload([i*33333 for i in range(90)]))
    obs=app.state.observations.create(owner_id="dev-user",video_id="vid1",payload=payload,digest=content_digest(payload),transform=payload.metadata.transform)
    app.state.designations.save(video_id="vid1",owner_id="dev-user",observation=obs,user=[1],opponent=[2])
    return obs


def test_solo_observations_are_not_counted_as_two_people(env):
    app,c=env
    obs=prepare(app)
    app.state.designations.save(video_id="vid1",owner_id="dev-user",observation=obs,user=[1],opponent=[])
    body=PoseImport.model_validate({"source":"test","poses":[{"frame_index":0,"timestamp_us":0,"track_id":1,"landmarks":landmarks()}]})
    app.state.poses.create(owner_id="dev-user",video_id="vid1",payload=body,digest=pose_digest(body))
    result=c.post("/api/v1/videos/vid1/actions",json={"events":[]})
    assert result.status_code==201,result.text
    summary=c.get(f"/api/v1/runs/{result.json()['run_id']}/observation-summary").json()
    assert summary["sample_count"]==1
    assert summary["both_observed_frames"]==0


def test_team_scope_keeps_private_data_and_enforces_readonly_and_revocation(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    tid=c.post("/api/v1/teams",json={"name":"테스트 체육관"}).json()["team_id"]
    assert c.get("/api/v1/workspace/sessions").json()["total"]==1
    c.put("/api/v1/teams/selection",json={"team_id":tid})
    assert c.get("/api/v1/workspace/sessions").json()["total"]==0
    assert c.get("/api/v1/videos/vid1/summary").status_code==404
    add_video(app.state.db,"team-video",owner="team:"+tid)
    assert c.get("/api/v1/workspace/sessions").json()["total"]==1
    with app.state.db.tx(immediate=True) as conn:
        conn.execute("INSERT INTO team_members VALUES(?,?,'viewer',?)",(tid,"viewer",now_rfc3339()))
    app.dependency_overrides[authenticated_user]=lambda:User(user_id="viewer")
    assert c.get("/api/v1/videos/team-video/summary").status_code==200
    assert c.post("/api/v1/workspace/athletes",json={"name":"forbidden"}).status_code==403
    assert c.delete("/api/v1/videos/team-video").status_code==403
    assert c.post("/api/v1/analysis/keys",json={"label":"forbidden"}).status_code==403
    assert c.get("/api/v1/analysis/keys").status_code==403
    assert c.put("/api/v1/teams/selection",json={"team_id":None}).status_code==200
    assert c.get("/api/v1/videos/team-video/summary").status_code==404
    c.cookies.set("workspace_team",tid)
    with app.state.db.tx(immediate=True) as conn:
        conn.execute("DELETE FROM team_members WHERE user_id='viewer'")
    assert c.get("/api/v1/workspace/sessions").status_code==403
    assert c.put("/api/v1/teams/selection",json={"team_id":None}).status_code==200


def test_owner_member_management_and_editor_cannot_issue_key(env):
    app,c=env
    tid=c.post("/api/v1/teams",json={"name":"team"}).json()["team_id"]
    uid=app.state.auth.create_user(email="coach@example.com",password="Strong-Coach-Pass#2026",display_name="Coach")
    body={"email":"coach@example.com","role":"editor"}
    assert c.put(f"/api/v1/teams/{tid}/members",json=body).status_code==200
    c.put("/api/v1/teams/selection",json={"team_id":tid})
    app.dependency_overrides[authenticated_user]=lambda:User(user_id=uid)
    assert c.post("/api/v1/workspace/athletes",json={"name":"team athlete"}).status_code==201
    assert c.post("/api/v1/analysis/keys",json={"label":"no"}).status_code==403
    assert c.put(f"/api/v1/teams/{tid}/members",json={**body,"role":"viewer"}).status_code==403
    assert c.delete(f"/api/v1/teams/{tid}/members/dev-user").status_code==403


def test_queue_idempotency_single_claim_lease_and_cancel(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    _,headers=key(c)
    first=enqueue(c)
    page=c.get("/api/v1/workspace/sessions?status=processing").json()
    assert page["counts"]["processing"]==1 and page["items"][0]["analysis_status"]=="queued"
    assert enqueue(c)["task_id"]==first["task_id"]
    assert c.post("/api/v1/videos/vid1/analysis",json={"kind":"tracking","request_key":"different-request"}).status_code==409
    assert c.post("/api/v1/worker/claim",json={}).status_code==401
    task=c.post("/api/v1/worker/claim",headers=headers,json={}).json()["task"]
    assert task["task_id"]==first["task_id"]
    assert c.post("/api/v1/worker/claim",headers=headers,json={}).json()=={"task":None}
    path=f"/api/v1/worker/tasks/{task['task_id']}"
    assert c.post(path+"/heartbeat",headers=headers,json={"lease":"x"*32}).status_code==409
    assert c.post(path+"/heartbeat",headers=headers,json={"lease":task["lease"],"progress":0.4}).status_code==200
    c.post(f"/api/v1/analysis/tasks/{task['task_id']}/cancel")
    assert c.post(path+"/heartbeat",headers=headers,json={"lease":task["lease"]}).status_code==409
    assert c.post(path+"/result",headers=headers,json={"lease":task["lease"],"source":"test","observations":make_payload([i*33333 for i in range(90)])}).status_code==409


def test_expired_lease_requeues_three_times_and_key_revocation_blocks_input(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    k,h=key(c)
    row=enqueue(c)
    old=None
    for i in range(3):
        task=c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"]
        assert task and task["lease"]!=old
        old=task["lease"]
        with app.state.db.tx(immediate=True) as conn:
            conn.execute("UPDATE analysis_tasks SET lease_until=0")
    assert c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"] is None
    assert c.get("/api/v1/analysis/tasks").json()[0]["status"]=="failed"
    c.delete("/api/v1/analysis/keys/"+k["key_id"])
    assert c.get(f"/api/v1/worker/tasks/{row['task_id']}/input",headers={**h,"X-Analysis-Lease":old}).status_code==401


def test_worker_scope_and_original_download_hash(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    original=app.state.settings.originals_dir/"vid1"/"source.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"private-test-video")
    with app.state.db.tx(immediate=True) as conn:
        conn.execute("UPDATE videos SET source_path=?,sha256=?",(str(original),hashlib.sha256(original.read_bytes()).hexdigest()))
    _,h=key(c)
    enqueue(c)
    t=c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"]
    r=c.get(t["input_path"],headers={**h,"X-Analysis-Lease":t["lease"]})
    assert r.status_code==200 and r.content==original.read_bytes()
    app.dependency_overrides[authenticated_user]=lambda:User(user_id="other")
    _,foreign=key(c)
    assert c.post("/api/v1/worker/claim",headers=foreign,json={}).json()["task"] is None
    assert c.get(t["input_path"],headers={**foreign,"X-Analysis-Lease":t["lease"]}).status_code==409


def test_action_result_is_atomic_idempotent_designated_and_pose_overlay_is_snapshot(env):
    app,c=env
    prepare(app)
    _,h=key(c)
    enqueue(c,"actions")
    task=c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"]
    points=landmarks()
    points["left_wrist"].update(x_px=None,y_px=None,finite=False)
    pose={"source":"test","poses":[{"frame_index":0,"timestamp_us":0,"track_id":1,"participant_id":"candidate:1","landmarks":points}]}
    body={"lease":task["lease"],"source":"unit-model","poses":pose,"events":[{**TEAM_LINE,"track_id":1,"start_timestamp_us":0,"end_timestamp_us":100000,"start_frame_index":0,"end_frame_index":3}]}
    path=f"/api/v1/worker/tasks/{task['task_id']}/result"
    result=c.post(path,headers=h,json=body)
    assert result.status_code==200,result.text
    rid=result.json()["run_id"]
    assert c.post(path,headers=h,json=body).json()["run_id"]==rid
    assert len(c.get("/api/v1/runs").json())==1
    event=c.get(f"/api/v1/runs/{rid}/timeline").json()["events"][0]
    assert event["participant_id"]=="A" and event["role"]=="USER"
    assert event["source_participant_id"]=="candidate:0" and event["verified"] is False and event["outcome"]=="UNKNOWN"
    view=c.get(f"/api/v1/runs/{rid}/poses").json()
    assert view["available"] and view["width"]==320
    assert len(view["samples"][0]["joints"])==33
    assert view["samples"][0]["joints"][0]["x"]==160
    assert next(j for j in view["samples"][0]["joints"] if j["name"]=="left_wrist")["state"]=="missing"
    obs=app.state.observations.latest("vid1","dev-user")
    app.state.designations.save(video_id="vid1",owner_id="dev-user",observation=obs,user=[2],opponent=[1])
    assert c.get(f"/api/v1/runs/{rid}/poses").json()==view
    assert c.post(path,headers=h,json={**body,"source":"different"}).status_code==409
    corrected=c.post(f"/api/v1/runs/{rid}/corrections",json={"user_participant_id":"B"})
    assert corrected.status_code==201,corrected.text
    new_id=corrected.json()["run_id"]
    assert c.get(f"/api/v1/runs/{new_id}/poses").json()==view
    event=c.get(f"/api/v1/runs/{new_id}/timeline").json()["events"][0]
    assert event["participant_id"]=="A" and event["role"]=="OPPONENT"
    assert c.get(f"/api/v1/runs/{rid}/timeline").json()["events"][0]["role"]=="USER"


def test_stale_designation_invalid_result_and_zero_candidates(env):
    app,c=env
    obs=prepare(app)
    _,h=key(c)
    enqueue(c,"actions")
    task=c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"]
    path=f"/api/v1/worker/tasks/{task['task_id']}/result"
    assert c.post(path,headers=h,json={"lease":task["lease"],"source":"test","events":[{**TEAM_LINE,"verified":True}]}).status_code==422
    app.state.designations.save(video_id="vid1",owner_id="dev-user",observation=obs,user=[2],opponent=[1])
    assert c.post(path,headers=h,json={"lease":task["lease"],"source":"test","events":[]}).status_code==409
    c.post(f"/api/v1/analysis/tasks/{task['task_id']}/cancel")
    enqueue(c,"actions","new-request-key")
    task=c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"]
    response=c.post(f"/api/v1/worker/tasks/{task['task_id']}/result",headers=h,json={"lease":task["lease"],"source":"test","events":[]})
    assert response.status_code==200,response.text
    assert c.get(f"/api/v1/runs/{response.json()['run_id']}").json()["counts"]["events"]==0
    run_id=response.json()["run_id"]
    pending=c.get("/api/v1/workspace/sessions?status=pending").json()
    assert pending["total"]==1
    reviewed=c.put(f"/api/v1/workspace/runs/{run_id}/review",json={"state":"reviewed","note":"후보 0건. 원본과 관절 관측을 검토함","expected_revision":0})
    assert reviewed.status_code==200,reviewed.text
    assert c.get(f"/api/v1/workspace/runs/{run_id}/report").json()["events"]==[]



def test_tracking_result_validates_pts_and_accepts_only_tracking_contract(env):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    _,h=key(c)
    enqueue(c)
    task=c.post("/api/v1/worker/claim",headers=h,json={}).json()["task"]
    path=f"/api/v1/worker/tasks/{task['task_id']}/result"
    payload=make_payload([i*33333 for i in range(90)])
    wrong=copy.deepcopy(payload)
    wrong["tracks"][0]["timestamp_us"]=999
    assert c.post(path,headers=h,json={"lease":task["lease"],"source":"test","observations":wrong}).status_code==422
    assert app.state.observations.latest("vid1","dev-user") is None
    result=c.post(path,headers=h,json={"lease":task["lease"],"source":"test","observations":payload})
    assert result.status_code==200,result.text
    assert result.json()["observation_set_id"]
    assert c.get("/api/v1/videos/vid1/designation").json()["state"]=="NOT_RUN"


def test_stale_upload_cleanup_respects_active_operations_and_completed_video(env):
    app,c=env
    service=app.state.uploads
    one,_=service.create("dev-user","one.mp4",200,"boxing","one")
    two,_=service.create("dev-user","two.mp4",200,"boxing","two")
    service._dir(one.upload_id).joinpath("part-000000").write_bytes(b"partial")
    service._dir(two.upload_id).joinpath("part-000000").write_bytes(b"active")
    with app.state.db.tx(immediate=True) as conn:
        conn.execute("UPDATE uploads SET updated_at='2020-01-01T00:00:00+00:00'")
    with service._exclusive("dev-user",two.upload_id):
        assert service.sweep_stale()==1
        assert not service._dir(one.upload_id).exists()
        assert service._dir(two.upload_id).exists()
    assert service.sweep_stale()==1
    assert service.get("dev-user",one.upload_id).status=="aborted"


def test_model_adapter_sdk_roundtrip_over_real_http(env):
    import socket
    import threading
    import uvicorn
    from scripts.analysis_worker import WorkerClient
    app,c=env
    prepare(app)
    original=app.state.settings.originals_dir/"vid1"/"source.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"sdk-transport-test-source")
    with app.state.db.tx(immediate=True) as conn:
        conn.execute("UPDATE videos SET source_path=?,sha256=?",(str(original),hashlib.sha256(original.read_bytes()).hexdigest()))
    credentials,_=key(c)
    enqueue(c,"actions")
    sock=socket.socket()
    sock.bind(("127.0.0.1",0))
    server=uvicorn.Server(uvicorn.Config(app,log_level="critical"))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.01)
    sdk=WorkerClient(f"http://127.0.0.1:{sock.getsockname()[1]}",credentials["token"])
    seen=[]
    def adapter(task,path,progress):
        assert path.read_bytes()==original.read_bytes()
        assert task["assignment"]["state"]=="ASSIGNED"
        seen.append(path)
        progress(.5,"test adapter")
        return {"source":"transport-test-only","events":[]}
    try:
        assert server.started
        task=sdk.call("/worker/claim",{"kinds":["actions"]})["task"]
        sdk.process(task,adapter)
        assert c.get("/api/v1/analysis/tasks").json()[0]["status"]=="completed"
        assert seen and not seen[0].exists()  # 원본 임시 사본도 정리
        with pytest.raises(ValueError):
            WorkerClient("http://example.com",credentials["token"])
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        sock.close()


@pytest.mark.parametrize("kind",["tracking","actions"])
def test_concurrent_claim_and_result_delivery_are_exactly_once(env,kind):
    from concurrent.futures import ThreadPoolExecutor
    app,c=env
    prepare(app)
    _,headers=key(c)
    enqueue(c,kind)
    def claim(_):
        with TestClient(app) as other:
            return other.post("/api/v1/worker/claim",headers=headers,json={}).json()["task"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims=list(pool.map(claim,range(6)))
    assert len([x for x in claims if x])==1
    task=next(x for x in claims if x)
    body={"lease":task["lease"],"source":"concurrency-test"}
    if kind=="actions":
        body["events"]=[]
    else:
        body["observations"]=make_payload([i*33333 for i in range(90)])
    def submit(_):
        with TestClient(app) as other:
            return other.post(f"/api/v1/worker/tasks/{task['task_id']}/result",headers=headers,json=body)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses=list(pool.map(submit,range(6)))
    assert all(r.status_code==200 for r in responses),[(r.status_code,r.text) for r in responses]
    assert all(r.json()==responses[0].json() for r in responses)
    with app.state.db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspace_activity WHERE action='analysis_completed'").fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]==(1 if kind=="actions" else 0)
