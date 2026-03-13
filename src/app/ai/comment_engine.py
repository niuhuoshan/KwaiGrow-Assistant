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

    def is_commentable(
        self,
        post_summary: str,
        hot_comments_summary: str,
        requirements: List[str],
        style_prompt: str = "",
        content_prompt: str = "",
    ) -> Tuple[bool, str]:
        post_summary = normalize_spaces(post_summary)
        hot_comments_summary = normalize_spaces(hot_comments_summary)

        if not post_summary:
            return False, "empty post summary"

        if not self._ai.enabled:
            if self._allow_rule_fallback:
                return self._judge_by_rules(post_summary)
            raise RuntimeError("AI 不可用，无法进行评论判定")

        return self._judge_by_ai(post_summary, hot_comments_summary, requirements, style_prompt, content_prompt)

    def generate_candidates(
        self,
        post_summary: str,
        hot_comments_summary: str,
        requirements: List[str],
        style_prompt: str = "",
        content_prompt: str = "",
    ) -> List[str]:
        post_summary = normalize_spaces(post_summary)
        hot_comments_summary = normalize_spaces(hot_comments_summary)

        if not self._ai.enabled:
            if self._allow_rule_fallback:
                return self._generate_by_rules(post_summary, requirements, style_prompt, content_prompt)
            raise RuntimeError("AI 不可用，无法生成评论")

        return self._generate_by_ai(post_summary, hot_comments_summary, requirements, style_prompt, content_prompt)

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
                self._logger.debug("[AI] candidate skip empty/dup text=%.50s", candidate)
                continue
            seen.add(text)

            if len(text) < min_length or len(text) > max_length:
                self._logger.debug("[AI] candidate skip length=%s (min=%s max=%s) text=%.50s", len(text), min_length, max_length, text)
                continue

            lowered = text.lower()
            hit_word = next((word for word in banned if word and word in lowered), None)
            if hit_word:
                self._logger.debug("[AI] candidate skip banned_word=%s text=%.50s", hit_word, text)
                continue

            if is_repeated(text):
                self._logger.debug("[AI] candidate skip repeated text=%.50s", text)
                continue

            self._logger.debug("[AI] candidate accepted text=%.80s", text)
            return text

        return None

    def _compose_comment_brief(self, requirements: List[str], style_prompt: str = "", content_prompt: str = "") -> str:
        lines = []
        clean_requirements = [normalize_spaces(item) for item in requirements if normalize_spaces(item)]
        clean_style = normalize_spaces(style_prompt)
        clean_content = normalize_spaces(content_prompt)

        if clean_requirements:
            lines.append(f"基础要求：{'; '.join(clean_requirements)}")
        if clean_style:
            lines.append(f"风格要求：{clean_style}")
        if clean_content:
            lines.append(f"内容要求：{clean_content}")

        return "\n".join(lines) or "基础要求：自然、真诚、简短。"

    def _judge_by_ai(
        self,
        post_summary: str,
        hot_comments_summary: str,
        requirements: List[str],
        style_prompt: str = "",
        content_prompt: str = "",
    ) -> Tuple[bool, str]:
        comment_brief = self._compose_comment_brief(requirements, style_prompt, content_prompt)
        system_prompt = (
            "你是评论审核助手。"
            "根据帖子摘要与目标评论要求判断是否适合评论。"
            "严格输出 JSON 对象：{\"commentable\": true/false, \"reason\": \"...\"}。"
        )
        user_prompt = (
            f"帖子内容摘要：{post_summary}\n"
            f"热评摘要：{hot_comments_summary or '无'}\n"
            f"目标评论要求：\n{comment_brief}"
        )

        raw = self._ai.chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.2, max_tokens=180)
        self._logger.info("[AI] judge raw response=%.300s", raw)
        parsed = safe_json_loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("commentability json parse failed")

        commentable = bool(parsed.get("commentable"))
        reason = normalize_spaces(str(parsed.get("reason") or "")) or "ai no reason"
        self._logger.debug("[AI] judge result commentable=%s reason=%s", commentable, reason)
        return commentable, reason

    def _judge_by_rules(self, post_summary: str) -> Tuple[bool, str]:
        risk_words = []
        lowered = post_summary.lower()
        if any(word in lowered for word in risk_words):
            return False, "rule blocked by risk words"
        return True, "rule pass"

    def _generate_by_ai(
        self,
        post_summary: str,
        hot_comments_summary: str,
        requirements: List[str],
        style_prompt: str = "",
        content_prompt: str = "",
    ) -> List[str]:
        comment_brief = self._compose_comment_brief(requirements, style_prompt, content_prompt)
        system_prompt = (
            "你是短视频评论生成助手。"
            "当前步骤只负责生成评论候选文案，不负责发送动作。"
            "务必严格遵循评论要求，内容要自然、口语化。"
            "不要输出风格标签，不要解释。"
            "仅输出 JSON 数组，元素是评论字符串，数量 1~3。"
        )
        user_prompt = (
            f"帖子内容摘要：{post_summary}\n"
            f"热评摘要：{hot_comments_summary or '无'}\n"
            "请直接输出可用的评论候选，不要分析任务，不要解释原因。\n"
            f"{comment_brief}\n"
            f"输出评论候选数量：{self._candidate_count}"
        )
        self._logger.info("[AI] generate prompt=%.300s", user_prompt)
        raw = self._ai.chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.8, max_tokens=260)
        self._logger.info("[AI] generate raw response=%.300s", raw)
        parsed = safe_json_loads(raw)
        comments = to_clean_string_list(parsed, self._candidate_count)
        self._logger.debug("[AI] generate parsed comments=%s", comments)
        if not comments:
            raise RuntimeError("empty ai comments")
        return comments

    def _generate_by_rules(
        self,
        post_summary: str,
        requirements: List[str],
        style_prompt: str = "",
        content_prompt: str = "",
    ) -> List[str]:
        base = post_summary[:40] if post_summary else "这条内容"
        hint_parts = []
        if requirements:
            hint_parts.append(requirements[0])
        if normalize_spaces(style_prompt):
            hint_parts.append(f"风格偏向{normalize_spaces(style_prompt)}")
        if normalize_spaces(content_prompt):
            hint_parts.append(f"内容围绕{normalize_spaces(content_prompt)}")
        req_hint = "，".join(hint_parts[:2]) if hint_parts else "保持真诚自然"

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
