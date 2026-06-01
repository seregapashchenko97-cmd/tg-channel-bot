import os
import asyncio
import aiosqlite

from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

from database import init_db
from scheduler import scheduler


BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

DB_NAME = "bot.db"


@dp.message(CommandStart())
async def start(message: Message):

    await message.answer(
        "🚀 Бот автопостинга\n\n"
        "Команды:\n"
        "/addchannel\n"
        "/channels\n"
        "/newpost"
    )


@dp.message(F.forward_from_chat)
async def add_channel(message: Message):

    if not message.forward_from_chat:
        return

    chat = message.forward_from_chat

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        INSERT INTO channels(
            user_id,
            channel_id,
            title
        )
        VALUES (?, ?, ?)
        """, (
            message.from_user.id,
            chat.id,
            chat.title
        ))

        await db.commit()

    await message.answer(
        f"✅ Канал добавлен:\n{chat.title}"
    )


@dp.message(F.text.startswith("/channels"))
async def channels(message: Message):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
        SELECT title
        FROM channels
        WHERE user_id=?
        """, (message.from_user.id,))

        rows = await cursor.fetchall()

    if not rows:
        await message.answer("Нет каналов")
        return

    text = "📢 Ваши каналы:\n\n"

    for row in rows:
        text += f"• {row[0]}\n"

    await message.answer(text)


@dp.message(F.text.startswith("/newpost"))
async def create_post(message: Message):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute("""
        SELECT channel_id
        FROM channels
        WHERE user_id=?
        LIMIT 1
        """, (message.from_user.id,))

        row = await cursor.fetchone()

    if not row:
        await message.answer(
            "Сначала добавьте канал"
        )
        return

    channel_id = row[0]

    publish_time = datetime.now().isoformat()

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        INSERT INTO posts(
            user_id,
            channel_id,
            text,
            publish_at
        )
        VALUES (?, ?, ?, ?)
        """, (
            message.from_user.id,
            channel_id,
            "Тестовый пост",
            publish_time
        ))

        await db.commit()

    await message.answer(
        "✅ Тестовый пост создан"
    )


async def main():

    await init_db()

    asyncio.create_task(
        scheduler(bot)
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
