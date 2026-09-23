"""자동 예약(매크로) 작업.

선택한 열차들을 주기적으로 다시 조회해서, 원하는 등급에 빈 좌석이 보이면 곧바로
예약(결제 전 홀드)을 겁니다. 텔레그램과는 콜백으로만 연결됩니다.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo

from korail_mobile_api import (
    KorailApiError,
    KorailAppError,
    KorailAuthError,
    KorailProtocolError,
    KorailSeatClass,
    KorailSeatUnavailableError,
    KorailSoldOutError,
    KorailTransportError,
    ReservationHoldResponse,
    TrainSearchContinuation,
    TrainSearchQuery,
    TrainSummary,
)

from .korail import Option, has_general_seat, has_special_seat

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

#: 한 번의 조회에서 따라가 볼 최대 페이지 수 (한 페이지 ≈ 10 편).
MAX_PAGES = 5


class SeatPref(str, Enum):
    GENERAL = "general"
    SPECIAL = "special"
    ANY = "any"

    @property
    def label(self) -> str:
        return {"general": "일반실", "special": "특실", "any": "일반실 우선·특실"}[self.value]

    def next(self) -> SeatPref:
        order = list(SeatPref)
        return order[(order.index(self) + 1) % len(order)]


def pick_seat_class(train: TrainSummary, pref: SeatPref) -> KorailSeatClass | None:
    if pref in (SeatPref.GENERAL, SeatPref.ANY) and has_general_seat(train):
        return KorailSeatClass.GENERAL
    if pref in (SeatPref.SPECIAL, SeatPref.ANY) and has_special_seat(train):
        return KorailSeatClass.SPECIAL
    return None


def pick_seat_classes(option: Option, pref: SeatPref) -> tuple[KorailSeatClass, ...] | None:
    """여정의 모든 구간에 좌석이 있을 때만 구간별 등급을 돌려줍니다."""
    classes = tuple(pick_seat_class(leg, pref) for leg in option)
    if any(c is None for c in classes):
        return None
    return classes  # type: ignore[return-value]


def seat_class_label(seat_class: KorailSeatClass) -> str:
    return "특실" if seat_class is KorailSeatClass.SPECIAL else "일반실"


def seat_classes_label(seat_classes: tuple[KorailSeatClass, ...]) -> str:
    labels = [seat_class_label(c) for c in seat_classes]
    return labels[0] if len(set(labels)) == 1 else "/".join(labels)


def train_key(train: TrainSummary) -> tuple[str, str]:
    return (train.departure_date or "", train.train_no)


def option_key(option: Option) -> tuple[tuple[str, str], ...]:
    return tuple(train_key(leg) for leg in option)


def departure_datetime(train: TrainSummary) -> datetime | None:
    if not train.departure_date or not train.departure_time:
        return None
    try:
        return datetime.strptime(
            train.departure_date + train.departure_time[:4], "%Y%m%d%H%M"
        ).replace(tzinfo=KST)
    except ValueError:
        return None


class ReserveAborted(Exception):
    """예약 요청 결과를 확신할 수 없거나 서버가 거절해 매크로를 멈춰야 하는 경우."""


class KorailLike(Protocol):
    async def login(self) -> None: ...
    async def search(self, query: TrainSearchQuery) -> list[TrainSummary]: ...
    async def search_transfer(
        self, query: TrainSearchQuery, continuation: TrainSearchContinuation | None = None
    ) -> tuple[list[Option], TrainSearchContinuation | None]: ...
    async def reserve(
        self, option: Option, seat_classes: tuple[KorailSeatClass, ...], adults: int
    ) -> ReservationHoldResponse: ...


SuccessCallback = Callable[
    ["MacroJob", ReservationHoldResponse, Option, tuple[KorailSeatClass, ...]], Awaitable[None]
]
StopCallback = Callable[["MacroJob", str], Awaitable[None]]


@dataclass
class MacroJob:
    id: int
    chat_id: int
    query: TrainSearchQuery
    targets: list[Option]
    seat_pref: SeatPref
    adults: int
    transfer: bool = False
    started_at: float = field(default_factory=time.time)
    attempts: int = 0
    errors: int = 0
    last_error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def title(self) -> str:
        q = self.query
        kind = " (환승)" if self.transfer else ""
        return (f"{q.departure_station_code}→{q.arrival_station_code}{kind} "
                f"{q.departure_date[4:6]}/{q.departure_date[6:]}")


class MacroManager:
    def __init__(
        self,
        korail: KorailLike,
        *,
        on_success: SuccessCallback,
        on_stop: StopCallback,
        interval: float = 3.0,
        jitter: float = 2.0,
        max_consecutive_errors: int = 20,
        max_hours: float = 24.0,
        now: Callable[[], datetime] = lambda: datetime.now(KST),
    ) -> None:
        self.korail = korail
        self.on_success = on_success
        self.on_stop = on_stop
        self.interval = interval
        self.jitter = jitter
        self.max_consecutive_errors = max_consecutive_errors
        self.max_seconds = max_hours * 3600
        self.now = now
        self.jobs: dict[int, MacroJob] = {}
        self._ids = itertools.count(1)

    # ------------------------------------------------------------ lifecycle

    def start(
        self,
        chat_id: int,
        query: TrainSearchQuery,
        targets: list[Option],
        seat_pref: SeatPref,
        adults: int,
        *,
        transfer: bool = False,
    ) -> MacroJob:
        targets = sorted(targets, key=lambda o: (o[0].departure_date or "", o[0].departure_time or ""))
        job = MacroJob(
            id=next(self._ids),
            chat_id=chat_id,
            query=query,
            targets=targets,
            seat_pref=seat_pref,
            adults=adults,
            transfer=transfer,
        )
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job), name=f"macro-{job.id}")
        return job

    def stop(self, job_id: int) -> MacroJob | None:
        # 태스크를 cancel 하지 않습니다. 예약 요청이 스레드에서 진행 중일 때 끊으면
        # 예약은 잡혔는데 알림이 안 갈 수 있습니다. 루프가 다음 확인 때 스스로 끝납니다.
        return self.jobs.pop(job_id, None)

    def jobs_for(self, chat_id: int) -> list[MacroJob]:
        return [job for job in self.jobs.values() if job.chat_id == chat_id]

    async def shutdown(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task is not None]
        self.jobs.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---------------------------------------------------------------- loop

    async def _finish(self, job: MacroJob, reason: str) -> None:
        self.jobs.pop(job.id, None)
        await self.on_stop(job, reason)

    async def _run(self, job: MacroJob) -> None:
        try:
            await self._loop(job)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # 예상 못 한 오류도 사용자에게 알리고 끝냅니다.
            log.exception("macro %s crashed", job.id)
            await self._finish(job, f"예상치 못한 오류로 중단했습니다: {error}")

    async def _loop(self, job: MacroJob) -> None:
        while job.id in self.jobs:
            remaining = self._remaining_targets(job)
            if not remaining:
                await self._finish(job, "선택한 열차가 모두 출발 시각을 지나 중단했습니다.")
                return
            if time.time() - job.started_at > self.max_seconds:
                await self._finish(job, "최대 실행 시간을 넘어 중단했습니다.")
                return

            job.attempts += 1
            try:
                done = await self._attempt(job, remaining)
            except ReserveAborted as error:
                await self._finish(job, str(error))
                return
            except KorailAuthError as error:
                await self._finish(job, f"로그인 실패로 중단했습니다: {error}")
                return
            except (KorailTransportError, KorailApiError) as error:
                job.errors += 1
                job.last_error = str(error)
                log.warning("macro %s error %s/%s: %s", job.id, job.errors,
                            self.max_consecutive_errors, error)
                if job.errors >= self.max_consecutive_errors:
                    await self._finish(job, f"연속 오류 {job.errors}회로 중단했습니다.\n마지막 오류: {error}")
                    return
                # 오류가 이어지면 점점 천천히 (최대 60초)
                await asyncio.sleep(min(60.0, self.interval * (2 ** min(job.errors, 5))))
                continue
            if done:
                return
            job.errors = 0
            await asyncio.sleep(self.interval + random.uniform(0, self.jitter))

    def _remaining_targets(self, job: MacroJob) -> list[Option]:
        now = self.now()
        result = []
        for option in job.targets:
            departs = departure_datetime(option[0])
            if departs is None or departs > now:
                result.append(option)
        return result

    def _first_query(self, job: MacroJob, targets: list[Option]) -> TrainSearchQuery:
        first = targets[0][0]
        return replace(
            job.query,
            departure_date=first.departure_date or job.query.departure_date,
            departure_time=(first.departure_time or job.query.departure_time)[:4] + "00",
        )

    async def _fetch(self, job: MacroJob, targets: list[Option]) -> dict[tuple, Option]:
        """선택한 여정들이 모두 나올 때까지 (최대 MAX_PAGES) 검색합니다."""
        wanted = {option_key(o) for o in targets}
        if job.transfer:
            return await self._fetch_transfer(job, targets, wanted)
        query = self._first_query(job, targets)
        found: dict[tuple, Option] = {}
        for _ in range(MAX_PAGES):
            trains = await self.korail.search(query)
            if not trains:
                break
            for train in trains:
                if option_key((train,)) in wanted:
                    found[option_key((train,))] = (train,)
            if wanted.issubset(found):
                break
            last = trains[-1]
            if not last.departure_date or not last.departure_time:
                break
            next_query = replace(query, departure_date=last.departure_date,
                                 departure_time=last.departure_time)
            if next_query == query:
                break
            query = next_query
        return found

    async def _fetch_transfer(
        self, job: MacroJob, targets: list[Option], wanted: set[tuple]
    ) -> dict[tuple, Option]:
        query = self._first_query(job, targets)
        cursor: TrainSearchContinuation | None = None
        found: dict[tuple, Option] = {}
        for _ in range(MAX_PAGES):
            options, cursor = await self.korail.search_transfer(query, cursor)
            for option in options:
                if option_key(option) in wanted:
                    found[option_key(option)] = option
            if wanted.issubset(found) or not options or cursor is None:
                break
        return found

    async def _attempt(self, job: MacroJob, targets: list[Option]) -> bool:
        found = await self._fetch(job, targets)
        for target in targets:
            option = found.get(option_key(target))
            if option is None:
                continue
            if job.id not in self.jobs:  # 조회 도중 사용자가 중지함
                return True
            seat_classes = pick_seat_classes(option, job.seat_pref)
            if seat_classes is None:
                continue
            names = "+".join(leg.train_no for leg in option)
            log.info("macro %s: %s 좌석 발견 (%s) → 예약 시도", job.id, names,
                     ",".join(c.name for c in seat_classes))
            try:
                hold = await self.korail.reserve(option, seat_classes, job.adults)
            except (KorailSoldOutError, KorailSeatUnavailableError) as error:
                # 조회와 예약 사이에 다른 사람이 가져간 경우 — 계속 시도합니다.
                log.info("macro %s: 매진(계속): %s", job.id, error)
                continue
            except KorailTransportError as error:
                # 요청이 서버에 닿았는지 알 수 없습니다. 재시도하면 중복 예약이 될 수 있습니다.
                raise ReserveAborted(
                    "예약 요청 중 통신 오류가 나서 중단했습니다. "
                    "예약이 잡혔을 수도 있으니 /reservations 로 확인하세요.\n"
                    f"오류: {error}"
                ) from error
            except KorailProtocolError as error:
                if error.raw is not None:
                    # 서버는 처리했는데 응답 해석만 실패한 경우 — 예약됐을 수 있습니다.
                    raise ReserveAborted(
                        "예약 응답을 해석하지 못해 중단했습니다. "
                        "예약이 잡혔을 수 있으니 /reservations 로 확인하세요.\n"
                        f"오류: {error}"
                    ) from error
                # 전송 전 검증 실패(좌석 코드가 예약 가능이 아님) — 계속 시도합니다.
                log.info("macro %s: 예약 전 검증 실패(계속): %s", job.id, error)
                continue
            except KorailAppError as error:
                if isinstance(error, KorailAuthError):
                    raise
                raise ReserveAborted(
                    f"코레일이 예약을 거절해 중단했습니다: {error.message or error}"
                ) from error
            self.jobs.pop(job.id, None)
            await self.on_success(job, hold, option, seat_classes)
            return True
        return False
