import aiosqlite


async def init_db(db_name: str = "bot.db") -> None:
    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                username TEXT,
                promo_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, channel_id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                publish_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT
            )
            """
        )

        await db.commit()
