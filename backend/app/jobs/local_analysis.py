"""별도 프로세스에서 내장 CPU 추론을 수행한다. 기존 작업 임대·취소·결과 검증을 재사용한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from fastapi import Request

from ..api.main import create_app
from ..api.errors import ProblemError
from ..api.v1.analysis import Claim, Failure, Heartbeat, Result, _valid, claim_for_engine, fail, heartbeat, result
from ..services.local_inference import infer_video, punch_candidates
from ..services.local_model import ENGINE_VERSION, installed
from ..settings import Settings
from ..storage.db import now_rfc3339

log=logging.getLogger("local-analysis")


class LocalWorker:
    def __init__(self,settings:Settings):
        self.app=create_app(settings)
        self.db=self.app.state.db
        self.settings=settings
        self.worker_id=uuid.uuid4().hex
        self.request=Request({"type":"http","method":"POST","path":"/internal/local-analysis","headers":[],"app":self.app})

    def key_for(self,owner:str) -> dict:
        kid="local-"+hashlib.sha256(owner.encode()).hexdigest()[:24]
        with self.db.tx(immediate=True) as conn:
            conn.execute("INSERT INTO analysis_keys(key_id,owner_id,label,token_hash,created_at,internal,last_seen) VALUES(?,?,?,?,?,1,?) ON CONFLICT(key_id) DO UPDATE SET revoked_at=NULL,last_seen=excluded.last_seen",
                         (kid,owner,"내장 CPU 분석",hashlib.sha256(secrets.token_bytes(32)).hexdigest(),now_rfc3339(),time.time()))
        return {"key_id":kid,"owner_id":owner}

    def run_once(self) -> bool:
        with self.db.tx() as conn:
            owners=[r[0] for r in conn.execute("SELECT DISTINCT owner_id FROM analysis_tasks WHERE engine='local' AND status IN ('queued','running')")]
        for owner in owners:
            key=self.key_for(owner)
            task=claim_for_engine(Claim(),self.request,key,"local")["task"]
            if task:
                self.process(task,key)
                return True
        return False

    def process(self,task:dict,key:dict):
        task_id,lease=task["task_id"],task["lease"]
        stop=threading.Event()
        lost=threading.Event()
        lock=threading.Lock()
        progress_state={"progress":0.,"message":"내장 분석 시작"}

        def pulse():
            while not stop.wait(10):
                try:
                    with lock:
                        body=Heartbeat(lease=lease,**progress_state)
                    heartbeat(task_id,body,self.request,key)
                except Exception:
                    lost.set()
                    return

        thread=threading.Thread(target=pulse,daemon=True)
        thread.start()
        last_check=0.

        def progress(value:float,message:str):
            nonlocal last_check
            if lost.is_set():
                raise ProblemError(409,"lease_lost","작업이 취소되었거나 중단되었습니다")
            with lock:
                progress_state.update(progress=value,message=message)
            now=time.monotonic()
            if now-last_check>=1:
                heartbeat(task_id,Heartbeat(lease=lease,progress=value,message=message),self.request,key)
                last_check=now

        try:
            video=self.app.state.videos.get_for_owner(task["video"]["video_id"],key["owner_id"])
            if video is None or video.deleting:
                raise RuntimeError("영상을 찾을 수 없습니다")
            cache_dir=self.settings.data_dir/"local-analysis"/video.video_id
            cache_dir.mkdir(parents=True,exist_ok=True)
            if task["kind"]=="tracking":
                progress(.01,"원본 PTS 확인·포즈 모델 준비")
                output=infer_video(Path(video.source_path),progress)
                # 추론이 완료된 실제 관측만 사용한다. 원본 PTS와 프레임 수 검사는 기존 입력 API에서 다시 수행한다.
                progress(.9,"원본과 검출·추적 결과 대조")
                staged=cache_dir/(task_id+".json")
                temp=staged.with_suffix(".tmp")
                temp.write_text(json.dumps(output["poses"],separators=(",",":")),encoding="utf-8")
                os.replace(temp,staged)
                saved=result(task_id,Result(lease=lease,source=ENGINE_VERSION,observations=output["observations"]),self.request,key)
                target=cache_dir/(saved["observation_set_id"]+".json")
                os.replace(staged,target)
            else:
                cache=cache_dir/(task["observation_set_id"]+".json")
                if not cache.is_file():
                    with self.db.tx() as conn:
                        source=conn.execute("SELECT task_id FROM analysis_tasks WHERE video_id=? AND status='completed' AND json_extract(result_json,'$.observation_set_id')=? ORDER BY updated_at DESC LIMIT 1",(video.video_id,task["observation_set_id"])).fetchone()
                    staged=cache_dir/(source["task_id"]+".json") if source else None
                    if staged is not None and staged.is_file():
                        os.replace(staged,cache)
                if not cache.is_file():
                    raise RuntimeError("현재 추적 결과의 내장 포즈가 없습니다. 내장 검출·추적을 먼저 다시 실행해 주세요")
                progress(.1,"지정된 선수의 관절 관측 읽기")
                poses=json.loads(cache.read_text(encoding="utf-8"))
                ids={tid for item in task["assignment"]["assignments"] for tid in item["track_ids"]}
                events=punch_candidates(poses["poses"],ids,video.duration_us)
                progress(.8,f"포즈·시간축 검사 · 팔 동작 후보 {len(events)}건")
                result(task_id,Result(lease=lease,source=ENGINE_VERSION,poses=poses,events=events),self.request,key)
            log.info("내장 분석 완료 task=%s kind=%s",task_id,task["kind"])
        except ProblemError as exc:
            if exc.code!="lease_lost":
                try:
                    fail(task_id,Failure(lease=lease,message=exc.title),self.request,key)
                except ProblemError:
                    pass
            log.warning("내장 분석 중단 task=%s code=%s",task_id,exc.code)
        except Exception as exc:
            log.exception("local_analysis_failed task=%s",task_id)
            try:
                message=str(exc)[:1000] if isinstance(exc,RuntimeError) else "내장 분석 중 오류가 발생했습니다. 운영 로그를 확인해 주세요"
                fail(task_id,Failure(lease=lease,message=message),self.request,key)
            except ProblemError:
                pass
        finally:
            stop.set()
            thread.join(timeout=20)

    def run(self):
        stop=threading.Event()
        def presence():
            while not stop.is_set():
                try:
                    with self.db.tx(immediate=True) as conn:
                        conn.execute("INSERT INTO local_engine_presence VALUES(?,?,?) ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at",(self.worker_id,time.time(),ENGINE_VERSION))
                        conn.execute("DELETE FROM local_engine_presence WHERE seen_at<?",(time.time()-3600,))
                except Exception:
                    log.warning("local_engine_presence_failed")
                stop.wait(10)
        thread=threading.Thread(target=presence,daemon=True)
        thread.start()
        try:
            while True:
                if not self.run_once():
                    time.sleep(2)
        finally:
            stop.set()
            thread.join(timeout=15)
            with self.db.tx(immediate=True) as conn:
                conn.execute("DELETE FROM local_engine_presence WHERE worker_id=?",(self.worker_id,))


def main():
    parser=argparse.ArgumentParser(description="내장 CPU 영상 분석")
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not installed():
        raise SystemExit("내장 모델 설치가 필요합니다. setup_analysis.py를 실행하세요")
    worker=LocalWorker(Settings())
    if args.once:
        worker.run_once()
    else:
        try:
            worker.run()
        except KeyboardInterrupt:
            pass


if __name__=="__main__":
    main()
