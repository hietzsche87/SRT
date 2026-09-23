"""텔레그램 핸들러."""

from __future__ import annotations

import itertools
import logging
import re
import time
import warnings
from dataclasses import replace
from datetime import date, datetime, timedelta
from html import escape

from korail_mobile_api import (
    KorailApiError,
    KorailSeatClass,
    ReservationHoldResponse,
    TrainSummary,
)
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from .config import Settings
from .formatting import WEEKDAYS, hold_message, reservations_message, train_line, ymd
from .korail import KorailService
from .macro import KST, MacroJob, MacroManager, SeatPref, seat_class_label, train_key

log = logging.getLogger(__name__)

# 조회 대화는 한 채팅에 하나뿐이라 per_message=False 가 의도된 동작입니다.
warnings.filterwarnings("ignore", message=".*per_message=False.*", category=PTBUserWarning)

DEP, ARR, DATE, TIME, PICK = range(5)

STATIONS = [
    "서울", "용산", "광명", "수서",
    "동탄", "천안아산", "오송", "대전",
    "동대구", "경주", "울산(통도사)", "부산",
    "광주송정", "목포", "전주", "강릉",
]

MAX_TRAINS = 30
MAX_ADULTS = 4

BTN_SEARCH = "🔎 예약하기"
BTN_STATUS = "📡 진행 중"
BTN_HISTORY = "🎫 예약 내역"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_SEARCH)], [KeyboardButton(BTN_STATUS), KeyboardButton(BTN_HISTORY)]],
    resize_keyboard=True,
    is_persistent=True,
)

HELP = (
    "🚄 <b>코레일 자동 예약 봇</b>\n\n"
    "1. <b>🔎 예약하기</b> (/search) — 출발역·도착역·날짜·시간을 고르고\n"
    "2. 원하는 열차들을 체크한 뒤 <b>▶️ 예약 시작</b>\n"
    "3. 빈 좌석이 보이면 자동으로 예약(결제 전)하고 알려 드립니다.\n"
    "4. 결제는 코레일톡 앱에서 기한 안에 하세요.\n\n"
    "/status — 진행 중인 자동 예약\n"
    "/stop — 자동 예약 모두 중지\n"
    "/reservations — 코레일 예약 내역\n"
    "/cancel — 입력 중인 조회 취소"
)


def _svc(context: ContextTypes.DEFAULT_TYPE) -> KorailService:
    return context.application.bot_data["korail"]


def _macros(context: ContextTypes.DEFAULT_TYPE) -> MacroManager:
    return context.application.bot_data["macros"]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


# ---------------------------------------------------------------- 접근 제어


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    allowed = _settings(context).allowed_user_ids
    if user is not None and user.id in allowed:
        return
    log.warning("허용되지 않은 사용자: %s", user.id if user else None)
    if update.effective_message is not None and user is not None:
        await update.effective_message.reply_text(
            f"⛔ 권한이 없습니다.\n당신의 텔레그램 ID: {user.id}\n"
            "서버의 ALLOWED_USER_IDS 에 이 값을 추가하세요."
        )
    elif update.callback_query is not None:
        await update.callback_query.answer("권한이 없습니다", show_alert=True)
    raise ApplicationHandlerStop


# ---------------------------------------------------------------- 기본 명령


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        HELP, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
    )


# ---------------------------------------------------------------- 조회 대화


def _station_keyboard(prefix: str, exclude: str | None = None) -> InlineKeyboardMarkup:
    names = [n for n in STATIONS if n != exclude]
    rows = [
        [InlineKeyboardButton(n, callback_data=f"{prefix}:{n}") for n in names[i:i + 4]]
        for i in range(0, len(names), 4)
    ]
    rows.append([InlineKeyboardButton("✖️ 취소", callback_data="x")])
    return InlineKeyboardMarkup(rows)


def _today() -> date:
    return datetime.now(KST).date()


def _date_keyboard() -> InlineKeyboardMarkup:
    today = _today()
    buttons = []
    for offset in range(21):
        d = today + timedelta(days=offset)
        label = f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"
        if offset == 0:
            label = "오늘 " + label
        buttons.append(InlineKeyboardButton(label, callback_data=f"dt:{d:%Y%m%d}"))
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("✖️ 취소", callback_data="x")])
    return InlineKeyboardMarkup(rows)


def _time_keyboard(selected_date: str) -> InlineKeyboardMarkup:
    now = datetime.now(KST)
    first_hour = now.hour if selected_date == f"{now:%Y%m%d}" else 0
    hours = list(range(first_hour, 24))
    buttons = [InlineKeyboardButton(f"{h:02d}시~", callback_data=f"tm:{h:02d}") for h in hours]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton("✖️ 취소", callback_data="x")])
    return InlineKeyboardMarkup(rows)


def _parse_date(text: str) -> str | None:
    digits = re.sub(r"\D", " ", text).split()
    today = _today()
    try:
        if len(digits) == 1 and len(digits[0]) == 8:
            d = datetime.strptime(digits[0], "%Y%m%d").date()
        elif len(digits) == 1 and len(digits[0]) == 4:
            d = date(today.year, int(digits[0][:2]), int(digits[0][2:]))
        elif len(digits) == 2:
            d = date(today.year, int(digits[0]), int(digits[1]))
        elif len(digits) == 3:
            d = date(int(digits[0]), int(digits[1]), int(digits[2]))
        else:
            return None
    except ValueError:
        return None
    if len(digits) in (1, 2) and len(digits[0]) != 8 and d < today:
        d = d.replace(year=d.year + 1)  # "1/3" 을 12월에 입력하면 내년
    if d < today:
        return None
    return f"{d:%Y%m%d}"


def _parse_time(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    if len(digits) in (1, 2):
        digits = digits.zfill(2) + "00"
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4:
        return None
    hour, minute = int(digits[:2]), int(digits[2:])
    if hour > 23 or minute > 59:
        return None
    return digits + "00"


async def _reply_or_edit(update: Update, text: str, markup: InlineKeyboardMarkup | None) -> None:
    if update.callback_query is not None:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=markup, parse_mode=ParseMode.HTML
        )


async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data.update(adults=1, seat=SeatPref.GENERAL)
    await update.effective_message.reply_text(
        "🚉 <b>출발역</b>을 고르거나 역 이름을 입력하세요.",
        reply_markup=_station_keyboard("dep"),
        parse_mode=ParseMode.HTML,
    )
    return DEP


def _input_value(update: Update) -> str:
    if update.callback_query is not None:
        return update.callback_query.data.split(":", 1)[1]
    return (update.effective_message.text or "").strip()


async def got_dep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    dep = _input_value(update)
    context.user_data["dep"] = dep
    await _reply_or_edit(
        update,
        f"출발: <b>{escape(dep)}</b>\n🏁 <b>도착역</b>을 고르거나 입력하세요.",
        _station_keyboard("arr", exclude=dep),
    )
    return ARR


async def got_arr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    arr = _input_value(update)
    if arr == context.user_data.get("dep"):
        await update.effective_message.reply_text("출발역과 도착역이 같습니다. 다시 입력하세요.")
        return ARR
    context.user_data["arr"] = arr
    await _reply_or_edit(
        update,
        f"{escape(context.user_data['dep'])} → <b>{escape(arr)}</b>\n"
        "📅 <b>날짜</b>를 고르거나 입력하세요 (예: 1025, 10/25, 20261025).",
        _date_keyboard(),
    )
    return DATE


async def got_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = _input_value(update)
    value = raw if update.callback_query is not None else _parse_date(raw)
    if value is None:
        await update.effective_message.reply_text("날짜를 이해하지 못했습니다. 예: 1025 또는 2026-10-25")
        return DATE
    context.user_data["date"] = value
    await _reply_or_edit(
        update,
        f"{escape(context.user_data['dep'])} → {escape(context.user_data['arr'])} · {ymd(value)}\n"
        "🕐 <b>몇 시 이후</b> 열차를 볼까요? (예: 07, 0730)",
        _time_keyboard(value),
    )
    return TIME


async def got_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = _input_value(update)
    value = raw + "0000" if update.callback_query is not None else _parse_time(raw)
    if value is None:
        await update.effective_message.reply_text("시간을 이해하지 못했습니다. 예: 07 또는 0730")
        return TIME
    context.user_data["time"] = value
    if update.callback_query is not None:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🔎 조회 중…")
    else:
        await update.effective_message.reply_text("🔎 조회 중…")

    ud = context.user_data
    query = _svc(context).make_query(ud["dep"], ud["arr"], ud["date"], value, ud["adults"])
    try:
        trains = await _svc(context).search(query)
    except KorailApiError as error:
        await update.effective_message.reply_text(
            f"❌ 조회 실패: {escape(str(error))}\n역 이름이 정확한지 확인하고 /search 로 다시 시도하세요.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END
    if not trains:
        await update.effective_message.reply_text("🚫 해당 조건의 열차가 없습니다. /search 로 다시 시도하세요.")
        return ConversationHandler.END
    ud.update(query=query, trains=trains[:MAX_TRAINS], selected=set())
    await update.effective_message.reply_text(
        _pick_text(context), reply_markup=_pick_keyboard(context), parse_mode=ParseMode.HTML
    )
    return PICK


def _pick_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    ud = context.user_data
    return (
        f"🚆 <b>{escape(ud['dep'])} → {escape(ud['arr'])}</b> · {ymd(ud['date'])}\n"
        "예약할 열차를 모두 체크한 뒤 <b>▶️ 예약 시작</b>을 누르세요.\n"
        "매진이어도 체크하면 빈 좌석이 날 때까지 계속 시도합니다."
    )


def _pick_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    ud = context.user_data
    rows = []
    for i, train in enumerate(ud["trains"]):
        mark = "☑️" if i in ud["selected"] else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {train_line(train)}", callback_data=f"tg:{i}")])
    if len(ud["trains"]) < MAX_TRAINS:
        rows.append([InlineKeyboardButton("⏬ 다음 열차 더 보기", callback_data="more")])
    rows.append([
        InlineKeyboardButton(f"💺 {ud['seat'].label}", callback_data="seat"),
        InlineKeyboardButton(f"👤 어른 {ud['adults']}명", callback_data="pax"),
    ])
    rows.append([
        InlineKeyboardButton(f"▶️ 예약 시작 ({len(ud['selected'])})", callback_data="go"),
        InlineKeyboardButton("✖️ 취소", callback_data="x"),
    ])
    return InlineKeyboardMarkup(rows)


async def pick_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cq = update.callback_query
    ud = context.user_data
    data = cq.data

    if data.startswith("tg:"):
        i = int(data[3:])
        ud["selected"] ^= {i}
    elif data == "seat":
        ud["seat"] = ud["seat"].next()
    elif data == "pax":
        ud["adults"] = ud["adults"] % MAX_ADULTS + 1
    elif data == "more":
        last = ud["trains"][-1]
        query = replace(ud["query"], departure_date=last.departure_date or ud["date"],
                        departure_time=last.departure_time or ud["time"])
        try:
            more = await _svc(context).search(query)
        except KorailApiError as error:
            await cq.answer(f"조회 실패: {error}"[:190], show_alert=True)
            return PICK
        known = {train_key(t) for t in ud["trains"]}
        new = [t for t in more if train_key(t) not in known]
        if not new:
            await cq.answer("더 이상 열차가 없습니다", show_alert=True)
            return PICK
        ud["trains"] = (ud["trains"] + new)[:MAX_TRAINS]
    elif data == "go":
        return await _start_macro(update, context)

    await cq.answer()
    await cq.edit_message_text(
        _pick_text(context), reply_markup=_pick_keyboard(context), parse_mode=ParseMode.HTML
    )
    return PICK


async def _start_macro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cq = update.callback_query
    ud = context.user_data
    if not ud["selected"]:
        await cq.answer("열차를 하나 이상 체크하세요", show_alert=True)
        return PICK
    targets: list[TrainSummary] = [ud["trains"][i] for i in sorted(ud["selected"])]
    query = replace(ud["query"], passengers=ud["adults"])
    job = _macros(context).start(
        chat_id=update.effective_chat.id,
        query=query,
        targets=targets,
        seat_pref=ud["seat"],
        adults=ud["adults"],
    )
    await cq.answer("자동 예약을 시작합니다")
    names = "\n".join(f"  • {escape(train_line(t))}" for t in job.targets)
    await cq.edit_message_text(
        f"▶️ <b>자동 예약 #{job.id} 시작</b>\n"
        f"{escape(job.title)} · {ud['seat'].label} · 어른 {ud['adults']}명\n{names}\n\n"
        "빈 좌석이 나면 바로 예약하고 알려 드립니다.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏹ 중지", callback_data=f"stop:{job.id}")]]
        ),
    )
    ud.clear()
    return ConversationHandler.END


async def search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query is not None:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("취소했습니다.")
    else:
        await update.effective_message.reply_text("취소했습니다.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ---------------------------------------------------------------- 매크로 관리


def _elapsed(job: MacroJob) -> str:
    seconds = int(time.time() - job.started_at)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}시간 {rest // 60}분" if hours else f"{rest // 60}분 {rest % 60}초"


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs = _macros(context).jobs_for(update.effective_chat.id)
    if not jobs:
        await update.effective_message.reply_text("진행 중인 자동 예약이 없습니다.")
        return
    lines = ["📡 <b>진행 중인 자동 예약</b>"]
    buttons = []
    for job in jobs:
        lines.append(
            f"\n<b>#{job.id}</b> {escape(job.title)} · {job.seat_pref.label} · 어른 {job.adults}명\n"
            f"  열차 {len(job.targets)}편 · 조회 {job.attempts}회 · {_elapsed(job)} 경과"
        )
        if job.last_error and job.errors:
            lines.append(f"  ⚠️ 최근 오류({job.errors}회 연속): {escape(job.last_error[:100])}")
        buttons.append([InlineKeyboardButton(f"⏹ #{job.id} 중지", callback_data=f"stop:{job.id}")])
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
    )


async def stop_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    macros = _macros(context)
    jobs = macros.jobs_for(update.effective_chat.id)
    for job in jobs:
        macros.stop(job.id)
    await update.effective_message.reply_text(
        f"⏹ 자동 예약 {len(jobs)}건을 중지했습니다." if jobs else "진행 중인 자동 예약이 없습니다."
    )


async def stop_one(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    job_id = int(cq.data.split(":", 1)[1])
    job = _macros(context).stop(job_id)
    if job is None:
        await cq.answer("이미 끝난 작업입니다")
        return
    await cq.answer("중지했습니다")
    await cq.edit_message_reply_markup(reply_markup=None)
    await cq.message.reply_text(f"⏹ 자동 예약 #{job.id} ({job.title}) 중지 — 조회 {job.attempts}회")


async def reservations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.effective_message.reply_text("🎫 예약 내역 조회 중…")
    try:
        history = await _svc(context).reservations()
    except KorailApiError as error:
        await msg.edit_text(f"❌ 조회 실패: {error}")
        return
    await msg.edit_text(reservations_message(history), parse_mode=ParseMode.HTML)


async def cancel_hold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    hold_id = int(cq.data.split(":", 1)[1])
    holds: dict[int, ReservationHoldResponse] = context.application.bot_data["holds"]
    hold = holds.get(hold_id)
    if hold is None:
        await cq.answer("취소 정보가 없습니다 (봇 재시작 등). 코레일톡 앱에서 취소하세요.", show_alert=True)
        return
    try:
        await _svc(context).cancel(hold)
    except KorailApiError as error:
        await cq.answer(f"취소 실패: {error}"[:190], show_alert=True)
        return
    holds.pop(hold_id, None)
    await cq.answer("예약을 취소했습니다")
    await cq.edit_message_reply_markup(reply_markup=None)
    await cq.message.reply_text("🗑 예약을 취소했습니다.")


# ---------------------------------------------------------------- 앱 구성


def build_application(settings: Settings) -> Application:
    app = ApplicationBuilder().token(settings.telegram_token).build()
    hold_ids = itertools.count(1)

    async def on_success(
        job: MacroJob, hold: ReservationHoldResponse, train: TrainSummary, seat_class: KorailSeatClass
    ) -> None:
        hold_id = next(hold_ids)
        app.bot_data["holds"][hold_id] = hold
        await app.bot.send_message(
            job.chat_id,
            hold_message(hold, train, seat_class_label(seat_class))
            + f"\n\n(자동 예약 #{job.id}, 조회 {job.attempts}회)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🗑 이 예약 취소", callback_data=f"cxl:{hold_id}")]]
            ),
        )

    async def on_stop(job: MacroJob, reason: str) -> None:
        await app.bot.send_message(job.chat_id, f"⏹ 자동 예약 #{job.id} ({job.title}) 중단\n{reason}")

    korail = KorailService(
        settings.korail_member_no, settings.korail_password, include_srt=settings.include_srt
    )
    macros = MacroManager(
        korail,
        on_success=on_success,
        on_stop=on_stop,
        interval=settings.poll_interval,
        jitter=settings.poll_jitter,
        max_consecutive_errors=settings.max_consecutive_errors,
        max_hours=settings.max_macro_hours,
    )
    app.bot_data.update(settings=settings, korail=korail, macros=macros, holds={})

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands([
            BotCommand("search", "열차 조회 · 자동 예약"),
            BotCommand("status", "진행 중인 자동 예약"),
            BotCommand("stop", "자동 예약 모두 중지"),
            BotCommand("reservations", "코레일 예약 내역"),
            BotCommand("help", "도움말"),
        ])
        try:
            await korail.login()
        except KorailApiError as error:
            log.error("시작 시 KORAIL 로그인 실패 (예약 시 다시 시도): %s", error)

    async def post_shutdown(application: Application) -> None:
        await macros.shutdown()
        korail.close()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    app.add_handler(TypeHandler(Update, guard), group=-1)

    text = filters.TEXT & ~filters.COMMAND
    menu = filters.Regex(f"^({re.escape(BTN_SEARCH)}|{re.escape(BTN_STATUS)}|{re.escape(BTN_HISTORY)})$")
    cancel_handlers = [
        CommandHandler("cancel", search_cancel),
        CallbackQueryHandler(search_cancel, pattern="^x$"),
    ]
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("search", search_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_SEARCH)}$"), search_start),
        ],
        states={
            DEP: [CallbackQueryHandler(got_dep, pattern="^dep:"), MessageHandler(text & ~menu, got_dep)],
            ARR: [CallbackQueryHandler(got_arr, pattern="^arr:"), MessageHandler(text & ~menu, got_arr)],
            DATE: [CallbackQueryHandler(got_date, pattern="^dt:"), MessageHandler(text & ~menu, got_date)],
            TIME: [CallbackQueryHandler(got_time, pattern="^tm:"), MessageHandler(text & ~menu, got_time)],
            PICK: [CallbackQueryHandler(pick_action, pattern="^(tg:\\d+|more|seat|pax|go)$")],
        },
        fallbacks=[
            *cancel_handlers,
            CommandHandler("search", search_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_SEARCH)}$"), search_start),
        ],
        allow_reentry=True,
        conversation_timeout=30 * 60,
    )
    app.add_handler(conversation)
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_STATUS)}$"), status))
    app.add_handler(CommandHandler("stop", stop_all))
    app.add_handler(CommandHandler("reservations", reservations))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_HISTORY)}$"), reservations))
    app.add_handler(CallbackQueryHandler(stop_one, pattern="^stop:\\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_hold, pattern="^cxl:\\d+$"))
    app.add_error_handler(_on_error)
    return app


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("핸들러 오류", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(f"⚠️ 오류가 발생했습니다: {context.error}")
        except Exception:  # noqa: BLE001 — 알림 실패는 무시
            pass
