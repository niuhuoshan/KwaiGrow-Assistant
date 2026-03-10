from __future__ import annotations

import logging
from typing import Callable, List, Tuple

from .openai_client import OpenAIChatClient
from ..utils.text import normalize_spaces, safe_json_loads, to_clean_string_list


class CommentEngine:
    def __init__(self, ai_client: OpenAIChatClient, candidate_count: int = 3, allow_rule_fallback: bool = False):
        self._ai = ai_client
        self._candidate_count = max(1, min(3, candidate_count))
        self._allow_rule_fallback = allow_rule_fallback
        self._logger = logging.getLogger(self.__class__.__name__)

    def is_commentable(self, post_summary: str, hot_comments_summary: str, requirements: List[str]) -> Tuple[bool, str]:
        post_summary = normalize_spaces(post_summary)
        hot_comments_summary = normalize_spaces(hot_comments_summary)

        if not post_summary:
            return False, "empty post summary"

        if not self._ai.enabled:
            if self._allow_rule_fallback:
                return self._judge_by_rules(post_summary)
            raise RuntimeError("AI 不可用，无法进行评论判定")

        return self._judge_by_ai(post_summary, hot_comments_summary, requirements)

    def generate_candidates(self, post_summary: str, hot_comments_summary: str, requirements: List[str]) -> List[str]:
        post_summary = normalize_spaces(post_summary)
        hot_comments_summary = normalize_spaces(hot_comments_summary)

        if not self._ai.enabled:
            if self._allow_rule_fallback:
                return self._generate_by_rules(post_summary, requirements)
            raise RuntimeError("AI 不可用，无法生成评论")

        return self._generate_by_ai(post_summary, hot_comments_summary, requirements)

    def pick_valid_comment(
        self,
        candidates: List[str],
        banned_words: List[str],
        min_length: int,
        max_length: int,
        is_repeated: Callable[[str], bool],
    ) -> str | None:
        seen = set()
        banned = [normalize_spaces(word).lower() for word in banned_words if normalize_spaces(word)]

        for candidate in candidates:
            text = normalize_spaces(candidate)
            if not text or text in seen:
                continue
            seen.add(text)

            if len(text) < min_length or len(text) > max_length:
                continue

            lowered = text.lower()
            if any(word and word in lowered for word in banned):
                continue

            if is_repeated(text):
                continue

            return text

        return None

    def _judge_by_ai(self, post_summary: str, hot_comments_summary: str, requirements: List[str]) -> Tuple[bool, str]:
        system_prompt = (
            "你是评论审核助手。"
            "根据帖子摘要与评论要求判断是否适合评论。"
            "严格输出 JSON 对象：{\"commentable\": true/false, \"reason\": \"...\"}。"
        )
        user_prompt = (
            f"帖子内容摘要：{post_summary}\n"
            f"热评摘要：{hot_comments_summary or '无'}\n"
            f"评论要求：{'; '.join(requirements)}"
        )

        raw = self._ai.chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.2, max_tokens=180)
        parsed = safe_json_loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("commentability json parse failed")

        commentable = bool(parsed.get("commentable"))
        reason = normalize_spaces(str(parsed.get("reason") or "")) or "ai no reason"
        return commentable, reason

    def _judge_by_rules(self, post_summary: str) -> Tuple[bool, str]:
        risk_words = ["加微信", "私信", "引流", "返利", "代刷", "博彩"]
        lowered = post_summary.lower()
        if any(word in lowered for word in risk_words):
            return False, "rule blocked by risk words"
        return True, "rule pass"

    def _generate_by_ai(self, post_summary: str, hot_comments_summary: str, requirements: List[str]) -> List[str]:
        system_prompt = (
            "你是短视频评论生成助手。"
            "务必严格遵循用户评论要求，不要输出风格标签，不要解释。"
            "仅输出 JSON 数组，元素是评论字符串，数量 1~3。"
        )
        user_prompt = (
            f"帖子内容摘要：{post_summary}\n"
            f"热评摘要：{hot_comments_summary or '无'}\n"
            f"评论要求（必须严格满足）：{'; '.join(requirements)}\n"
            f"输出评论候选数量：{self._candidate_count}"
        )

        raw = self._ai.chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.8, max_tokens=260)
        parsed = safe_json_loads(raw)
        comments = to_clean_string_list(parsed, self._candidate_count)
        if not comments:
            raise RuntimeError("empty ai comments")
        return comments

    def _generate_by_rules(self, post_summary: str, requirements: List[str]) -> List[str]:
        base = post_summary[:40] if post_summary else "这条内容"
        req_hint = requirements[0] if requirements else "保持真诚自然"

        templates = [
            f"{base}，这个点讲得很清楚，{req_hint}。",
            f"看完有收获，尤其是这个细节，{req_hint}。",
            f"内容挺实在，节奏也舒服，{req_hint}。",
        ]

        cleaned = []
        seen = set()
        for text in templates:
            v = normalize_spaces(text)
            if not v or v in seen:
                continue
            seen.add(v)
            cleaned.append(v)
            if len(cleaned) >= self._candidate_count:
                break

        return cleaned
