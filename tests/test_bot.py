from datetime import date

import pytest
from korail_mobile_api import ReservationHoldResponse, TrainSummary

from korail_bot import bot
from korail_bot.config import Settings
from korail_bot.formatting import hold_message, train_line


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(bot, "_today", lambda: date(2026, 9, 23))


@pytest.mark.parametrize("text,expected", [
    ("1025", "20261025"),
    ("10/25", "20261025"),
    ("2026-10-25", "20261025"),
    ("20261025", "20261025"),
    ("1/3", "20270103"),
    ("0923", "20260923"),
    ("20260101", None),
    ("hello", None),
    ("13/40", None),
])
def test_parse_date(text, expected):
    assert bot._parse_date(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("7", "070000"), ("07", "070000"), ("0730", "073000"), ("7:30", "073000"),
    ("730", "073000"), ("2400", None), ("abc", None),
])
def test_parse_time(text, expected):
    assert bot._parse_time(text) == expected


def test_messages_render():
    t = TrainSummary(train_no="00101", train_group_name="KTX", departure_date="20261001",
                     departure_time="070000", arrival_time="093000",
                     departure_station_name="서울", arrival_station_name="부산",
                     general_reservation_code="11", special_reservation_code="13")
    assert train_line(t) == "07:00→09:30 KTX 101 | 일반 ✅ · 특실 ❌"
    msg = hold_message(ReservationHoldResponse(total_price="59800", payment_deadline_date="20260923",
                                               payment_deadline_time="201000"), t, "일반실")
    assert "59,800원" in msg and "2026-09-23 20:10" in msg


def test_build_application(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "123:abc", "KORAIL_MEMBER_NO": "1234567890",
                 "KORAIL_PASSWORD": "pw", "ALLOWED_USER_IDS": "1, 2"}.items():
        monkeypatch.setenv(k, v)
    settings = Settings.from_env()
    assert settings.allowed_user_ids == {1, 2}
    app = bot.build_application(settings)
    assert "macros" in app.bot_data
