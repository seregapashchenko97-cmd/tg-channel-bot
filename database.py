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
    group_id TEXT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT 'text',
    media_file_id TEXT,
    parse_mode TEXT,
    buttons_json TEXT,
    publish_at TEXT NOT NULL,
    repeat_type TEXT NOT NULL DEFAULT 'none',
    repeat_weekday INTEGER,
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
        await migrate_posts(db)
        await db.commit()


async def migrate_posts(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(posts)")
    columns = {row[1] for row in await cursor.fetchall()}

    migrations = {
        "group_id": "ALTER TABLE posts ADD COLUMN group_id TEXT",
        "media_type": "ALTER TABLE posts ADD COLUMN media_type TEXT NOT NULL DEFAULT 'text'",
        "media_file_id": "ALTER TABLE posts ADD COLUMN media_file_id TEXT",
        "parse_mode": "ALTER TABLE posts ADD COLUMN parse_mode TEXT",
        "buttons_json": "ALTER TABLE posts ADD COLUMN buttons_json TEXT",
        "repeat_type": "ALTER TABLE posts ADD COLUMN repeat_type TEXT NOT NULL DEFAULT 'none'",
        "repeat_weekday": "ALTER TABLE posts ADD COLUMN repeat_weekday INTEGER",
    }

    for column, sql in migrations.items():
        if column not in columns:
            await db.execute(sql)

    await db.execute("UPDATE posts SET group_id = CAST(id AS TEXT) WHERE group_id IS NULL")
    await db.execute("UPDATE posts SET media_type = 'text' WHERE media_type IS NULL")
    await db.execute("UPDATE posts SET repeat_type = 'none' WHERE repeat_type IS NULL")


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
