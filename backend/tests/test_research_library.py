"""Research results must retain ownership, original timing and quality boundaries."""
import hashlib
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.services import research_handoff as handoff
from app.settings import Settings
from .test_manual_import import add_video
from .test_observations import make_payload
from .test_poses import landmarks
from .test_workflow import env  # noqa: F401


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/absolute", "root/../../escape", "root\\..\\escape"])
def test_archive_unsafe_paths_rejected(path):
    with pytest.raises(handoff.HandoffError):
        handoff.safe_relative(path)


def test_corrupt_archive_does_not_activate(tmp_path):
    archive=tmp_path/"bad.zip"
    with ZipFile(archive,"w") as z:
        z.writestr("root/FILE_INDEX.jsonl",json.dumps({"path":"data.json","bytes":2,"sha256":hashlib.sha256(b"{}").hexdigest()}))
        z.writestr("root/data.json",b"bad")
    target=tmp_path/"activated"
    with pytest.raises(handoff.HandoffError,match="무결성"):
        handoff.prepare(archive,target)
    assert not target.exists()


def test_public_catalog_hides_unreviewed_sources_and_private_paths(tmp_path,monkeypatch):
    items=[dict(clip_id=id,filename="sample.mp4",source_url=None,frame_count=30,has_research_pose=True,public_preview=id=="01",source="private",source_sha256="h") for id in ("01","02")]
    monkeypatch.setattr(handoff,"catalog",lambda *args:items)
    app=create_app(Settings(data_dir=tmp_path/"public",public_demo=True,enable_fixtures=False))
    c=TestClient(app)
    body=c.get("/api/v1/research/clips").json()
    assert [item["clip_id"] for item in body["items"]]==["01"]
    assert "source_sha256" not in body["items"][0] and "source" not in body["items"][0]
    assert c.post("/api/v1/research/clips/02/import").status_code==404


def test_payload_excludes_old_image_candidate_and_other_clips(tmp_path):
    (tmp_path/"metadata.json").write_text("{}")
    (tmp_path/"tracking.jsonl").write_text("{}\n")
    folder=tmp_path/"results/mvp_phase1c_participant_temporal_pose"
    folder.mkdir(parents=True)
    def write(name,rows):
        (folder/name).write_text("\n".join(json.dumps(r) for r in rows))
    write("pose_quality_frames.jsonl",[{"clip_id":"01","mode":"IMAGE"},{"clip_id":"02","mode":"VIDEO"},{"clip_id":"01","mode":"VIDEO","track_id":0}])
    write("action_candidates.jsonl",[{"clip_id":"01","mode":"IMAGE","status":"CANDIDATE"},{"clip_id":"01","mode":"VIDEO","status":"CANDIDATE","contamination_gates":["CONTAMINATION"]}])
    _,poses,actions=handoff.payloads({"clip_id":"01","metadata":"metadata.json","tracking":"tracking.jsonl","detections":"missing"},tmp_path)
    assert poses["poses"]==[{"clip_id":"01","mode":"VIDEO","track_id":0}]
    assert not actions


def test_first_import_uses_verified_upload_chunks_and_reuses_video(env,monkeypatch,media_dir):
    app,c=env
    data=(media_dir/"demo-video-01.mp4").read_bytes()
    clip={"clip_id":"01","filename":"sample.mp4","source_sha256":hashlib.sha256(data).hexdigest(),"public_preview":True}
    monkeypatch.setattr(handoff,"catalog",lambda *args:[clip])
    monkeypatch.setattr(handoff,"source_bytes",lambda _:data)
    first=c.post("/api/v1/research/clips/01/import")
    assert first.status_code==200,first.text
    assert first.json()["reused"] is False
    second=c.post("/api/v1/research/clips/01/import")
    assert second.status_code==200 and second.json()["reused"] is True
    assert second.json()["video_id"]==first.json()["video_id"]


def test_low_disk_space_is_explained_before_creating_upload(env,monkeypatch):
    app,c=env
    monkeypatch.setattr(handoff,"clip_by_id",lambda *args,**kw:{"filename":"sample.mp4","source_sha256":"h"})
    monkeypatch.setattr(handoff,"source_bytes",lambda _:b"video")
    monkeypatch.setattr("app.api.v1.research.shutil.disk_usage",lambda _:SimpleNamespace(free=0))
    response=c.post("/api/v1/research/clips/01/import")
    assert response.status_code==507 and response.json()["code"]=="research_storage_full"
    assert not app.state.videos.list_for_owner("dev-user")


def test_research_import_selection_report_and_quality_gates(env,monkeypatch):
    app,c=env
    add_video(app.state.db,duration=3_000_000)
    add_video(app.state.db,"foreign",owner="someone-else")
    clip={"clip_id":"01","source_sha256":"h","public_preview":True,"has_research_pose":True}
    monkeypatch.setattr(handoff,"catalog",lambda:[clip])
    obs=make_payload([i*33333 for i in range(90)])
    obs["source"]=handoff.SOURCE_LABEL
    points=landmarks()
    points["left_wrist"]["state"]="LOW_VISIBILITY"
    poses={"source":handoff.SOURCE_LABEL,"poses":[
        {"frame_index":0,"timestamp_us":0,"track_id":1,"status":"FEATURE_ELIGIBLE","landmarks":points},
        {"frame_index":1,"timestamp_us":33333,"track_id":1,"status":"REJECTED","landmarks":landmarks()},
        {"frame_index":2,"timestamp_us":66666,"track_id":1,"status":"FEATURE_ELIGIBLE","reasons":["CROSS_PERSON_CONTAMINATION_SUSPECTED"],"landmarks":landmarks()},
        {"frame_index":3,"timestamp_us":99999,"track_id":None,"status":"FEATURE_ELIGIBLE","landmarks":landmarks()}]}
    monkeypatch.setattr(handoff,"payloads",lambda _: (obs,poses,[]))
    assert c.post("/api/v1/videos/foreign/research/observations").status_code==404
    assert c.post("/api/v1/videos/vid1/research/actions").status_code==409
    attached=c.post("/api/v1/videos/vid1/research/observations")
    assert attached.status_code==200,attached.text
    assert c.get("/api/v1/videos/vid1/research").json()["attached"]
    saved=c.put("/api/v1/videos/vid1/designation",json={"set_id":attached.json()["set_id"],"user_track_ids":[1],"opponent_track_ids":[2],"mode":"sparring"})
    assert saved.status_code==200,saved.text
    result=c.post("/api/v1/videos/vid1/research/actions")
    assert result.status_code==200,result.text
    rid=result.json()["run_id"]
    view=c.get(f"/api/v1/runs/{rid}/poses").json()
    assert len(view["samples"])==1
    wrist=next(j for j in view["samples"][0]["joints"] if j["name"]=="left_wrist")
    assert wrist["state"]=="missing"
    summary=c.get(f"/api/v1/runs/{rid}/observation-summary").json()
    assert summary["event_count"]==0
    assert not summary["coaching"]["cards"] and summary["coaching"]["insufficient_reason"]
    assert c.get(f"/api/v1/workspace/runs/{rid}/report").status_code==200
    with app.state.db.tx(immediate=True) as conn:
        conn.execute("UPDATE videos SET sha256='different' WHERE video_id='vid1'")
    assert c.post("/api/v1/videos/vid1/research/observations").status_code==404
