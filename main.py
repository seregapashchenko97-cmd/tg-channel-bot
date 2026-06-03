import asyncio
import calendar
import json
import logging
import os
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from database import init_db, upsert_user
from scheduler import scheduler


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = os.getenv("DB_NAME", "bot.db")
TIMEZONE_NAME = os.getenv("TIMEZONE", "Europe/Kyiv")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "15"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it to .env or environment variables.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
timezone = ZoneInfo(TIMEZONE_NAME)

MONTHS = {
    1: "Січень",
    2: "Лютий",
    3: "Березень",
    4: "Квітень",
    5: "Травень",
    6: "Червень",
    7: "Липень",
    8: "Серпень",
    9: "Вересень",
    10: "Жовтень",
    11: "Листопад",
    12: "Грудень",
}

REPEAT_LABELS = {"none": "Без повтору", "daily": "Щодня", "weekly": "Щотижня"}
PARSE_LABELS = {"": "Без форматування", "HTML": "HTML", "MarkdownV2": "MarkdownV2"}


class PostForm(StatesGroup):
    choosing_channels = State()
    writing_content = State()
    choosing_date = State()
    choosing_hour = State()
    choosing_minute = State()
    choosing_repeat = State()
    choosing_format = State()
    writing_buttons = State()
    confirming_preview = State()


class EditForm(StatesGroup):
    writing_content = State()
    choosing_date = State()
    choosing_hour = State()
    choosing_minute = State()


def main_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="📢 Мої канали"), KeyboardButton(text="➕ Додати канал")],
        [KeyboardButton(text="📝 Створити пост"), KeyboardButton(text="📋 Черга")],
    ]
    if ADMIN_ID and user_id == ADMIN_ID:
        keyboard.append([KeyboardButton(text="📊 Статистика")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


async def get_user_channels(user_id: int) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT channel_id, title FROM channels WHERE user_id=? ORDER BY title",
            (user_id,),
        )
        return await cursor.fetchall()


def selected_channels_keyboard(channels: list[aiosqlite.Row], selected: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    selected_set = set(selected)
    for channel in channels:
        channel_id = channel["channel_id"]
        mark = "✅ " if channel_id in selected_set else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{channel['title']}", callback_data=f"sel:{channel_id}")])
    buttons.append([InlineKeyboardButton(text="Готово", callback_data="sel_done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def month_keyboard(year: int, month: int, back_to: str) -> InlineKeyboardMarkup:
    previous_month = month - 1
    previous_year = year
    next_month = month + 1
    next_year = year
    if previous_month == 0:
        previous_month = 12
        previous_year -= 1
    if next_month == 13:
        next_month = 1
        next_year += 1

    buttons = [
        [
            InlineKeyboardButton(text="<", callback_data=f"cal:month:{previous_year}:{previous_month}:{back_to}"),
            InlineKeyboardButton(text=f"{MONTHS[month]} {year}", callback_data="cal:ignore"),
            InlineKeyboardButton(text=">", callback_data=f"cal:month:{next_year}:{next_month}:{back_to}"),
        ],
        [
            InlineKeyboardButton(text="Пн", callback_data="cal:ignore"),
            InlineKeyboardButton(text="Вт", callback_data="cal:ignore"),
            InlineKeyboardButton(text="Ср", callback_data="cal:ignore"),
            InlineKeyboardButton(text="Чт", callback_data="cal:ignore"),
            InlineKeyboardButton(text="Пт", callback_data="cal:ignore"),
            InlineKeyboardButton(text="Сб", callback_data="cal:ignore"),
            InlineKeyboardButton(text="Нд", callback_data="cal:ignore"),
        ],
    ]

    today = datetime.now(timezone).date()
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:ignore"))
                continue
            selected_date = date(year, month, day)
            if selected_date < today:
                row.append(InlineKeyboardButton(text=".", callback_data="cal:ignore"))
            else:
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal:day:{year}:{month}:{day}"))
        buttons.append(row)

    if back_to == "create":
        buttons.append([InlineKeyboardButton(text="⚡ Опублікувати зараз", callback_data="create_publish_now")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{back_to}:date")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def hour_keyboard(back_to: str) -> InlineKeyboardMarkup:
    buttons = []
    for start in range(0, 24, 4):
        buttons.append([InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=f"hour:{hour}") for hour in range(start, start + 4)])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{back_to}:hour")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def minute_keyboard(back_to: str) -> InlineKeyboardMarkup:
    buttons = []
    for start in range(0, 60, 15):
        buttons.append([InlineKeyboardButton(text=f"{minute:02d}", callback_data=f"minute:{minute}") for minute in range(start, start + 15, 5)])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{back_to}:minute")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def repeat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без повтору", callback_data="repeat:none")],
            [InlineKeyboardButton(text="Щодня", callback_data="repeat:daily")],
            [InlineKeyboardButton(text="Щотижня", callback_data="repeat:weekly")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:create:repeat")],
        ]
    )


def format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без форматування", callback_data="format:")],
            [InlineKeyboardButton(text="HTML", callback_data="format:HTML")],
            [InlineKeyboardButton(text="MarkdownV2", callback_data="format:MarkdownV2")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:create:format")],
        ]
    )


def buttons_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без кнопок", callback_data="buttons:none")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:create:buttons")],
        ]
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Додати в чергу", callback_data="confirm:save")],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="confirm:cancel")],
        ]
    )


def edit_keyboard(group_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Опублікувати зараз", callback_data=f"publish_now:{group_id}")],
            [
                InlineKeyboardButton(text="✏️ Змінити пост", callback_data=f"edit_content:{group_id}"),
                InlineKeyboardButton(text="🕒 Змінити час", callback_data=f"edit_time:{group_id}"),
            ],
            [InlineKeyboardButton(text="🗑 Скасувати", callback_data=f"cancel:{group_id}")],
        ]
    )


def post_buttons_from_json(buttons_json: str | None) -> InlineKeyboardMarkup | None:
    if not buttons_json:
        return None
    rows = json.loads(buttons_json)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button["text"], url=button["url"]) for button in row]
            for row in rows
        ]
    )


def parse_buttons(text: str) -> str | None:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "|" not in line:
            return None
        title, url = [part.strip() for part in line.split("|", 1)]
        if not title or not url.startswith(("http://", "https://")):
            return None
        rows.append([{"text": title, "url": url}])
    return json.dumps(rows, ensure_ascii=False) if rows else None


def get_message_content(message: Message) -> dict[str, str] | None:
    if message.photo:
        return {"media_type": "photo", "media_file_id": message.photo[-1].file_id, "text": message.caption or ""}
    if message.video:
        return {"media_type": "video", "media_file_id": message.video.file_id, "text": message.caption or ""}
    if message.text:
        return {"media_type": "text", "media_file_id": "", "text": message.text}
    return None


def build_publish_at(data: dict, minute: int) -> datetime:
    return datetime.fromisoformat(data["publish_date"]).replace(
        hour=data["publish_hour"],
        minute=minute,
        second=0,
        tzinfo=timezone,
    )


async def send_preview_message(message: Message, data: dict) -> None:
    reply_markup = post_buttons_from_json(data.get("buttons_json"))
    parse_mode = data.get("parse_mode") or None
    text = data.get("text") or None

    if data.get("media_type") == "photo":
        await message.answer_photo(data["media_file_id"], caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
    elif data.get("media_type") == "video":
        await message.answer_video(data["media_file_id"], caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        await message.answer(data["text"], parse_mode=parse_mode, reply_markup=reply_markup)


async def show_preview(message: Message, state: FSMContext, user_id: int) -> None:
    data = await state.get_data()
    channels = await get_user_channels(user_id)
    selected = set(data["channel_ids"])
    channel_titles = [channel["title"] for channel in channels if channel["channel_id"] in selected]

    await message.answer("👀 Передперегляд поста:")
    await send_preview_message(message, data)
    await message.answer(
        "Перевірте налаштування:\n\n"
        f"Канали: {', '.join(channel_titles)}\n"
        f"Час: {data['publish_at']}\n"
        f"Повтор: {REPEAT_LABELS[data['repeat_type']]}\n"
        f"Форматування: {PARSE_LABELS[data.get('parse_mode') or '']}",
        reply_markup=confirm_keyboard(),
    )


async def save_post_group(user_id: int, data: dict) -> None:
    group_id = uuid.uuid4().hex
    publish_at_dt = datetime.strptime(data["publish_at"], "%Y-%m-%d %H:%M:%S")
    repeat_weekday = publish_at_dt.weekday() if data["repeat_type"] == "weekly" else None

    async with aiosqlite.connect(DB_NAME) as db:
        for channel_id in data["channel_ids"]:
            await db.execute(
                """
                INSERT INTO posts(
                    group_id, user_id, channel_id, text, media_type, media_file_id,
                    parse_mode, buttons_json, publish_at, repeat_type, repeat_weekday
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    user_id,
                    channel_id,
                    data["text"],
                    data["media_type"],
                    data.get("media_file_id") or None,
                    data.get("parse_mode") or None,
                    data.get("buttons_json"),
                    data["publish_at"],
                    data["repeat_type"],
                    repeat_weekday,
                ),
            )
        await db.commit()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await upsert_user(DB_NAME, message.from_user)
    await message.answer(
        "🚀 PostPilot UA\n\n"
        "Безкоштовний автопостинг для Telegram-каналів.",
        reply_markup=main_keyboard(message.from_user.id),
    )


@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: Message) -> None:
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("Ця кнопка доступна тільки власнику бота.")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        channels = (await (await db.execute("SELECT COUNT(*) FROM channels")).fetchone())[0]
        pending = (await (await db.execute("SELECT COUNT(*) FROM posts WHERE status='pending'")).fetchone())[0]
        sent = (await (await db.execute("SELECT COUNT(*) FROM posts WHERE status='sent'")).fetchone())[0]

    await message.answer(
        "📊 Статистика бота\n\n"
        f"Користувачів: {users}\n"
        f"Каналів: {channels}\n"
        f"Постів у черзі: {pending}\n"
        f"Опубліковано: {sent}"
    )


@dp.message(F.text == "➕ Додати канал")
async def add_channel_hint(message: Message) -> None:
    await message.answer(
        "1. Додайте цього бота в адміністратори каналу.\n"
        "2. Дайте право публікувати повідомлення.\n"
        "3. Перешліть сюди будь-який пост із каналу."
    )


@dp.message(F.forward_from_chat)
async def add_channel(message: Message) -> None:
    chat = message.forward_from_chat
    if chat.type != "channel":
        await message.answer("Потрібно переслати повідомлення саме з Telegram-каналу.")
        return

    me = await bot.get_me()
    try:
        member = await bot.get_chat_member(chat.id, me.id)
    except Exception:
        await message.answer("Додайте мене в адміністратори каналу з правом публікації й спробуйте ще раз.")
        return

    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await message.answer("Я ще не адміністратор цього каналу.")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO channels(user_id, channel_id, title, username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET
                title=excluded.title,
                username=excluded.username
            """,
            (message.from_user.id, chat.id, chat.title, chat.username),
        )
        await db.commit()

    await message.answer(f"✅ Канал додано:\n{chat.title}")


@dp.message(F.text == "📢 Мої канали")
async def my_channels(message: Message) -> None:
    rows = await get_user_channels(message.from_user.id)
    if not rows:
        await message.answer("У вас ще немає каналів.\n\nНатисніть «➕ Додати канал».")
        return

    text = "📢 Ваші канали:\n\n"
    for row in rows:
        text += f"• {row['title']}\n  ID: {row['channel_id']}\n\n"
    await message.answer(text)


@dp.message(F.text == "📝 Створити пост")
async def create_post(message: Message, state: FSMContext) -> None:
    channels = await get_user_channels(message.from_user.id)
    if not channels:
        await message.answer("Спочатку додайте канал через «➕ Додати канал».")
        return

    await state.clear()
    await state.update_data(channel_ids=[])
    await state.set_state(PostForm.choosing_channels)
    await message.answer("Виберіть один або кілька каналів:", reply_markup=selected_channels_keyboard(channels, []))


@dp.callback_query(PostForm.choosing_channels, F.data.startswith("sel:"))
async def toggle_post_channel(callback: CallbackQuery, state: FSMContext) -> None:
    channel_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    selected = data.get("channel_ids", [])
    selected.remove(channel_id) if channel_id in selected else selected.append(channel_id)

    await state.update_data(channel_ids=selected)
    await callback.message.edit_reply_markup(reply_markup=selected_channels_keyboard(await get_user_channels(callback.from_user.id), selected))
    await callback.answer()


@dp.callback_query(PostForm.choosing_channels, F.data == "sel_done")
async def finish_channel_selection(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("channel_ids"):
        await callback.answer("Оберіть хоча б один канал.", show_alert=True)
        return

    await state.set_state(PostForm.writing_content)
    await callback.message.answer("Надішліть текст, фото з підписом або відео з підписом.")
    await callback.answer()


@dp.message(PostForm.writing_content)
async def write_post_content(message: Message, state: FSMContext) -> None:
    content = get_message_content(message)
    if not content:
        await message.answer("Надішліть текст, фото або відео.")
        return

    now = datetime.now(timezone)
    await state.update_data(**content)
    await state.set_state(PostForm.choosing_date)
    await message.answer(
        f"Виберіть дату публікації.\nЧасовий пояс: {TIMEZONE_NAME}",
        reply_markup=month_keyboard(now.year, now.month, "create"),
    )


@dp.callback_query(F.data == "cal:ignore")
async def ignore_calendar_button(callback: CallbackQuery) -> None:
    await callback.answer()


@dp.callback_query(F.data.startswith("cal:month:"))
async def change_calendar_month(callback: CallbackQuery) -> None:
    _, _, year, month, back_to = callback.data.split(":")
    await callback.message.edit_reply_markup(reply_markup=month_keyboard(int(year), int(month), back_to))
    await callback.answer()


@dp.callback_query(PostForm.choosing_date, F.data == "create_publish_now")
async def create_publish_now(callback: CallbackQuery, state: FSMContext) -> None:
    publish_at = datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")
    await state.update_data(publish_at=publish_at, repeat_type="none")
    await state.set_state(PostForm.choosing_format)
    await callback.message.answer("Оберіть форматування тексту:", reply_markup=format_keyboard())
    await callback.answer()


@dp.callback_query(PostForm.choosing_date, F.data.startswith("cal:day:"))
async def choose_post_date(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, year, month, day = callback.data.split(":")
    selected_date = date(int(year), int(month), int(day))
    await state.update_data(publish_date=selected_date.isoformat())
    await state.set_state(PostForm.choosing_hour)
    await callback.message.answer(f"Дата: {selected_date.strftime('%d.%m.%Y')}\nВиберіть годину:", reply_markup=hour_keyboard("create"))
    await callback.answer()


@dp.callback_query(PostForm.choosing_hour, F.data.startswith("hour:"))
async def choose_post_hour(callback: CallbackQuery, state: FSMContext) -> None:
    hour = int(callback.data.split(":", 1)[1])
    await state.update_data(publish_hour=hour)
    await state.set_state(PostForm.choosing_minute)
    await callback.message.answer(f"Година: {hour:02d}:00\nВиберіть хвилини:", reply_markup=minute_keyboard("create"))
    await callback.answer()


@dp.callback_query(PostForm.choosing_minute, F.data.startswith("minute:"))
async def choose_post_minute(callback: CallbackQuery, state: FSMContext) -> None:
    minute = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    publish_at_dt = build_publish_at(data, minute)
    if publish_at_dt <= datetime.now(timezone):
        await state.set_state(PostForm.choosing_date)
        now = datetime.now(timezone)
        await callback.message.answer("Цей час уже минув. Виберіть інший:", reply_markup=month_keyboard(now.year, now.month, "create"))
        await callback.answer()
        return

    await state.update_data(publish_at=publish_at_dt.strftime("%Y-%m-%d %H:%M:%S"))
    await state.set_state(PostForm.choosing_repeat)
    await callback.message.answer("Оберіть повтор публікації:", reply_markup=repeat_keyboard())
    await callback.answer()


@dp.callback_query(PostForm.choosing_repeat, F.data.startswith("repeat:"))
async def choose_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(repeat_type=callback.data.split(":", 1)[1])
    await state.set_state(PostForm.choosing_format)
    await callback.message.answer("Оберіть форматування тексту:", reply_markup=format_keyboard())
    await callback.answer()


@dp.callback_query(PostForm.choosing_format, F.data.startswith("format:"))
async def choose_format(callback: CallbackQuery, state: FSMContext) -> None:
    parse_mode = callback.data.split(":", 1)[1] or None
    await state.update_data(parse_mode=parse_mode)
    await state.set_state(PostForm.writing_buttons)
    await callback.message.answer(
        "Додайте кнопки під постом.\n\n"
        "Формат кожної кнопки:\n"
        "Назва | https://site.com\n\n"
        "Кожна кнопка з нового рядка.",
        reply_markup=buttons_keyboard(),
    )
    await callback.answer()


@dp.callback_query(PostForm.writing_buttons, F.data == "buttons:none")
async def no_buttons(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(buttons_json=None)
    await state.set_state(PostForm.confirming_preview)
    await show_preview(callback.message, state, callback.from_user.id)
    await callback.answer()


@dp.message(PostForm.writing_buttons)
async def write_buttons(message: Message, state: FSMContext) -> None:
    buttons_json = parse_buttons(message.text or "")
    if buttons_json is None:
        await message.answer("Не бачу кнопки. Приклад:\nСайт | https://example.com")
        return

    await state.update_data(buttons_json=buttons_json)
    await state.set_state(PostForm.confirming_preview)
    await show_preview(message, state, message.from_user.id)


@dp.callback_query(PostForm.confirming_preview, F.data == "confirm:cancel")
async def cancel_preview(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Створення поста скасовано.", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()


@dp.callback_query(PostForm.confirming_preview, F.data == "confirm:save")
async def confirm_preview(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await save_post_group(callback.from_user.id, data)
    await state.clear()
    await callback.message.answer(f"✅ Пост додано в чергу.\n\nЧас публікації: {data['publish_at']}", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()


@dp.callback_query(F.data.startswith("back:create:"))
async def back_create(callback: CallbackQuery, state: FSMContext) -> None:
    step = callback.data.split(":")[2]
    data = await state.get_data()
    now = datetime.now(timezone)

    if step == "date":
        await state.set_state(PostForm.writing_content)
        await callback.message.answer("Надішліть пост ще раз: текст, фото або відео.")
    elif step == "hour":
        await state.set_state(PostForm.choosing_date)
        await callback.message.answer("Виберіть дату:", reply_markup=month_keyboard(now.year, now.month, "create"))
    elif step == "minute":
        await state.set_state(PostForm.choosing_hour)
        selected_date = date.fromisoformat(data["publish_date"])
        await callback.message.answer(f"Дата: {selected_date.strftime('%d.%m.%Y')}\nВиберіть годину:", reply_markup=hour_keyboard("create"))
    elif step == "repeat":
        await state.set_state(PostForm.choosing_minute)
        await callback.message.answer("Виберіть хвилини:", reply_markup=minute_keyboard("create"))
    elif step == "format":
        await state.set_state(PostForm.choosing_repeat)
        await callback.message.answer("Оберіть повтор публікації:", reply_markup=repeat_keyboard())
    elif step == "buttons":
        await state.set_state(PostForm.choosing_format)
        await callback.message.answer("Оберіть форматування тексту:", reply_markup=format_keyboard())

    await callback.answer()


@dp.message(F.text == "📋 Черга")
async def queue(message: Message) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                posts.group_id, posts.publish_at, posts.text, posts.media_type,
                posts.repeat_type, posts.parse_mode,
                GROUP_CONCAT(channels.title, ', ') AS channel_titles
            FROM posts
            JOIN channels
                ON channels.user_id = posts.user_id
               AND channels.channel_id = posts.channel_id
            WHERE posts.user_id=? AND posts.status='pending'
            GROUP BY posts.group_id, posts.publish_at, posts.text, posts.media_type, posts.repeat_type, posts.parse_mode
            ORDER BY posts.publish_at ASC
            LIMIT 10
            """,
            (message.from_user.id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("Черга порожня.")
        return

    for index, row in enumerate(rows, start=1):
        preview = row["text"].replace("\n", " ")[:100] if row["text"] else "Без підпису"
        media = {"text": "текст", "photo": "фото", "video": "відео"}.get(row["media_type"], row["media_type"])
        await message.answer(
            f"📋 Пост {index}\n\n"
            f"Канали: {row['channel_titles']}\n"
            f"Тип: {media}\n"
            f"Час: {row['publish_at']}\n"
            f"Повтор: {REPEAT_LABELS.get(row['repeat_type'], row['repeat_type'])}\n"
            f"Форматування: {PARSE_LABELS.get(row['parse_mode'] or '', row['parse_mode'] or '')}\n"
            f"Текст: {preview}",
            reply_markup=edit_keyboard(row["group_id"]),
        )


@dp.callback_query(F.data.startswith("publish_now:"))
async def publish_now(callback: CallbackQuery) -> None:
    group_id = callback.data.split(":", 1)[1]
    now = datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            UPDATE posts
            SET publish_at=?, repeat_type='none', error=NULL
            WHERE group_id=? AND user_id=? AND status='pending'
            """,
            (now, group_id, callback.from_user.id),
        )
        await db.commit()

    if cursor.rowcount == 0:
        await callback.answer("Пост не знайдено.", show_alert=True)
        return

    await callback.message.answer("⚡ Пост буде опубліковано протягом кількох секунд.")
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_post(callback: CallbackQuery) -> None:
    group_id = callback.data.split(":", 1)[1]
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "UPDATE posts SET status='cancelled' WHERE group_id=? AND user_id=? AND status='pending'",
            (group_id, callback.from_user.id),
        )
        await db.commit()

    if cursor.rowcount == 0:
        await callback.answer("Пост не знайдено.", show_alert=True)
        return

    await callback.message.answer("🗑 Пост скасовано.")
    await callback.answer()


@dp.callback_query(F.data.startswith("edit_content:"))
async def edit_content_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(group_id=callback.data.split(":", 1)[1])
    await state.set_state(EditForm.writing_content)
    await callback.message.answer("Надішліть новий текст, фото з підписом або відео з підписом.")
    await callback.answer()


@dp.message(EditForm.writing_content)
async def edit_content_save(message: Message, state: FSMContext) -> None:
    content = get_message_content(message)
    if not content:
        await message.answer("Надішліть текст, фото або відео.")
        return

    data = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            UPDATE posts
            SET text=?, media_type=?, media_file_id=?
            WHERE group_id=? AND user_id=? AND status='pending'
            """,
            (content["text"], content["media_type"], content.get("media_file_id") or None, data["group_id"], message.from_user.id),
        )
        await db.commit()

    await state.clear()
    await message.answer("✅ Пост оновлено." if cursor.rowcount else "Пост не знайдено.", reply_markup=main_keyboard(message.from_user.id))


@dp.callback_query(F.data.startswith("edit_time:"))
async def edit_time_start(callback: CallbackQuery, state: FSMContext) -> None:
    now = datetime.now(timezone)
    await state.clear()
    await state.update_data(group_id=callback.data.split(":", 1)[1])
    await state.set_state(EditForm.choosing_date)
    await callback.message.answer("Виберіть нову дату:", reply_markup=month_keyboard(now.year, now.month, "edit"))
    await callback.answer()


@dp.callback_query(EditForm.choosing_date, F.data.startswith("cal:day:"))
async def edit_choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, year, month, day = callback.data.split(":")
    selected_date = date(int(year), int(month), int(day))
    await state.update_data(publish_date=selected_date.isoformat())
    await state.set_state(EditForm.choosing_hour)
    await callback.message.answer(f"Дата: {selected_date.strftime('%d.%m.%Y')}\nВиберіть годину:", reply_markup=hour_keyboard("edit"))
    await callback.answer()


@dp.callback_query(EditForm.choosing_hour, F.data.startswith("hour:"))
async def edit_choose_hour(callback: CallbackQuery, state: FSMContext) -> None:
    hour = int(callback.data.split(":", 1)[1])
    await state.update_data(publish_hour=hour)
    await state.set_state(EditForm.choosing_minute)
    await callback.message.answer(f"Година: {hour:02d}:00\nВиберіть хвилини:", reply_markup=minute_keyboard("edit"))
    await callback.answer()


@dp.callback_query(EditForm.choosing_minute, F.data.startswith("minute:"))
async def edit_choose_minute(callback: CallbackQuery, state: FSMContext) -> None:
    minute = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    publish_at_dt = build_publish_at(data, minute)
    if publish_at_dt <= datetime.now(timezone):
        now = datetime.now(timezone)
        await state.set_state(EditForm.choosing_date)
        await callback.message.answer("Цей час уже минув. Виберіть інший:", reply_markup=month_keyboard(now.year, now.month, "edit"))
        await callback.answer()
        return

    publish_at = publish_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "UPDATE posts SET publish_at=?, error=NULL WHERE group_id=? AND user_id=? AND status='pending'",
            (publish_at, data["group_id"], callback.from_user.id),
        )
        await db.commit()

    await state.clear()
    await callback.message.answer(f"✅ Час оновлено: {publish_at}" if cursor.rowcount else "Пост не знайдено.", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()


@dp.callback_query(F.data.startswith("back:edit:"))
async def back_edit(callback: CallbackQuery, state: FSMContext) -> None:
    step = callback.data.split(":")[2]
    data = await state.get_data()
    now = datetime.now(timezone)
    if step == "date":
        await state.clear()
        await callback.message.answer("Редагування часу скасовано.", reply_markup=main_keyboard(callback.from_user.id))
    elif step == "hour":
        await state.set_state(EditForm.choosing_date)
        await callback.message.answer("Виберіть дату:", reply_markup=month_keyboard(now.year, now.month, "edit"))
    elif step == "minute":
        await state.set_state(EditForm.choosing_hour)
        selected_date = date.fromisoformat(data["publish_date"])
        await callback.message.answer(f"Дата: {selected_date.strftime('%d.%m.%Y')}\nВиберіть годину:", reply_markup=hour_keyboard("edit"))
    await callback.answer()


@dp.message()
async def fallback(message: Message) -> None:
    await message.answer("Я не впізнав команду. Скористайтеся кнопками меню.", reply_markup=main_keyboard(message.from_user.id))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db(DB_NAME)
    asyncio.create_task(
        scheduler(
            bot=bot,
            db_name=DB_NAME,
            timezone_name=TIMEZONE_NAME,
            interval_seconds=CHECK_INTERVAL_SECONDS,
        )
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
