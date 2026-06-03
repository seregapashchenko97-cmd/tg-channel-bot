import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot


async def scheduler(
    bot: Bot,
    db_name: str,
    timezone_name: str = "Europe/Kyiv",
    interval_seconds: int = 15,
) -> None:
    timezone = ZoneInfo(timezone_name)

    while True:
        try:
            await publish_due_posts(bot, db_name, timezone)
        except Exception:
            logging.exception("Scheduler loop failed")

        await asyncio.sleep(interval_seconds)


async def publish_due_posts(bot: Bot, db_name: str, timezone: ZoneInfo) -> None:
    now = datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(db_name) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, channel_id, text
            FROM posts
            WHERE status='pending'
              AND publish_at <= ?
            ORDER BY publish_at ASC
            LIMIT 20
            """,
            (now,),
        )
        posts = await cursor.fetchall()

    for post in posts:
        try:
            await bot.send_message(chat_id=post["channel_id"], text=post["text"])
        except Exception as exc:
            logging.exception("Failed to publish post %s", post["id"])
            await mark_failed(db_name, post["id"], str(exc)[:500])
        else:
            await mark_sent(db_name, post["id"])


async def mark_sent(db_name: str, post_id: int) -> None:
    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            UPDATE posts
            SET status='sent', sent_at=CURRENT_TIMESTAMP, error=NULL
            WHERE id=?
            """,
            (post_id,),
        )
        await db.commit()


async def mark_failed(db_name: str, post_id: int, error: str) -> None:
    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            UPDATE posts
            SET status='failed', error=?
            WHERE id=?
            """,
            (error, post_id),
        )
        await db.commit()
