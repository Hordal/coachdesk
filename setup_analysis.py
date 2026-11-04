"""내장 CPU 분석을 한 번 준비한다. 모델은 Google 공식 공개 파일에서만 받는다."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"backend"))
from app.services.local_model import MODEL_URL, MODEL_SHA256, MODEL_FILE
from app.services.sample_video import SAMPLE_URL, SAMPLE_SHA256, sample_path, sample_ready


def main():
    from app.services.local_model import installed
    if "--if-needed" in sys.argv and installed() and sample_ready():
        print("내장 모델과 테스트 영상이 준비되어 있습니다.")
        return
    python=ROOT/".venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
    if not python.is_file():
        subprocess.run([sys.executable,str(ROOT/"setup_local.py")],check=True)
    subprocess.run([str(python),"-m","pip","install","-r",str(ROOT/"backend/requirements.analysis.txt")],check=True)
    target=ROOT/"runtime/models"/MODEL_FILE
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=MODEL_SHA256:
        tmp=target.with_suffix(".download")
        with urllib.request.urlopen(MODEL_URL,timeout=120) as response,tmp.open("wb") as stream:
            while block:=response.read(1024*1024):
                stream.write(block)
        if hashlib.sha256(tmp.read_bytes()).hexdigest()!=MODEL_SHA256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("모델 해시가 공식 고정 버전과 다릅니다")
        os.replace(tmp,target)
    if not sample_ready():
        sample=sample_path()
        sample.parent.mkdir(parents=True,exist_ok=True)
        partial=sample.with_suffix(".download")
        with urllib.request.urlopen(SAMPLE_URL,timeout=120) as response,partial.open("wb") as stream:
            while block:=response.read(1024*1024):
                stream.write(block)
        if hashlib.sha256(partial.read_bytes()).hexdigest()!=SAMPLE_SHA256:
            partial.unlink(missing_ok=True)
            raise RuntimeError("테스트 영상의 해시가 다릅니다. 출처와 버전을 확인해 주세요")
        os.replace(partial,sample)
    print("내장 모델과 테스트 영상 준비 완료. 실행 중인 COACHDESK를 다시 시작하세요.")


if __name__=="__main__":
    main()
