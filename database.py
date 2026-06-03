import aiosqlite


CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_CHANNELS = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    username TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, channel_id)
);
"""

CREATE_POSTS = """
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
);
"""


async def init_db(db_name: str) -> None:
    async with aiosqlite.connect(db_name) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute(CREATE_USERS)
        await db.execute(CREATE_CHANNELS)
        await db.execute(CREATE_POSTS)
        await db.commit()


async def upsert_user(db_name: str, user) -> None:
    async with aiosqlite.connect(db_name) as db:
        await db.execute(
            """
            INSERT INTO users(user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (user.id, user.username, user.first_name),
        )
        await db.commit()
