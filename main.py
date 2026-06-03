import asyncio
import calendar
import logging
import os
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it to .env or environment variables.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
timezone = ZoneInfo(TIMEZONE_NAME)


class PostForm(StatesGroup):
    choosing_channel = State()
    writing_text = State()
    choosing_date = State()
    choosing_hour = State()
    choosing_minute = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📢 Мої канали"),
                KeyboardButton(text="➕ Додати канал"),
            ],
            [
                KeyboardButton(text="📝 Створити пост"),
                KeyboardButton(text="📋 Черга"),
            ],
        ],
        resize_keyboard=True,
    )


async def user_channels_keyboard(user_id: int, prefix: str) -> InlineKeyboardMarkup:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT channel_id, title
            FROM channels
            WHERE user_id=?
            ORDER BY title
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()

    buttons = [
        [InlineKeyboardButton(text=title, callback_data=f"{prefix}:{channel_id}")]
        for channel_id, title in rows
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def channel_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM channels WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
    return row[0]


def month_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    month_title = date(year, month, 1).strftime("%B %Y")
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
            InlineKeyboardButton(text="‹", callback_data=f"cal:month:{previous_year}:{previous_month}"),
            InlineKeyboardButton(text=month_title, callback_data="cal:ignore"),
            InlineKeyboardButton(text="›", callback_data=f"cal:month:{next_year}:{next_month}"),
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
                row.append(InlineKeyboardButton(text="·", callback_data="cal:ignore"))
            else:
                row.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=f"cal:day:{year}:{month}:{day}",
                    )
                )
        buttons.append(row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def hour_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for start in range(0, 24, 4):
        buttons.append(
            [
                InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=f"hour:{hour}")
                for hour in range(start, start + 4)
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def minute_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for start in range(0, 60, 15):
        buttons.append(
            [
                InlineKeyboardButton(text=f"{minute:02d}", callback_data=f"minute:{minute}")
                for minute in range(start, start + 15, 5)
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await upsert_user(DB_NAME, message.from_user)
    await message.answer(
        "🚀 PostPilot UA\n\n"
        "Безкоштовний автопостинг для Telegram-каналів.\n\n"
        "Щоб додати канал, зробіть бота адміністратором каналу "
        "з правом публікації, а потім перешліть сюди будь-який пост із цього каналу.",
        reply_markup=main_keyboard(),
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
        await message.answer(
            "Я бачу канал, але не можу перевірити права.\n\n"
            "Додайте мене в адміністратори каналу з правом публікації й спробуйте ще раз."
        )
        return

    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        await message.answer(
            "Я ще не адміністратор цього каналу.\n\n"
            "Додайте мене в адміністратори з правом публікації."
        )
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
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT title, channel_id
            FROM channels
            WHERE user_id=?
            ORDER BY title
            """,
            (message.from_user.id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        await message.answer(
            "У вас ще немає каналів.\n\n"
            "Натисніть «➕ Додати канал» і виконайте інструкцію."
        )
        return

    text = "📢 Ваші канали:\n\n"
    for title, channel_id in rows:
        text += f"• {title}\n  ID: {channel_id}\n\n"

    await message.answer(text)


@dp.message(F.text == "📝 Створити пост")
async def create_post(message: Message, state: FSMContext) -> None:
    if await channel_count(message.from_user.id) == 0:
        await message.answer("Спочатку додайте канал через «➕ Додати канал».")
        return

    await state.set_state(PostForm.choosing_channel)
    await message.answer(
        "Виберіть канал для публікації:",
        reply_markup=await user_channels_keyboard(message.from_user.id, "post_channel"),
    )


@dp.callback_query(PostForm.choosing_channel, F.data.startswith("post_channel:"))
async def choose_post_channel(callback: CallbackQuery, state: FSMContext) -> None:
    channel_id = int(callback.data.split(":", 1)[1])

    await state.update_data(channel_id=channel_id)
    await state.set_state(PostForm.writing_text)
    await callback.message.answer("Напишіть текст поста.")
    await callback.answer()


@dp.message(PostForm.writing_text)
async def write_post_text(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Поки що ця версія приймає тільки текстові пости.")
        return

    now = datetime.now(timezone)
    await state.update_data(text=message.text)
    await state.set_state(PostForm.choosing_date)
    await message.answer(
        f"Виберіть дату публікації.\nЧасовий пояс: {TIMEZONE_NAME}",
        reply_markup=month_keyboard(now.year, now.month),
    )


@dp.callback_query(PostForm.choosing_date, F.data == "cal:ignore")
async def ignore_calendar_button(callback: CallbackQuery) -> None:
    await callback.answer()


@dp.callback_query(PostForm.choosing_date, F.data.startswith("cal:month:"))
async def change_calendar_month(callback: CallbackQuery) -> None:
    _, _, year, month = callback.data.split(":")
    await callback.message.edit_reply_markup(reply_markup=month_keyboard(int(year), int(month)))
    await callback.answer()


@dp.callback_query(PostForm.choosing_date, F.data.startswith("cal:day:"))
async def choose_post_date(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, year, month, day = callback.data.split(":")
    selected_date = date(int(year), int(month), int(day))

    await state.update_data(publish_date=selected_date.isoformat())
    await state.set_state(PostForm.choosing_hour)
    await callback.message.answer(
        f"Дата: {selected_date.strftime('%d.%m.%Y')}\nВиберіть годину:",
        reply_markup=hour_keyboard(),
    )
    await callback.answer()


@dp.callback_query(PostForm.choosing_hour, F.data.startswith("hour:"))
async def choose_post_hour(callback: CallbackQuery, state: FSMContext) -> None:
    hour = int(callback.data.split(":", 1)[1])

    await state.update_data(publish_hour=hour)
    await state.set_state(PostForm.choosing_minute)
    await callback.message.answer(
        f"Година: {hour:02d}:00\nВиберіть хвилини:",
        reply_markup=minute_keyboard(),
    )
    await callback.answer()


@dp.callback_query(PostForm.choosing_minute, F.data.startswith("minute:"))
async def choose_post_minute(callback: CallbackQuery, state: FSMContext) -> None:
    minute = int(callback.data.split(":", 1)[1])
    data = await state.get_data()

    publish_at_dt = datetime.fromisoformat(data["publish_date"]).replace(
        hour=data["publish_hour"],
        minute=minute,
        second=0,
        tzinfo=timezone,
    )

    if publish_at_dt <= datetime.now(timezone):
        await state.set_state(PostForm.choosing_date)
        now = datetime.now(timezone)
        await callback.message.answer(
            "Цей час уже минув. Виберіть іншу дату й час:",
            reply_markup=month_keyboard(now.year, now.month),
        )
        await callback.answer()
        return

    publish_at = publish_at_dt.strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO posts(user_id, channel_id, text, publish_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                callback.from_user.id,
                data["channel_id"],
                data["text"],
                publish_at,
            ),
        )
        await db.commit()

    await state.clear()
    await callback.message.answer(
        f"✅ Пост додано в чергу.\n\nЧас публікації: {publish_at}",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@dp.message(F.text == "📋 Черга")
async def queue(message: Message) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT posts.id, channels.title, posts.publish_at, posts.text
            FROM posts
            JOIN channels
                ON channels.user_id = posts.user_id
               AND channels.channel_id = posts.channel_id
            WHERE posts.user_id=? AND posts.status='pending'
            ORDER BY posts.publish_at ASC
            LIMIT 10
            """,
            (message.from_user.id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("Черга порожня.")
        return

    text = "📋 Найближчі пости:\n\n"
    buttons = []

    for post_id, title, publish_at, post_text in rows:
        preview = post_text.replace("\n", " ")[:80]
        text += f"#{post_id} • {title}\n{publish_at}\n{preview}\n\n"
        buttons.append([InlineKeyboardButton(text=f"Скасувати #{post_id}", callback_data=f"cancel:{post_id}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_post(callback: CallbackQuery) -> None:
    post_id = int(callback.data.split(":", 1)[1])

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            UPDATE posts
            SET status='cancelled'
            WHERE id=? AND user_id=? AND status='pending'
            """,
            (post_id, callback.from_user.id),
        )
        await db.commit()

    if cursor.rowcount == 0:
        await callback.answer("Пост не знайдено або вже не в черзі.", show_alert=True)
        return

    await callback.message.answer(f"🗑 Пост #{post_id} скасовано.")
    await callback.answer()


@dp.message()
async def fallback(message: Message) -> None:
    await message.answer(
        "Я не впізнав команду. Скористайтеся кнопками меню.",
        reply_markup=main_keyboard(),
    )


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
