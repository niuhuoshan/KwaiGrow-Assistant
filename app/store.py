from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime
from typing import Optional

from app.models import PostItem


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL DEFAULT '',
                post_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                title_hash TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                commented INTEGER NOT NULL DEFAULT 0,
                commented_at TEXT,
                last_comment TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_post_id_unique
            ON posts(post_id)
            WHERE post_id != '';

            CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_post_url_unique
            ON posts(post_url)
            WHERE post_url != '';

            CREATE INDEX IF NOT EXISTS idx_posts_title_hash
            ON posts(title_hash);

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL DEFAULT '',
                post_url TEXT NOT NULL DEFAULT '',
                title_hash TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_comments_created_at
            ON comments(created_at DESC);

            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                post_id TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]

    def upsert_post_seen(self, post: PostItem, title_hash: str) -> None:
        now = datetime.utcnow().isoformat()
        row = self.conn.execute(
            "SELECT id FROM posts WHERE (post_id = ? AND post_id != '') OR (post_url = ? AND post_url != '') LIMIT 1",
            (post.post_id, post.url),
        ).fetchone()

        if row:
            self.conn.execute(
                "UPDATE posts SET title = ?, title_hash = ?, last_seen_at = ? WHERE id = ?",
                (post.title, title_hash, now, row["id"]),
            )
        else:
            self.conn.execute(
                "INSERT INTO posts (post_id, post_url, title, title_hash, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                (post.post_id, post.url, post.title, title_hash, now, now),
            )
        self.conn.commit()

    def is_post_commented(self, post_id: str, post_url: str, title_hash: str, skip_same_title_hash: bool) -> bool:
        clauses = ["(post_id = ? AND post_id != '')", "(post_url = ? AND post_url != '')"]
        params = [post_id, post_url]
        if skip_same_title_hash and title_hash:
            clauses.append("(title_hash = ? AND title_hash != '')")
            params.append(title_hash)

        where_clause = " OR ".join(clauses)
        row = self.conn.execute(
            f"SELECT 1 FROM posts WHERE commented = 1 AND ({where_clause}) LIMIT 1",
            tuple(params),
        ).fetchone()
        return row is not None

    def mark_commented(self, post: PostItem, title_hash: str, comment_text: str) -> None:
        now = datetime.utcnow().isoformat()
        row = self.conn.execute(
            "SELECT id FROM posts WHERE (post_id = ? AND post_id != '') OR (post_url = ? AND post_url != '') LIMIT 1",
            (post.post_id, post.url),
        ).fetchone()

        if row:
            self.conn.execute(
                "UPDATE posts SET commented = 1, commented_at = ?, last_comment = ?, title_hash = ?, title = ?, last_seen_at = ? WHERE id = ?",
                (now, comment_text, title_hash, post.title, now, row["id"]),
            )
        else:
            self.conn.execute(
                "INSERT INTO posts (post_id, post_url, title, title_hash, first_seen_at, last_seen_at, commented, commented_at, last_comment) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (post.post_id, post.url, post.title, title_hash, now, now, now, comment_text),
            )

        self.conn.execute(
            "INSERT INTO comments (post_id, post_url, title_hash, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (post.post_id, post.url, title_hash, comment_text, now),
        )
        self.conn.commit()

    def is_recent_comment_duplicate(self, content: str, window: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM comments WHERE content = ? ORDER BY id DESC LIMIT ?",
            (content, max(1, window)),
        ).fetchone()
        return row is not None

    def daily_comment_count(self, for_date: Optional[date] = None) -> int:
        day = (for_date or date.today()).isoformat()
        row = self.conn.execute(
            "SELECT COUNT(1) AS cnt FROM comments WHERE substr(created_at, 1, 10) = ?",
            (day,),
        ).fetchone()
        return int(row["cnt"] if row else 0)

    def log_action(self, stage: str, status: str, detail: str, post_id: str = "") -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "INSERT INTO actions (stage, status, post_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (stage, status, post_id, detail, now),
        )
        self.conn.commit()
