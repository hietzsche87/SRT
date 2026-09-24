"""실행: ``python -m korail_bot``"""

from __future__ import annotations

import logging
import os

from telegram import Update

from .bot import build_application
from .config import Settings


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
    # httpx 는 INFO 로 요청 URL(텔레그램 토큰 포함)을 찍으므로 낮춥니다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # 대화 시간 제한 작업이 추가/삭제될 때마다 찍히는 로그는 소음이라 숨깁니다.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    settings = Settings.from_env()
    if not settings.allowed_user_ids:
        logging.warning("ALLOWED_USER_IDS 가 비어 있어 아무도 봇을 쓸 수 없습니다. "
                        "봇에게 /start 를 보내 ID 를 확인한 뒤 설정하세요.")
    app = build_application(settings)

    if settings.webhook_base_url:
        logging.info("웹훅 모드: %s/%s → %s:%s", settings.webhook_base_url,
                     settings.webhook_path, settings.listen_host, settings.listen_port)
        app.run_webhook(
            listen=settings.listen_host,
            port=settings.listen_port,
            url_path=settings.webhook_path,
            webhook_url=f"{settings.webhook_base_url}/{settings.webhook_path}",
            secret_token=settings.webhook_secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        logging.info("롱폴링 모드")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
