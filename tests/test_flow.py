"""가짜 텔레그램 전송로로 대화 흐름 전체를 돌려 봅니다."""

import asyncio
import json

import pytest
from korail_mobile_api import ReservationHoldResponse, TrainSummary
from telegram import Update
from telegram.request import BaseRequest

from korail_bot import bot
from korail_bot.config import Settings

USER = {"id": 42, "is_bot": False, "first_name": "u"}
CHAT = {"id": 42, "type": "private"}


class FakeRequest(BaseRequest):
    def __init__(self):
        self.calls = []

    @property
    def read_timeout(self):
        return 1

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((name, params))
        if name == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "b", "username": "b"}
        elif name in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
            result = {"message_id": 99, "date": 0, "chat": CHAT, "text": params.get("text", "")}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


class FakeKorail:
    include_srt = True

    def __init__(self):
        self.reserved = []

    def make_query(self, *a):
        from korail_bot.korail import KorailService
        return KorailService.make_query(self, *a)

    async def login(self):
        pass

    async def search(self, query):
        return [TrainSummary(train_no="00101", train_group_name="KTX", departure_date=query.departure_date,
                             departure_time="230000", arrival_time="235900",
                             general_reservation_code="11", special_reservation_code="13")]

    async def search_transfer(self, query, cursor=None):
        a = TrainSummary(train_no="009", train_group_name="KTX", departure_date=query.departure_date,
                         departure_time="200000", arrival_time="210000", arrival_station_name="오송",
                         general_reservation_code="11")
        b = TrainSummary(train_no="503", train_group_name="KTX", departure_date=query.departure_date,
                         departure_time="213000", arrival_time="233000", general_reservation_code="11")
        return [(a, b)], None

    async def reserve(self, option, seat_classes, adults):
        self.reserved.append((tuple(t.train_no for t in option), seat_classes, adults))
        return ReservationHoldResponse(total_price="59800")

    def close(self):
        pass


def msg(text, uid=42):
    return {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": CHAT,
            "from": {**USER, "id": uid}, "text": text,
            **({"entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]}
               if text.startswith("/") else {})}}


def cb(data):
    return {"update_id": 2, "callback_query": {"id": "c", "from": USER, "chat_instance": "x", "data": data,
            "message": {"message_id": 99, "date": 0, "chat": CHAT, "text": "."}}}


def _app(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "123:abc", "KORAIL_MEMBER_NO": "1",
                 "KORAIL_PASSWORD": "pw", "ALLOWED_USER_IDS": "42"}.items():
        monkeypatch.setenv(k, v)
    request = FakeRequest()
    real_builder = bot.ApplicationBuilder

    class Builder(real_builder):
        def build(self):
            self.request(request).get_updates_request(FakeRequest())
            return super().build()

    monkeypatch.setattr(bot, "ApplicationBuilder", Builder)
    app = bot.build_application(Settings.from_env())
    fake = FakeKorail()
    app.bot_data["korail"] = fake
    app.bot_data["macros"].korail = fake
    app.bot_data["macros"].interval = 0
    app.bot_data["macros"].jitter = 0
    return app, request, fake


@pytest.mark.asyncio
async def test_full_flow(monkeypatch):
    app, request, fake = _app(monkeypatch)
    async with app:
        async def send(payload):
            await app.process_update(Update.de_json(payload, app.bot))

        await send(msg("/start", uid=7))  # 허용되지 않은 사용자
        assert "7" in request.calls[-1][1]["text"]
        await send(msg("/search"))
        await send(cb("dep:서울"))
        await send(msg("부산"))
        await send(cb("dt:20301001"))
        await send(msg("0700"))
        assert "예약할 열차" in request.calls[-1][1]["text"]
        await send(cb("tg:0"))
        await send(cb("pax"))
        await send(cb("go"))
        for _ in range(100):
            if fake.reserved and not app.bot_data["macros"].jobs:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
    assert fake.reserved[0][0] == ("00101",) and fake.reserved[0][2] == 2
    texts = [c[1].get("text", "") for c in request.calls]
    assert any("예약 성공" in t for t in texts)


@pytest.mark.asyncio
async def test_transfer_flow(monkeypatch):
    app, request, fake = _app(monkeypatch)
    async with app:
        async def send(payload):
            await app.process_update(Update.de_json(payload, app.bot))

        await send(msg("/search"))
        await send(cb("dep:강릉"))
        await send(cb("arr:목포"))
        await send(cb("dt:20301001"))
        await send(cb("tm:07"))
        await send(cb("mode"))
        assert "환승" in request.calls[-1][1]["text"]
        await send(cb("tg:0"))
        await send(cb("go"))
        for _ in range(100):
            if fake.reserved and not app.bot_data["macros"].jobs:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
    assert fake.reserved[0][0] == ("009", "503")
    texts = [c[1].get("text", "") for c in request.calls]
    assert any("예약 성공" in t and "2구간" in t for t in texts)
