"""환경변수에서 봇 설정을 읽습니다."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _require(name: str) -> str:
    value = _env(name)
    if value is None:
        raise RuntimeError(f"환경변수 {name} 가 필요합니다 (.env 확인)")
    return value


def _parse_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part)


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    korail_member_no: str
    korail_password: str

    # 웹훅: WEBHOOK_BASE_URL 이 비어 있으면 롱폴링으로 동작합니다.
    webhook_base_url: str | None
    webhook_path: str
    webhook_secret: str | None
    listen_host: str
    listen_port: int

    # 매크로(자동 예약) 조회 간격
    poll_interval: float
    poll_jitter: float
    max_consecutive_errors: int
    max_macro_hours: float
    include_srt: bool

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            telegram_token=_require("TELEGRAM_BOT_TOKEN"),
            allowed_user_ids=_parse_ids(_env("ALLOWED_USER_IDS")),
            korail_member_no=_require("KORAIL_MEMBER_NO"),
            korail_password=_require("KORAIL_PASSWORD"),
            webhook_base_url=(_env("WEBHOOK_BASE_URL") or "").rstrip("/") or None,
            webhook_path=(_env("WEBHOOK_PATH", "telegram") or "telegram").strip("/"),
            webhook_secret=_env("WEBHOOK_SECRET"),
            listen_host=_env("LISTEN_HOST", "0.0.0.0") or "0.0.0.0",
            listen_port=int(_env("LISTEN_PORT", "8000") or "8000"),
            poll_interval=float(_env("MACRO_INTERVAL_SECONDS", "3") or "3"),
            poll_jitter=float(_env("MACRO_JITTER_SECONDS", "2") or "2"),
            max_consecutive_errors=int(_env("MACRO_MAX_ERRORS", "20") or "20"),
            max_macro_hours=float(_env("MACRO_MAX_HOURS", "24") or "24"),
            include_srt=(_env("INCLUDE_SRT", "true") or "true").lower()
            in ("1", "true", "yes", "y"),
        )
