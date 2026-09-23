"""korail-mobile-api 를 봇에서 쓰기 좋게 감싼 계층.

라이브러리는 동기(httpx) 클라이언트라서, 모든 호출을 하나의 락으로 직렬화하고
``asyncio.to_thread`` 로 이벤트 루프 밖에서 돌립니다. 세션이 만료되면 한 번
다시 로그인하고 재시도합니다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from typing import TypeVar

from korail_mobile_api import (
    KorailApiError,
    KorailAuthContinuationRequired,
    KorailAuthError,
    KorailClient,
    KorailConfig,
    KorailNoResultsError,
    KorailPassengerCounts,
    KorailSeatClass,
    KorailSessionExpiredError,
    ReservationHistoryResponse,
    ReservationHoldResponse,
    TrainSearchContinuation,
    TrainSearchQuery,
    TrainSummary,
    build_config_from_env,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

#: 한 번에 예약되는 여정. 직통은 구간 1개, 환승은 2개(탑승 순서대로).
Option = tuple[TrainSummary, ...]

#: 검색 결과에서 "예약 가능"을 뜻하는 좌석 코드 (라이브러리 예약 검증과 같은 규칙).
AVAILABLE_CODE = "11"


def _build_config() -> KorailConfig:
    # 실기기 값을 넣어 두면 재시작해도 같은 기기로 보입니다. 없으면 합성 값을 씁니다.
    if os.environ.get("KORAIL_DYNAPATH_DEVICE_ID"):
        return build_config_from_env()
    return KorailConfig(enable_dynapath=True)


def has_general_seat(train: TrainSummary) -> bool:
    return train.general_reservation_code == AVAILABLE_CODE


def has_special_seat(train: TrainSummary) -> bool:
    return train.special_reservation_code == AVAILABLE_CODE


class KorailService:
    def __init__(self, member_no: str, password: str, *, include_srt: bool = True) -> None:
        self._member_no = member_no
        self._password = password
        self.include_srt = include_srt
        self._lock = threading.Lock()
        self._client = KorailClient(_build_config())

    # ------------------------------------------------------------------ sync

    def _login_locked(self) -> None:
        log.info("KORAIL 로그인 시도")
        try:
            self._client.login(self._member_no, self._password)
        except KorailAuthContinuationRequired as error:
            raise KorailAuthError(
                "추가 인증(2단계)이 필요한 계정입니다. 코레일톡 앱에서 인증을 마친 뒤 다시 시도하세요."
            ) from error
        log.info("KORAIL 로그인 성공")

    def _authed(self, operation: Callable[[], T]) -> T:
        with self._lock:
            if self._client.session.current is None:
                self._login_locked()
            try:
                return operation()
            except KorailSessionExpiredError:
                log.info("세션 만료 — 재로그인 후 재시도")
                self._client.clear_session()
                self._login_locked()
                return operation()

    def login_sync(self) -> None:
        with self._lock:
            self._client.clear_session()
            self._login_locked()

    def search_sync(self, query: TrainSearchQuery) -> list[TrainSummary]:
        with self._lock:
            try:
                return list(self._client.search_trains(query).trains)
            except KorailNoResultsError:
                return []

    def search_transfer_sync(
        self, query: TrainSearchQuery, continuation: TrainSearchContinuation | None = None
    ) -> tuple[list[Option], TrainSearchContinuation | None]:
        """환승 여정 한 페이지와 다음 페이지 커서."""
        with self._lock:
            try:
                result = self._client.search_transfer_trains(query, continuation=continuation)
            except KorailNoResultsError:
                return [], None
            options = [tuple(itinerary.legs) for itinerary in result.itineraries]
            try:
                cursor = result.next_page() if options else None
            except (KorailApiError, ValueError):
                cursor = None
            return options, cursor

    def reserve_sync(
        self, option: Option, seat_classes: tuple[KorailSeatClass, ...], adults: int
    ) -> ReservationHoldResponse:
        passengers = KorailPassengerCounts(adult=adults)
        if len(option) == 1:
            return self._authed(
                lambda: self._client.reserve(
                    option[0], passengers=passengers, seat_class=seat_classes[0]
                )
            )
        return self._authed(
            lambda: self._client.reserve_transfer(
                list(option), passengers=passengers, seat_classes=list(seat_classes)
            )
        )

    def cancel_sync(self, hold: ReservationHoldResponse) -> None:
        self._authed(lambda: self._client.cancel_unpaid_hold(hold))

    def reservations_sync(self) -> ReservationHistoryResponse:
        return self._authed(self._client.get_reservation_history)

    def close(self) -> None:
        with self._lock:
            try:
                self._client.logout()
            except KorailApiError:
                pass
            self._client.close()

    # ----------------------------------------------------------------- async

    async def login(self) -> None:
        await asyncio.to_thread(self.login_sync)

    async def search(self, query: TrainSearchQuery) -> list[TrainSummary]:
        return await asyncio.to_thread(self.search_sync, query)

    async def search_transfer(
        self, query: TrainSearchQuery, continuation: TrainSearchContinuation | None = None
    ) -> tuple[list[Option], TrainSearchContinuation | None]:
        return await asyncio.to_thread(self.search_transfer_sync, query, continuation)

    async def reserve(
        self, option: Option, seat_classes: tuple[KorailSeatClass, ...], adults: int
    ) -> ReservationHoldResponse:
        return await asyncio.to_thread(self.reserve_sync, option, seat_classes, adults)

    async def cancel(self, hold: ReservationHoldResponse) -> None:
        await asyncio.to_thread(self.cancel_sync, hold)

    async def reservations(self) -> ReservationHistoryResponse:
        return await asyncio.to_thread(self.reservations_sync)

    def make_query(self, dep: str, arr: str, date: str, time: str, adults: int) -> TrainSearchQuery:
        return TrainSearchQuery(
            departure_station_code=dep,
            arrival_station_code=arr,
            departure_date=date,
            departure_time=time,
            passengers=adults,
            include_srt=self.include_srt,
        )
