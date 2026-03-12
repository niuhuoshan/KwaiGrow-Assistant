from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import DedupConfig
from ..schema import CommentRecord


class DedupStore:
    def __init__(self, cfg: DedupConfig):
        self._cfg = cfg
        self._logger = logging.getLogger(self.__class__.__name__)
        db_path = Path(cfg.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                date TEXT NOT NULL,
                platform TEXT NOT NULL,
                keyword TEXT NOT NULL,
                post_id TEXT,
                url TEXT,
                title_hash TEXT,
                comment_text TEXT NOT NULL,
                payload_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_comments_date ON comments(date);
            CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
            CREATE INDEX IF NOT EXISTS idx_comments_url ON comments(url);
            CREATE INDEX IF NOT EXISTS idx_comments_title_hash ON comments(title_hash);
            CREATE INDEX IF NOT EXISTS idx_comments_text ON comments(comment_text);

            CREATE TABLE IF NOT EXISTS keyword_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                keyword TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(topic, keyword)
            );

            CREATE INDEX IF NOT EXISTS idx_keyword_history_topic_created
            ON keyword_history(topic, created_at DESC);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def has_commented(self, post_id: str, url: str, title_hash: str) -> bool:
        cur = self._conn.cursor()

        if self._cfg.by_post_id and post_id:
            row = cur.execute("SELECT 1 FROM comments WHERE post_id = ? LIMIT 1", (post_id,)).fetchone()
            if row:
                self._logger.debug("[DEDUP] hit by_post_id post_id=%s", post_id)
                return True

        if self._cfg.by_url and url:
            row = cur.execute("SELECT 1 FROM comments WHERE url = ? LIMIT 1", (url,)).fetchone()
            if row:
                self._logger.debug("[DEDUP] hit by_url url=%s", url)
                return True

        is_card_post = (post_id or "").startswith("kscard:")
        if self._cfg.by_title_hash and title_hash and (not is_card_post):
            row = cur.execute("SELECT 1 FROM comments WHERE title_hash = ? LIMIT 1", (title_hash,)).fetchone()
            if row:
                self._logger.debug("[DEDUP] hit by_title_hash title_hash=%s", title_hash)
                return True

        return False

    def comment_text_exists(self, comment_text: str, days: int = 14) -> bool:
        if not self._cfg.avoid_repeated_comment_text:
            return False

        cur = self._conn.cursor()
        since = datetime.now().date().toordinal() - max(0, days)
        date_floor = datetime.fromordinal(since).date().isoformat()
        row = cur.execute(
            "SELECT 1 FROM comments WHERE comment_text = ? AND date >= ? LIMIT 1",
            (comment_text, date_floor),
        ).fetchone()
        return bool(row)

    def count_today(self) -> int:
        today = datetime.now().date().isoformat()
        cur = self._conn.cursor()
        row = cur.execute("SELECT COUNT(1) AS cnt FROM comments WHERE date = ?", (today,)).fetchone()
        return int(row["cnt"] if row else 0)

    def get_used_keywords_for_topic(self, topic: str, limit: int = 200) -> list[str]:
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT keyword FROM keyword_history WHERE topic = ? ORDER BY id DESC LIMIT ?",
            (topic, max(1, limit)),
        ).fetchall()
        return [str(r["keyword"]) for r in rows if r and r["keyword"]]

    def add_used_keyword(self, topic: str, keyword: str) -> None:
        text_topic = (topic or "").strip()
        text_keyword = (keyword or "").strip()
        if not text_topic or not text_keyword:
            return

        created_at = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO keyword_history (topic, keyword, created_at) VALUES (?, ?, ?)",
            (text_topic, text_keyword, created_at),
        )
        self._conn.commit()

    def insert_comment(self, record: CommentRecord) -> None:
        cur = self._conn.cursor()
        created_at = record.created_at.isoformat(timespec="seconds")
        date = record.created_at.date().isoformat()

        payload = {
            "platform": record.platform,
            "keyword": record.keyword,
            "post_id": record.post_id,
            "url": record.url,
            "title": record.title,
            "title_hash": record.title_hash,
            "comment_text": record.comment_text,
        }

        cur.execute(
            """
            INSERT INTO comments (
                created_at, date, platform, keyword, post_id, url, title_hash, comment_text, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                date,
                record.platform,
                record.keyword,
                record.post_id,
                record.url,
                record.title_hash,
                record.comment_text,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self._conn.commit()
