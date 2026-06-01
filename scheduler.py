import asyncio
import aiosqlite
from datetime import datetime


DB_NAME = "bot.db"


async def scheduler(bot):

    while True:

        async with aiosqlite.connect(DB_NAME) as db:

            cursor = await db.execute("""
            SELECT id, channel_id, text
            FROM posts
            WHERE status='pending'
            AND publish_at <= ?
            """, (datetime.now().isoformat(),))

            posts = await cursor.fetchall()

            for post_id, channel_id, text in posts:

                try:

                    await bot.send_message(
                        chat_id=channel_id,
                        text=text
                    )

                    await db.execute("""
                    UPDATE posts
                    SET status='published'
                    WHERE id=?
                    """, (post_id,))

                except Exception:
                    pass

            await db.commit()

        await asyncio.sleep(30)
