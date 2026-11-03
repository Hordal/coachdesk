"""사용자 모델 어댑터 실행기. 표준 라이브러리만 사용하며 모델/학습 코드를 포함하지 않는다.

COACH_WORKER_KEY 환경 변수 설정 후 실행:
python scripts/analysis_worker.py --server http://127.0.0.1:8765 --adapter my_model:analyze

analyze(task:dict, video_path:Path, progress:Callable[[float,str],None]) -> dict
반환값: tracking={source,observations}, actions={source,events,poses?(선택),definition_version?(선택)}
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class WorkerClient:
    def __init__(self, server: str, token: str):
        server=server.rstrip("/")
        parsed=urlparse(server)
        if parsed.scheme not in {"https","http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"","/"}:
            raise ValueError("서버 주소 형식이 올바르지 않습니다")
        if parsed.scheme=="http" and parsed.hostname not in {"127.0.0.1","localhost","::1"}:
            raise ValueError("다른 PC의 서버에는 HTTPS 주소를 사용하세요")
        if not token:
            raise ValueError("COACH_WORKER_KEY 환경 변수가 필요합니다")
        self.server,self.token=server,token

    def call(self,path: str, body: dict):
        request=Request(self.server+"/api/v1"+path, data=json.dumps(body).encode(), headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"})
        try:
            with urlopen(request,timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            # 자격 증명과 원본 응답은 로그에 출력하지 않는다.
            raise RuntimeError(f"서버 요청 거절 ({exc.code})") from None

    def download(self,task:dict,target:Path):
        request=Request(self.server+task["input_path"], headers={"Authorization":"Bearer "+self.token,"X-Analysis-Lease":task["lease"]})
        checksum=hashlib.sha256()
        with urlopen(request,timeout=60) as response,target.open("wb") as stream:
            while data:=response.read(1024*1024):
                checksum.update(data)
                stream.write(data)
        if checksum.hexdigest()!=task["video"]["sha256"]:
            raise RuntimeError("원본 파일의 해시가 일치하지 않습니다")

    def process(self,task:dict,adapter):
        stop=threading.Event()
        lost=threading.Event()
        lock=threading.Lock()
        state={"progress":0.0,"message":"원본 영상 받는 중"}

        def progress(value:float,message:str):
            if lost.is_set():
                raise RuntimeError("작업이 취소되었거나 연결이 끊겼습니다")
            with lock:
                state.update(progress=min(1.0,max(0.0,value)),message=str(message)[:300])

        def heartbeat():
            while not stop.wait(15):
                try:
                    with lock:
                        body={"lease":task["lease"],**state}
                    self.call(f"/worker/tasks/{task['task_id']}/heartbeat",body)
                except (RuntimeError,URLError,TimeoutError):
                    lost.set()
                    return

        thread=threading.Thread(target=heartbeat,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="coach-analysis-") as directory:
                suffix=Path(task["video"]["filename"]).suffix.lower()
                video=Path(directory)/("source"+suffix)
                self.download(task,video)
                progress(0,"모델 실행 중")
                output=adapter(task,video,progress)
                if not isinstance(output,dict) or "lease" in output:
                    raise ValueError("어댑터는 lease를 제외한 결과 dict를 반환해야 합니다")
                progress(1,"결과 전송 중")
                body={**output,"lease":task["lease"]}
                # 응답 유실 시 동일 결과를 다시 보내도 실행 결과는 한 번만 저장된다.
                for attempt in range(3):
                    try:
                        self.call(f"/worker/tasks/{task['task_id']}/result",body)
                        break
                    except (URLError,TimeoutError):
                        if attempt==2:
                            raise
                        time.sleep(2)
        except Exception as exc:
            if not lost.is_set():
                try:
                    self.call(f"/worker/tasks/{task['task_id']}/fail",{"lease":task["lease"],"message":f"어댑터 실행/결과 수신 실패 ({type(exc).__name__}). 담당 PC의 모델과 데이터 계약을 확인하세요.","retryable":False})
                except Exception:
                    pass
            print(f"작업 실패: {task['task_id']} ({type(exc).__name__})",flush=True)
        finally:
            stop.set()
            thread.join(timeout=65)


def main():
    parser=argparse.ArgumentParser(description="COACHDESK 외부 모델 연결 워커")
    parser.add_argument("--server",default="http://127.0.0.1:8765")
    parser.add_argument("--adapter",required=True,help="직접 준비한 Python 모듈:함수")
    parser.add_argument("--kinds",nargs="+",choices=["tracking","actions"],default=["tracking","actions"])
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    client=WorkerClient(args.server,os.environ.get("COACH_WORKER_KEY",""))
    sys.path.insert(0,str(Path.cwd()))
    module,separator,name=args.adapter.partition(":")
    if not separator:
        parser.error("--adapter는 모듈:함수 형식이어야 합니다")
    adapter=getattr(importlib.import_module(module),name)
    if not callable(adapter):
        parser.error("어댑터가 호출 가능한 함수가 아닙니다")
    print("분석 워커 연결됨. 대기 작업을 확인합니다.",flush=True)
    while True:
        try:
            response=client.call("/worker/claim",{"kinds":args.kinds})
            if response["task"]:
                task=response["task"]
                print(f"작업 시작: {task['task_id']} / {task['kind']}",flush=True)
                client.process(task,adapter)
            elif not args.once:
                time.sleep(3)
        except (RuntimeError,URLError,TimeoutError) as exc:
            print(f"서버 연결 확인 필요 ({type(exc).__name__})",flush=True)
            if not args.once:
                time.sleep(10)
        if args.once:
            return


if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("워커 종료")
