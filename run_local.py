"""로컬 API·웹 화면·영상 준비 워커를 함께 실행한다. 종료하면 자식 프로세스도 정리한다."""
import os
from pathlib import Path
import subprocess
import sys
import time
import secrets

ROOT = Path(__file__).resolve().parent


def main():
    if not (ROOT / "frontend/dist/index.html").is_file():
        sys.exit("frontend에서 npm ci 후 npm run build를 실행하세요.")
    os.environ.setdefault("AUTH_MODE", "dev")
    os.environ.setdefault("APP_PROFILE", "workstation")
    os.environ.setdefault("ENABLE_FIXTURES", "0")
    os.environ.setdefault("DATA_DIR", str(ROOT / "runtime" / "workspace"))
    state = Path(os.environ["DATA_DIR"])
    state.mkdir(parents=True,exist_ok=True)
    secret_file = state / ".media-secret"
    if not secret_file.exists():
        with secret_file.open("x",encoding="utf-8") as stream:
            stream.write(secrets.token_hex(32))
    os.environ.setdefault("MEDIA_SECRET",secret_file.read_text(encoding="utf-8").strip())
    os.environ.setdefault("RETENTION_DAYS", "0")  # 개인 로컬 작업은 자동 삭제하지 않는다.
    for binary in ("ffmpeg", "ffprobe"):
        path = ROOT / "runtime" / "ffmpeg" / f"{binary}.exe"
        if path.is_file():
            os.environ.setdefault(f"{binary.upper()}_PATH", str(path))
    port = os.environ.get("MMA_PORT", "8765")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    children = []
    try:
        commands=[["-m", "uvicorn", "app.api.asgi:app", "--host", "127.0.0.1", "--port", port], ["-m", "app.jobs.worker"]]
        sys.path.insert(0,str(ROOT/"backend"))
        from app.services.local_model import installed
        if os.environ.get("ENABLE_LOCAL_ANALYSIS","1")=="1" and installed():
            commands.append(["-m","app.jobs.local_analysis"])
        for args in commands:
            children.append(subprocess.Popen([sys.executable, *args], cwd=ROOT / "backend", creationflags=flags))
        print(f"MMA Coach: http://127.0.0.1:{port} | Ctrl+C로 종료", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError("서버 또는 워커가 종료되었습니다. 위 오류를 확인하세요.")
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
