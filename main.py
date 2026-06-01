import os
import asyncio
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

from database import init_db
from scheduler import scheduler


BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_NAME = "bot.db"


@dp.message(CommandStart())
async def start(message: Message):

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📢 Мої канали"),
                KeyboardButton(text="📝 Створити пост")
            ],
            [
                KeyboardButton(text="📋 Черга"),
                KeyboardButton(text="❤️ Підтримати")
            ]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "🚀 PostPilot UA\n\n"
        "Автопостинг для Telegram-каналів",
        reply_markup=keyboard
    )


@dp.message(F.text == "📢 Мої канали")
async def my_channels(message: Message):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            """
            SELECT title
            FROM channels
            WHERE user_id=?
            """,
            (message.from_user.id,)
        )

        rows = await cursor.fetchall()

    if not rows:
        await message.answer(
            "У вас ще немає каналів.\n\n"
            "Перешліть будь-яке повідомлення зі свого каналу."
        )
        return

    text = "📢 Ваші канали:\n\n"

    for row in rows:
        text += f"• {row[0]}\n"

    await message.answer(text)


@dp.message(F.text == "❤️ Підтримати")
async def donate(message: Message):

    await message.answer(
        "❤️ Дякуємо за підтримку проекту\n\n"
        "Пізніше тут буде Monobank банка."
    )


@dp.message(F.forward_from_chat)
async def add_channel(message: Message):

    chat = message.forward_from_chat

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            """
            INSERT INTO channels(
                user_id,
                channel_id,
                title
            )
            VALUES (?, ?, ?)
            """,
            (
                message.from_user.id,
                chat.id,
                chat.title
            )
        )

        await db.commit()

    await message.answer(
        f"✅ Канал додано:\n{chat.title}"
    )


async def main():

    await init_db()

    asyncio.create_task(
        scheduler(bot)
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
