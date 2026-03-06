from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass(slots=True)
class Post:
    post_id: str
    title: str
    url: str


@dataclass(slots=True)
class PostContext:
    post: Post
    content_summary: str
    hot_comments_summary: str


@dataclass(slots=True)
class CommentRecord:
    platform: str
    keyword: str
    post_id: str
    url: str
    title: str
    title_hash: str
    comment_text: str
    created_at: datetime = field(default_factory=datetime.now)
