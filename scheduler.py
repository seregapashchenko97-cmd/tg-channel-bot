import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot


PROMO_FOOTER = "\n\n—\nАвтопостинг через PostPilot UA"


async def scheduler(
    bot: Bot,
    db_name: str = "bot.db",
    timezone_name: str = "Europe/Kyiv",
    interval_seconds: int = 15,
) -> None:
    timezone = ZoneInfo(timezone_name)

    while True:
        try:
            await publish_due_posts(bot, db_name, timezone)
        except Exception:
            logging.exception("Scheduler error")

        await asyncio.sleep(interval_seconds)


async def publish_due_posts(bot: Bot, db_name: str, timezone: ZoneInfo) -> None:
    now = datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(db_name) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT
                posts.id,
                posts.channel_id,
                posts.text,
                channels.promo_enabled
            FROM posts
            JOIN channels
                ON channels.user_id = posts.user_id
               AND channels.channel_id = posts.channel_id
            WHERE posts.status='pending'
              AND posts.publish_at <= ?
            ORDER BY posts.publish_at ASC
            LIMIT 20
            """,
            (now,),
        )

        posts = await cursor.fetchall()

    for post in posts:
        text = post["text"]

        if post["promo_enabled"]:
            text += PROMO_FOOTER

        try:
            await bot.send_message(
                chat_id=post["channel_id"],
                text=text
            )
        except Exception as error:
            await mark_failed(db_name, post["id"], str(error))
        else:
            await mark_sent(db_name, post["id"])


async def mark_sent(db_name: str, post_id: int) -> None:
    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            UPDATE posts
            SET status='sent',
                sent_at=CURRENT_TIMESTAMP,
                error=NULL
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
            SET status='failed',
                error=?
            WHERE id=?
            """,
            (error[:500], post_id),
        )
        await db.commit()
