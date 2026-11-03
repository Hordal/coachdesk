"""다운로드한 로컬 패키지의 첫 실행 준비. 런타임 자료를 외부로 보내지 않는다."""
import hashlib
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 이상이 필요합니다.")
    directory = ROOT / ".venv"
    python = directory / "Scripts/python.exe" if sys.platform == "win32" else directory / "bin/python"
    if not python.is_file():
        venv.create(directory, with_pip=True)
    requirements = ROOT / "backend/requirements.lock.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = directory / ".requirements.sha256"
    if not stamp.is_file() or stamp.read_text() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        stamp.write_text(digest)
    if "--rebuild" in sys.argv or not (ROOT / "frontend/dist/index.html").is_file():
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        for args in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
            subprocess.run([npm, *args], cwd=ROOT / "frontend", check=True)


if __name__ == "__main__":
    main()
