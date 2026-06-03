import asyncio
import logging
import os
from datetime import datetime
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
    choosing_time = State()


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
            [
                KeyboardButton(text="📣 Реклама"),
                KeyboardButton(text="❤️ Підтримати"),
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


def parse_publish_time(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            publish_at = datetime.strptime(value, fmt).replace(tzinfo=timezone)
        except ValueError:
            continue

        if publish_at <= datetime.now(timezone):
            return None

        return publish_at.strftime("%Y-%m-%d %H:%M:%S")

    return None


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
            SELECT title, channel_id, promo_enabled
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
    for title, channel_id, promo_enabled in rows:
        promo = "увімкнено" if promo_enabled else "вимкнено"
        text += f"• {title}\n  ID: {channel_id}\n  Рекламний підпис: {promo}\n\n"

    await message.answer(text)


@dp.message(F.text == "📣 Реклама")
async def promo_settings(message: Message) -> None:
    if await channel_count(message.from_user.id) == 0:
        await message.answer("Спочатку додайте хоча б один канал.")
        return

    await message.answer(
        "Рекламний підпис додається тільки до постів у ваших підключених каналах.\n"
        "Виберіть канал, щоб увімкнути або вимкнути підпис:",
        reply_markup=await user_channels_keyboard(message.from_user.id, "promo"),
    )


@dp.callback_query(F.data.startswith("promo:"))
async def toggle_promo(callback: CallbackQuery) -> None:
    channel_id = int(callback.data.split(":", 1)[1])

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            UPDATE channels
            SET promo_enabled = CASE promo_enabled WHEN 1 THEN 0 ELSE 1 END
            WHERE user_id=? AND channel_id=?
            """,
            (callback.from_user.id, channel_id),
        )
        await db.commit()

        cursor = await db.execute(
            """
            SELECT title, promo_enabled
            FROM channels
            WHERE user_id=? AND channel_id=?
            """,
            (callback.from_user.id, channel_id),
        )
        row = await cursor.fetchone()

    if not row:
        await callback.answer("Канал не знайдено.", show_alert=True)
        return

    status = "увімкнено" if row[1] else "вимкнено"
    await callback.message.answer(f"📣 {row[0]}: рекламний підпис {status}.")
    await callback.answer()


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

    await state.update_data(text=message.text)
    await state.set_state(PostForm.choosing_time)
    await message.answer(
        "Коли опублікувати?\n\n"
        "Формат: 03.06.2026 18:30\n"
        "Також можна: 2026-06-03 18:30\n\n"
        f"Часовий пояс: {TIMEZONE_NAME}"
    )


@dp.message(PostForm.choosing_time)
async def choose_post_time(message: Message, state: FSMContext) -> None:
    publish_at = parse_publish_time(message.text or "")

    if not publish_at:
        await message.answer(
            "Не вдалося прочитати дату або час уже минув.\n"
            "Спробуйте так: 03.06.2026 18:30"
        )
        return

    data = await state.get_data()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO posts(user_id, channel_id, text, publish_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                message.from_user.id,
                data["channel_id"],
                data["text"],
                publish_at,
            ),
        )
        await db.commit()

    await state.clear()
    await message.answer(
        f"✅ Пост додано в чергу.\n\nЧас публікації: {publish_at}",
        reply_markup=main_keyboard(),
    )


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


@dp.message(F.text == "❤️ Підтримати")
async def donate(message: Message) -> None:
    await message.answer(
        "❤️ Дякуємо за підтримку проєкту!\n\n"
        "Пізніше тут можна додати Monobank банку або Patreon."
    )


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
