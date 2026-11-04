# 임시 공유 접속

share_local.py는 기존 runtime/workspace와 분리된 runtime/public/open-workspace를 사용합니다. 로그인 없는 공개 체험 모드를 127.0.0.1:8780에 실행하고 Cloudflare 임시 터널을 연결합니다. 기존 개인 영상은 공개되지 않습니다.

## 시작

1. start.cmd로 의존성·모델·화면을 먼저 준비합니다.
2. Cloudflare 공식 cloudflared Windows 실행 파일을 runtime/tools/cloudflared.exe에 둡니다.
3. .venv/Scripts/python.exe share_local.py를 실행합니다.
4. runtime/public/tunnel.log에서 HTTPS 주소를 확인합니다. 주소는 실행마다 바뀔 수 있습니다.
5. 공개 주소를 열면 로그인 없이 체험할 수 있습니다. 기존 계정용 workspace와 accounts.json은 보존하며 공개 모드에서 사용하지 않습니다.

모든 방문자가 공개 체험 영상과 기록을 공유합니다. 계정 관리와 외부 모델 키 관리는 차단합니다. 공개해도 되는 시험 영상만 등록하세요. 개인별 비공개 기록이 필요하면 별도의 계정 인증 서비스로 운영해야 합니다.

PC가 켜져 있고 서버·터널이 살아 있는 동안만 열립니다. 이는 임시 시험 주소이며 상시 서비스는 고정 도메인과 서버가 필요합니다. Cloudflare를 통해 접속하므로 공개 시험용 영상만 등록하세요.

## 종료

runtime/public/processes.json에 이번 공유 서버와 터널의 프로세스 ID가 기록됩니다. 해당 프로세스가 이 프로젝트의 run_local.py 또는 runtime/tools/cloudflared.exe인지 확인하고 종료하세요. 개인 서버 8765는 별개입니다. 재실행 전에 공유 서버를 종료해야 하며 실행 중인 8780 포트가 있으면 중복 시작을 거절합니다.
