from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from openai import OpenAI

from app.config import ModelConfig


class OpenAICompatClient:
    def __init__(self, cfg: ModelConfig) -> None:
        self.client = OpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            timeout=cfg.timeout_seconds,
        )
        self.model_id = cfg.model_id
        self.temperature = cfg.temperature
        self.max_tokens = cfg.max_tokens

    def expand_keywords(self, seed: str, max_count: int) -> List[str]:
        prompt = (
            "你是短视频平台运营助手。"
            "请基于用户给定方向词，扩展出最多10个搜索关键词。"
            "要求：中文、贴近快手内容检索、避免重复、不要输出解释。"
            "输出JSON数组，例如：[\"词1\",\"词2\"]。"
            f"\n方向词: {seed}"
        )
        try:
            content = self._chat(prompt)
            data = self._extract_json(content)
            if isinstance(data, list):
                return self._clean_keywords(data, max_count)
            if isinstance(data, dict) and isinstance(data.get("keywords"), list):
                return self._clean_keywords(data["keywords"], max_count)
        except Exception:
            pass
        return self._fallback_expand(seed, max_count)

    def should_comment(self, post_text: str, hot_comments: List[str], requirement: str) -> Dict[str, Any]:
        comment_text = "；".join(hot_comments[:5])
        prompt = (
            "你是评论审核助手。"
            "根据帖子内容与评论要求，判断是否值得评论。"
            "严格输出JSON对象，字段: should_comment(boolean), reason(string), "
            "post_summary(string), hot_comment_summary(string)。"
            "如果信息不足，should_comment=false。"
            f"\n评论要求: {requirement}"
            f"\n帖子内容: {post_text}"
            f"\n热评摘要: {comment_text}"
        )
        try:
            content = self._chat(prompt)
            data = self._extract_json(content)
            if not isinstance(data, dict):
                raise ValueError("invalid gate response")
            return {
                "should_comment": bool(data.get("should_comment")),
                "reason": str(data.get("reason", ""))[:200],
                "post_summary": str(data.get("post_summary", post_text))[:300],
                "hot_comment_summary": str(data.get("hot_comment_summary", comment_text))[:300],
            }
        except Exception as exc:
            return {
                "should_comment": True,
                "reason": f"模型调用失败，降级继续: {exc}",
                "post_summary": post_text[:300],
                "hot_comment_summary": comment_text[:300],
            }

    def generate_comments(
        self,
        post_summary: str,
        hot_comment_summary: str,
        requirement: str,
        candidate_count: int,
    ) -> List[str]:
        prompt = (
            "你是短视频评论生成器。"
            "必须严格遵守评论要求，不要营销腔，不要输出解释。"
            "输出JSON对象，字段 comments，为数组，包含1到3条候选评论。"
            f"\n评论要求: {requirement}"
            f"\n帖子摘要: {post_summary}"
            f"\n热评摘要: {hot_comment_summary}"
            f"\n候选条数上限: {max(1, min(3, candidate_count))}"
        )
        try:
            content = self._chat(prompt)
            data = self._extract_json(content)

            if isinstance(data, dict) and isinstance(data.get("comments"), list):
                return [str(v).strip() for v in data["comments"] if str(v).strip()]
            if isinstance(data, list):
                return [str(v).strip() for v in data if str(v).strip()]

            lines = [line.strip(" -") for line in content.splitlines() if line.strip()]
            comments = [line for line in lines if line][:3]
            if comments:
                return comments
        except Exception:
            pass

        base = (post_summary or "这个内容").strip()[:20]
        return [
            f"这个点说得很实在，受教了。",
            f"看完有启发，想问下你是怎么做到{base}的？",
            "感谢分享，信息很有用。",
        ][: max(1, min(3, candidate_count))]

    def _chat(self, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model_id,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": "你是严谨的中文运营助手，输出尽量结构化。"},
                {"role": "user", "content": prompt},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    @staticmethod
    def _extract_json(text: str) -> Any:
        txt = (text or "").strip()
        if not txt:
            return {}
        try:
            return json.loads(txt)
        except Exception:
            pass

        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", txt)
        if not match:
            return {}

        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            return {}

    @staticmethod
    def _clean_keywords(items: List[Any], max_count: int) -> List[str]:
        result: List[str] = []
        seen = set()
        for item in items:
            word = str(item).strip()
            if not word or word in seen:
                continue
            seen.add(word)
            result.append(word)
            if len(result) >= max(1, max_count):
                break
        return result

    @staticmethod
    def _fallback_expand(seed: str, max_count: int) -> List[str]:
        templates = [
            "{k}",
            "{k} 日常",
            "{k} 推荐",
            "{k} 热门",
            "{k} 合集",
            "{k} 技巧",
            "{k} 教程",
            "{k} 避坑",
            "{k} 分享",
            "{k} 讨论",
            "{k} 新手",
            "{k} 进阶",
        ]
        words = []
        used = set()
        for tpl in templates:
            item = tpl.format(k=seed).strip()
            if not item or item in used:
                continue
            used.add(item)
            words.append(item)
            if len(words) >= max(1, max_count):
                break
        return words
