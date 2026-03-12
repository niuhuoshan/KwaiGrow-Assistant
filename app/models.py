from dataclasses import dataclass
from typing import List


@dataclass
class PostItem:
    post_id: str
    url: str
    title: str
    author_id: str = ""
    keyword: str = ""


@dataclass
class PostContext:
    post_text: str
    hot_comments: List[str]
