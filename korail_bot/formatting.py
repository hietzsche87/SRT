"""텔레그램 메시지용 문자열 포맷."""

from __future__ import annotations

from html import escape

from korail_mobile_api import ReservationHistoryResponse, ReservationHoldResponse, TrainSummary

from .korail import has_general_seat, has_special_seat

WEEKDAYS = "월화수목금토일"


def hhmm(value: str | None) -> str:
    if not value or len(value) < 4:
        return "--:--"
    return f"{value[:2]}:{value[2:4]}"


def ymd(value: str | None) -> str:
    if not value or len(value) != 8:
        return value or "-"
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def train_name(train: TrainSummary) -> str:
    name = train.train_group_name or train.train_class_name or "열차"
    return f"{name} {train.train_no.lstrip('0') or train.train_no}"


def seat_status(train: TrainSummary) -> str:
    general = "일반 ✅" if has_general_seat(train) else "일반 ❌"
    special = "특실 ✅" if has_special_seat(train) else "특실 ❌"
    return f"{general} · {special}"


def train_line(train: TrainSummary) -> str:
    """버튼에 들어갈 한 줄 요약 (텔레그램 버튼은 HTML 을 해석하지 않음)."""
    return (
        f"{hhmm(train.departure_time)}→{hhmm(train.arrival_time)} "
        f"{train_name(train)} | {seat_status(train)}"
    )


def hold_message(hold: ReservationHoldResponse, train: TrainSummary, seat_label: str) -> str:
    lines = [
        "🎉 <b>예약 성공!</b>",
        "",
        f"🚆 {escape(train_name(train))} ({escape(seat_label)})",
        f"📍 {escape(train.departure_station_name or '')} {hhmm(train.departure_time)} → "
        f"{escape(train.arrival_station_name or '')} {hhmm(train.arrival_time)}",
        f"📅 {ymd(train.departure_date)}",
    ]
    price = hold.total_price or hold.total_fare
    if price:
        try:
            lines.append(f"💰 {int(price):,}원")
        except ValueError:
            lines.append(f"💰 {escape(price)}원")
    if hold.payment_deadline_date or hold.payment_deadline_time:
        lines.append(
            f"⏰ 결제 기한: {ymd(hold.payment_deadline_date)} {hhmm(hold.payment_deadline_time)}"
        )
    elif hold.payment_deadline_message:
        lines.append(f"⏰ {escape(hold.payment_deadline_message)}")
    lines += ["", "⚠️ 결제 전 예약입니다. 기한 안에 <b>코레일톡 앱</b>에서 결제하세요."]
    return "\n".join(lines)


def reservations_message(history: ReservationHistoryResponse) -> str:
    journeys = getattr(history, "journeys", ()) or ()
    if not journeys:
        return "📭 현재 예약 내역이 없습니다."
    lines = ["📋 <b>예약 내역</b>"]
    for journey in journeys:
        for train in journey.trains:
            lines.append(
                f"\n• {ymd(train.run_date)} {escape(train.train_class_name or '')} "
                f"{escape((train.train_no or '').lstrip('0'))}\n"
                f"  {escape(train.departure_station or '')} {hhmm(train.departure_time)} → "
                f"{escape(train.arrival_station or '')} {hhmm(train.arrival_time)}"
            )
            deadline_date = getattr(train, "payment_deadline_date", None)
            deadline_time = getattr(train, "payment_deadline_time", None)
            if deadline_date or deadline_time:
                lines.append(f"  ⏰ 결제 기한: {ymd(deadline_date)} {hhmm(deadline_time)}")
    lines.append("\n결제·취소는 코레일톡 앱에서 할 수 있습니다.")
    return "\n".join(lines)
