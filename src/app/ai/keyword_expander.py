from __future__ import annotations

import logging
from typing import List

from .openai_client import OpenAIChatClient
from ..utils.text import normalize_spaces, safe_json_loads, to_clean_string_list


SYNONYM_MAP = {
    "美女": ["高颜值", "小姐姐", "气质美女", "氛围感美女"],
    "美食": ["探店", "家常菜", "美味分享", "下饭菜"],
    "旅行": ["旅游", "周末出游", "城市漫游", "风景打卡"],
    "健身": ["减脂", "增肌", "训练打卡", "居家锻炼"],
}


class KeywordExpander:
    def __init__(self, ai_client: OpenAIChatClient, max_count: int = 10):
        self._ai = ai_client
        self._max_count = max_count
        self._logger = logging.getLogger(self.__class__.__name__)

    def expand(self, direction: str, used_keywords: List[str] | None = None) -> List[str]:
        direction = normalize_spaces(direction)
        if not direction:
            return []

        if not self._ai.enabled:
            raise RuntimeError("AI 不可用，无法扩展关键词")

        used = {
            normalize_spaces(v)
            for v in (used_keywords or [])
            if normalize_spaces(v)
        }

        return self._expand_by_ai(direction, used)

    def _expand_by_ai(self, direction: str, used: set[str]) -> List[str]:
        system_prompt = (
            "你是短视频平台搜索词扩展助手。"
            "请严格输出 JSON 数组，只包含搜索词字符串，不要输出其它解释。"
        )
        used_text = "、".join(sorted(used)) if used else "无"
        user_prompt = (
            f"方向词：{direction}\n"
            f"历史已使用关键词（禁止重复推荐）：{used_text}\n"
            f"要求：扩展为最多 {self._max_count} 个可用于快手视频搜索的中文关键词；"
            "避免重复、空值、无意义词。"
        )

        raw = self._ai.chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.5, max_tokens=320)
        parsed = safe_json_loads(raw)
        words = to_clean_string_list(parsed, self._max_count * 2)
        if not words:
            raise RuntimeError("empty ai keyword expansion")

        cleaned: List[str] = []
        seen = set()
        for word in words:
            text = normalize_spaces(word)
            if not text or text in seen or text in used:
                continue
            if not self._looks_like_keyword(text):
                continue
            seen.add(text)
            cleaned.append(text)
            if len(cleaned) >= self._max_count:
                break

        if cleaned:
            return cleaned

        raise RuntimeError("no valid new keyword after filtering")

    @staticmethod
    def _looks_like_keyword(text: str) -> bool:
        if not text:
            return False
        if len(text) < 2 or len(text) > 16:
            return False
        if any(mark in text for mark in ["，", "。", "！", "？", "；", ",", ".", "!", "?", "#", "\n"]):
            return False
        if text.count(" ") > 1:
            return False
        return True

    def _expand_by_rules(self, direction: str, used: set[str]) -> List[str]:
        suffixes = ["推荐", "热门", "合集", "日常", "实拍", "同款", "高质量", "最新", "精选"]

        words: List[str] = [direction]
        words.extend(SYNONYM_MAP.get(direction, []))
        words.extend([f"{direction}{suffix}" for suffix in suffixes])
        words.extend([f"{direction} 教程", f"{direction} 分享", f"{direction} 现场"])

        cleaned: List[str] = []
        seen = set()
        for word in words:
            text = normalize_spaces(word)
            if not text or text in seen or text in used:
                continue
            seen.add(text)
            cleaned.append(text)
            if len(cleaned) >= self._max_count:
                break

        return cleaned
