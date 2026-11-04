"""Start an isolated public demo without login. Keeps local recordings private.

Run with .venv/Scripts/python.exe share_local.py. cloudflared.exe must exist in
runtime/tools. Process IDs and signing secrets stay under ignored runtime/public.
"""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "runtime" / "public"


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    data = STATE / "open-workspace"
    secret_file = STATE / "media-secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
    env = {**os.environ, "AUTH_MODE": "dev", "APP_PROFILE": "workstation", "PUBLIC_DEMO": "1",
           "COOKIE_SECURE": "1", "ENABLE_FIXTURES": "0", "MMA_PORT": "8780",
           "DATA_DIR": str(data), "MEDIA_SECRET": secret_file.read_text(encoding="utf-8").strip(),
           "PYTHONIOENCODING": "utf-8"}
    tunnel = ROOT / "runtime" / "tools" / "cloudflared.exe"
    if not tunnel.is_file():
        raise SystemExit("runtime/tools/cloudflared.exe is required")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Refuse to duplicate processes from this launcher's previous run.
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8780)) == 0:
            raise SystemExit("Port 8780 is already in use; stop the existing preview first")
    with (STATE / "server.log").open("ab") as log:
        server = subprocess.Popen([sys.executable, "run_local.py"], cwd=ROOT, env=env,
                                  stdout=log, stderr=log, creationflags=flags)
    with (STATE / "tunnel.log").open("wb") as log:
        try:
            proxy = subprocess.Popen([str(tunnel), "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8780"],
                                     cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
        except Exception:
            server.terminate()
            raise
    (STATE / "processes.json").write_text(json.dumps({"server": server.pid, "tunnel": proxy.pid}), encoding="utf-8")
    print("Open demo preview started. See runtime/public/tunnel.log for its HTTPS URL.")


if __name__ == "__main__":
    main()
