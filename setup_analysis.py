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


def main():
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
    print("내장 분석 준비 완료. 실행 중인 COACHDESK를 다시 시작하세요.")


if __name__=="__main__":
    main()
