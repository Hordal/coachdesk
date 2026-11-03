"""uvicorn 진입점: `uvicorn app.api.asgi:app`. 가져오는 순간 앱을 만들므로 시험에서는 이 모듈을 가져오지 않는다."""

from .main import create_app

app = create_app()
