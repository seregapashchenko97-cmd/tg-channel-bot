import aiosqlite


DB_NAME = "bot.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            title TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            text TEXT,
            publish_at TEXT,
            status TEXT DEFAULT 'pending'
        )
        """)

        await db.commit()
