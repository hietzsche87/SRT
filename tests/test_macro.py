import asyncio
from datetime import datetime

import pytest
from korail_mobile_api import (
    KorailAppError,
    KorailProtocolError,
    KorailSeatClass,
    KorailSoldOutError,
    KorailTransportError,
    ReservationHoldResponse,
    TrainSearchQuery,
    TrainSummary,
)

from korail_bot.macro import KST, MacroManager, SeatPref, pick_seat_class, pick_seat_classes

NOW = datetime(2026, 10, 1, 6, 0, tzinfo=KST)


def train(no: str, dep: str, gen: str = "13", spe: str = "13") -> TrainSummary:
    return TrainSummary(
        train_no=no,
        departure_date="20261001",
        departure_time=dep,
        arrival_time="235900",
        general_reservation_code=gen,
        special_reservation_code=spe,
    )


QUERY = TrainSearchQuery("서울", "부산", "20261001", "070000")


class FakeKorail:
    def __init__(self, pages: list[list[list[TrainSummary]]], reserve_errors=()):
        # pages[attempt] = list of result pages for that attempt
        self.pages = pages
        self.attempt = -1
        self.page = 0
        self.reserve_errors = list(reserve_errors)
        self.reserved: list[tuple[str, KorailSeatClass]] = []
        self.queries: list[TrainSearchQuery] = []
        self.transfer_pages: list = []

    async def login(self):
        pass

    async def search(self, query):
        self.queries.append(query)
        if query.departure_time == QUERY_START:
            self.attempt += 1
            self.page = 0
        attempt = min(self.attempt, len(self.pages) - 1)
        result_pages = self.pages[attempt]
        result = result_pages[self.page] if self.page < len(result_pages) else []
        self.page += 1
        return result

    async def search_transfer(self, query, cursor=None):
        self.queries.append(query)
        pages = self.transfer_pages
        index = 0 if cursor is None else cursor
        options = pages[index] if index < len(pages) else []
        return options, (index + 1 if index + 1 < len(pages) else None)

    async def reserve(self, option, seat_classes, adults):
        if self.reserve_errors:
            raise self.reserve_errors.pop(0)
        if len(option) == 1:
            self.reserved.append((option[0].train_no, seat_classes[0]))
        else:
            self.reserved.append((tuple(t.train_no for t in option), seat_classes))
        return ReservationHoldResponse(pnr_no="PNR", total_price="59800")


QUERY_START = "070000"


def manager(fake, results):
    async def on_success(job, hold, option, seat_classes):
        if len(option) == 1:
            results.append(("ok", option[0].train_no, seat_classes[0]))
        else:
            results.append(("ok", tuple(t.train_no for t in option), seat_classes))

    async def on_stop(job, reason):
        results.append(("stop", reason))

    return MacroManager(fake, on_success=on_success, on_stop=on_stop,
                        interval=0, jitter=0, max_consecutive_errors=3, now=lambda: NOW)


async def wait_done(m, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if not m.jobs:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


def test_pick_seat_class():
    assert pick_seat_class(train("1", "0700", gen="11"), SeatPref.GENERAL) is KorailSeatClass.GENERAL
    assert pick_seat_class(train("1", "0700", spe="11"), SeatPref.GENERAL) is None
    assert pick_seat_class(train("1", "0700", spe="11"), SeatPref.ANY) is KorailSeatClass.SPECIAL
    assert pick_seat_class(train("1", "0700", gen="11", spe="11"), SeatPref.SPECIAL) is KorailSeatClass.SPECIAL


@pytest.mark.asyncio
async def test_reserves_when_seat_opens():
    sold = [train("101", "070000"), train("103", "080000")]
    open_ = [train("101", "070000"), train("103", "080000", gen="11")]
    fake = FakeKorail([[sold], [sold], [open_]])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("103", "080000"),), (train("101", "070000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results == [("ok", "103", KorailSeatClass.GENERAL)]
    assert fake.reserved == [("103", KorailSeatClass.GENERAL)]


@pytest.mark.asyncio
async def test_follows_pages_to_find_later_train():
    page1 = [train("101", "070000"), train("102", "073000")]
    page2 = [train("102", "073000"), train("199", "210000", gen="11")]
    fake = FakeKorail([[page1, page2]])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("101", "070000"),), (train("199", "210000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results == [("ok", "199", KorailSeatClass.GENERAL)]
    assert fake.queries[1].departure_time == "073000"


@pytest.mark.asyncio
async def test_sold_out_race_keeps_trying():
    open_ = [train("101", "070000", gen="11")]
    fake = FakeKorail([[open_]], reserve_errors=[KorailSoldOutError("ERR211161", "매진")])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("101", "070000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results == [("ok", "101", KorailSeatClass.GENERAL)]


@pytest.mark.asyncio
async def test_transport_error_on_reserve_stops_to_avoid_double_booking():
    open_ = [train("101", "070000", gen="11")]
    fake = FakeKorail([[open_]], reserve_errors=[KorailTransportError("timeout")])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("101", "070000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results[0][0] == "stop" and "/reservations" in results[0][1]
    assert fake.reserved == []


@pytest.mark.asyncio
async def test_parse_failure_after_server_success_stops():
    error = KorailProtocolError("bad shape")
    error.raw = {"strResult": "SUCC"}
    fake = FakeKorail([[[train("101", "070000", gen="11")]]], reserve_errors=[error])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("101", "070000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results[0][0] == "stop"


@pytest.mark.asyncio
async def test_refusal_stops():
    fake = FakeKorail([[[train("101", "070000", gen="11")]]],
                      reserve_errors=[KorailAppError("WRR999", "중복 예약")])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("101", "070000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results == [("stop", "코레일이 예약을 거절해 중단했습니다: 중복 예약")]


@pytest.mark.asyncio
async def test_departed_trains_stop_job():
    fake = FakeKorail([[[]]])
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [(train("101", "050000"),)], SeatPref.GENERAL, 1)
    await wait_done(m)
    assert results[0][0] == "stop" and "출발" in results[0][1]


@pytest.mark.asyncio
async def test_user_stop_ends_loop_without_callback():
    fake = FakeKorail([[[train("101", "070000")]]])
    results = []
    m = manager(fake, results)
    m.interval = 0.01
    job = m.start(1, QUERY, [(train("101", "070000"),)], SeatPref.GENERAL, 1)
    await asyncio.sleep(0.05)
    assert m.stop(job.id) is job
    await asyncio.wait_for(job.task, 1)
    assert results == []


def test_pick_seat_classes_needs_every_leg():
    a, b = train("1", "0700", gen="11"), train("2", "0900", gen="13", spe="11")
    assert pick_seat_classes((a, b), SeatPref.GENERAL) is None
    assert pick_seat_classes((a, b), SeatPref.ANY) == (KorailSeatClass.GENERAL, KorailSeatClass.SPECIAL)


@pytest.mark.asyncio
async def test_transfer_reserves_when_both_legs_open():
    def itin(gen2):
        return (train("009", "070000", gen="11"), train("503", "090000", gen=gen2))

    fake = FakeKorail([[[]]])
    # 첫 페이지엔 다른 여정, 둘째 페이지에 목표 여정 — 처음엔 2구간 매진
    other = (train("011", "071000"), train("505", "091000"))
    fake.transfer_pages = [[other], [itin("13")]]
    results = []
    m = manager(fake, results)
    m.start(1, QUERY, [itin("13")], SeatPref.GENERAL, 1, transfer=True)
    await asyncio.sleep(0.05)
    assert fake.reserved == []
    fake.transfer_pages = [[other], [itin("11")]]
    await wait_done(m)
    assert results == [("ok", ("009", "503"), (KorailSeatClass.GENERAL, KorailSeatClass.GENERAL))]
