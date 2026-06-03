import asyncio
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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


def build_post_keyboard(buttons_json: str | None) -> InlineKeyboardMarkup | None:
    if not buttons_json:
        return None

    try:
        rows = json.loads(buttons_json)
    except json.JSONDecodeError:
        return None

    keyboard = []
    for row in rows:
        keyboard_row = []
        for button in row:
            keyboard_row.append(InlineKeyboardButton(text=button["text"], url=button["url"]))
        keyboard.append(keyboard_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


async def publish_due_posts(bot: Bot, db_name: str, timezone: ZoneInfo) -> None:
    now = datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(db_name) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                id,
                channel_id,
                text,
                media_type,
                media_file_id,
                parse_mode,
                buttons_json,
                publish_at,
                repeat_type
            FROM posts
            WHERE status='pending'
              AND publish_at <= ?
            ORDER BY publish_at ASC
            LIMIT 30
            """,
            (now,),
        )
        posts = await cursor.fetchall()

    for post in posts:
        try:
            await send_post(bot, post)
        except Exception as exc:
            logging.exception("Failed to publish post %s", post["id"])
            await mark_failed(db_name, post["id"], str(exc)[:500])
        else:
            await mark_published(db_name, post, timezone)


async def send_post(bot: Bot, post: aiosqlite.Row) -> None:
    reply_markup = build_post_keyboard(post["buttons_json"])
    parse_mode = post["parse_mode"] or None

    if post["media_type"] == "photo":
        await bot.send_photo(
            chat_id=post["channel_id"],
            photo=post["media_file_id"],
            caption=post["text"] or None,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return

    if post["media_type"] == "video":
        await bot.send_video(
            chat_id=post["channel_id"],
            video=post["media_file_id"],
            caption=post["text"] or None,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return

    await bot.send_message(
        chat_id=post["channel_id"],
        text=post["text"],
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


async def mark_published(db_name: str, post: aiosqlite.Row, timezone: ZoneInfo) -> None:
    if post["repeat_type"] == "daily":
        await reschedule(db_name, post["id"], post["publish_at"], timezone, days=1)
        return

    if post["repeat_type"] == "weekly":
        await reschedule(db_name, post["id"], post["publish_at"], timezone, days=7)
        return

    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            UPDATE posts
            SET status='sent', sent_at=CURRENT_TIMESTAMP, error=NULL
            WHERE id=?
            """,
            (post["id"],),
        )
        await db.commit()


async def reschedule(
    db_name: str,
    post_id: int,
    publish_at: str,
    timezone: ZoneInfo,
    days: int,
) -> None:
    current_time = datetime.strptime(publish_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone)
    next_time = current_time + timedelta(days=days)
    now = datetime.now(timezone)

    while next_time <= now:
        next_time += timedelta(days=days)

    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            UPDATE posts
            SET publish_at=?, status='pending', sent_at=CURRENT_TIMESTAMP, error=NULL
            WHERE id=?
            """,
            (next_time.strftime("%Y-%m-%d %H:%M:%S"), post_id),
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
