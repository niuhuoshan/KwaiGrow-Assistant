from __future__ import annotations

import logging
import random
import time
from typing import List, Set

from .ai.comment_engine import CommentEngine
from .ai.keyword_expander import KeywordExpander
from .ai.openai_client import OpenAIChatClient
from .browser.kuaishou_client import KuaishouClient
from .config import AppConfig, WaitRange, load_selector_map
from .schema import CommentRecord, Post
from .storage.dedup_store import DedupStore
from .utils.text import short_hash, truncate


class AutoCommenterOrchestrator:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._logger = logging.getLogger(self.__class__.__name__)
        if cfg.platform.lower() != "kuaishou":
            raise ValueError("this project currently supports platform=kuaishou only")

        self._ai_client = OpenAIChatClient(cfg.openai, enabled=cfg.ai.enabled)
        self._keyword_expander = KeywordExpander(self._ai_client, max_count=cfg.ai.keyword_max_count)
        self._comment_engine = CommentEngine(self._ai_client, candidate_count=cfg.ai.comment_candidate_count)

        selectors = load_selector_map(cfg.selectors.kuaishou_selector_file)
        self._browser = KuaishouClient(cfg.browser, selectors)
        self._store = DedupStore(cfg.dedup)

    def close(self) -> None:
        self._browser.close()
        self._store.close()
        self._ai_client.close()

    def run(self, once: bool = False) -> None:
        self._logger.info("[AI] startup health check")
        self._ensure_ai_ready()

        self._logger.info("[AUTOMATION] start browser client")
        self._browser.start()
        self._logger.info(
            "[AUTOMATION] loaded limits: max_comments_per_round=%s daily_comment_limit=%s single_keyword_search=%s",
            self._cfg.runtime.max_comments_per_round,
            self._cfg.runtime.daily_comment_limit,
            self._cfg.runtime.single_keyword_search,
        )

        round_index = 0
        while True:
            round_index += 1
            self._logger.info("[AUTOMATION] round=%s begin", round_index)

            try:
                self._run_single_round()
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("[AUTOMATION] round=%s failed: %s", round_index, exc)

            if once:
                self._logger.info("[AUTOMATION] once mode enabled, stop after round=%s", round_index)
                break

            self._random_wait(self._cfg.runtime.round_wait_seconds, "round idle")

    def _run_single_round(self) -> None:
        if self._store.count_today() >= self._cfg.runtime.daily_comment_limit:
            self._logger.info(
                "[AUTOMATION] daily limit reached (%s), skip this round",
                self._cfg.runtime.daily_comment_limit,
            )
            return

        logged_in = self._browser.check_login_state()
        if not logged_in:
            self._logger.warning("[AUTOMATION] kuaishou not logged in, trigger login flow")
            self._browser.start_login_flow()
            self._logger.warning("[AUTOMATION] login required, finish login then rerun")
            return

        round_comments = 0
        for direction in self._unique_direction_keywords():
            if not self._is_valid_search_keyword(direction):
                self._logger.warning("[AUTOMATION] skip invalid direction keyword=%s", direction)
                continue

            used_keywords = self._store.get_used_keywords_for_topic(direction, limit=500)
            keywords = self._expand_keywords(direction, used_keywords)
            if not keywords:
                self._logger.info("[AI] no new keywords for direction=%s, skip", direction)
                continue

            if self._cfg.runtime.single_keyword_search:
                keywords = keywords[:1]

            for keyword in keywords:
                if self._limit_reached(round_comments):
                    return

                self._store.add_used_keyword(direction, keyword)
                self._logger.info("[AUTOMATION] search keyword=%s | direction=%s", keyword, direction)
                try:
                    posts = self._browser.search_posts(
                        keyword=keyword,
                        limit=self._cfg.runtime.search_limit_per_keyword,
                        sort_by=self._cfg.runtime.sort_by,
                        time_range=self._cfg.runtime.time_range,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning("[AUTOMATION] search failed keyword=%s error=%s", keyword, exc)
                    continue

                self._logger.info("[AUTOMATION] fetched %s posts for keyword=%s", len(posts), keyword)
                for post in posts:
                    if self._limit_reached(round_comments):
                        return

                    if self._should_skip_post(post):
                        continue

                    commented = self._process_post(keyword, post)
                    if not commented:
                        continue

                    round_comments += 1
                    self._logger.info(
                        "[AUTOMATION] commented post_id=%s keyword=%s round_count=%s",
                        post.post_id,
                        keyword,
                        round_comments,
                    )
                    self._random_wait(self._cfg.runtime.action_wait_seconds, "after comment")

    def _process_post(self, keyword: str, post: Post) -> bool:
        self._logger.info("[AUTOMATION] fetch post context post_id=%s", post.post_id)
        try:
            context = self._browser.fetch_post_context(post)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("[AUTOMATION] fetch post context failed post_id=%s error=%s", post.post_id, exc)
            return False

        self._logger.info("[AI] commentability check post_id=%s", post.post_id)
        commentable, reason = self._comment_engine.is_commentable(
            post_summary=context.content_summary,
            hot_comments_summary=context.hot_comments_summary,
            requirements=self._cfg.comment_rules.requirements,
        )
        if not commentable:
            if self._cfg.ai.strict_comment_gate:
                self._logger.info("[AI] post not commentable post_id=%s reason=%s", post.post_id, reason)
                return False
            self._logger.info(
                "[AI] gate建议跳过，但当前为非严格模式，继续生成评论 post_id=%s reason=%s",
                post.post_id,
                reason,
            )

        self._logger.info("[AI] generate comment candidates post_id=%s", post.post_id)
        candidates = self._comment_engine.generate_candidates(
            post_summary=context.content_summary,
            hot_comments_summary=context.hot_comments_summary,
            requirements=self._cfg.comment_rules.requirements,
        )

        chosen = self._comment_engine.pick_valid_comment(
            candidates=candidates,
            banned_words=self._cfg.comment_rules.banned_words,
            min_length=self._cfg.comment_rules.min_length,
            max_length=self._cfg.comment_rules.max_length,
            is_repeated=(
                (lambda text: False)
                if self._cfg.runtime.comment_every_post
                else (lambda text: self._store.comment_text_exists(text))
            ),
        )

        if not chosen and self._cfg.runtime.comment_every_post:
            chosen = self._force_comment_text(context.content_summary, post.post_id)
            self._logger.info("[AI] fallback force comment post_id=%s text=%s", post.post_id, truncate(chosen, 50))

        if not chosen:
            self._logger.info("[AI] no valid comment after rules post_id=%s candidates=%s", post.post_id, candidates)
            return False

        self._logger.info("[AUTOMATION] submit comment post_id=%s text=%s", post.post_id, truncate(chosen, 50))
        try:
            self._browser.submit_comment(post, chosen)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("[AUTOMATION] submit failed post_id=%s error=%s", post.post_id, exc)
            return False

        title_hash = short_hash(post.title)
        self._store.insert_comment(
            CommentRecord(
                platform=self._cfg.platform,
                keyword=keyword,
                post_id=post.post_id,
                url=post.url,
                title=post.title,
                title_hash=title_hash,
                comment_text=chosen,
            )
        )
        self._logger.info("[AUTOMATION] write sqlite record post_id=%s", post.post_id)
        return True

    def _force_comment_text(self, post_summary: str, post_id: str) -> str:
        base = (post_summary or "这条内容").strip().replace("\n", " ")
        if len(base) > 18:
            base = base[:18]
        suffix = short_hash(post_id)[:4]
        text = f"这个点我认同，{base}，想请教下你是怎么做到的？{suffix}"

        text = text[: self._cfg.comment_rules.max_length]
        if len(text) < self._cfg.comment_rules.min_length:
            text = "内容很有启发，想请教下你是怎么做到的？"
        return text

    def _expand_keywords(self, direction: str, used_keywords: List[str]) -> List[str]:
        self._logger.info(
            "[AI] expand direction keyword=%s | used_history_count=%s",
            direction,
            len(used_keywords),
        )
        words = self._keyword_expander.expand(direction, used_keywords=used_keywords)
        if not words:
            self._logger.warning("[AI] no expanded keywords for direction=%s", direction)
            return []

        unique_words: List[str] = []
        seen_normalized: Set[str] = set()
        used_normalized = {
            self._normalize_keyword(w)
            for w in used_keywords
            if self._normalize_keyword(w)
        }

        for raw_word in words:
            normalized_word = self._normalize_keyword(raw_word)
            if not normalized_word:
                continue
            if normalized_word in seen_normalized or normalized_word in used_normalized:
                continue

            seen_normalized.add(normalized_word)
            unique_words.append(normalized_word)
            if len(unique_words) >= self._cfg.ai.keyword_max_count:
                break

        self._logger.info("[AI] expanded keywords direction=%s -> %s", direction, unique_words)
        return unique_words

    def _should_skip_post(self, post: Post) -> bool:
        title_hash = short_hash(post.title)
        skipped = self._store.has_commented(
            post_id=post.post_id,
            url=post.url,
            title_hash=title_hash,
        )
        if skipped:
            self._logger.info("[AUTOMATION] skip dedup post_id=%s url=%s", post.post_id, post.url)
        return skipped

    def _unique_direction_keywords(self) -> List[str]:
        unique_directions: List[str] = []
        seen: Set[str] = set()

        for raw_direction in self._cfg.topics.direction_keywords:
            normalized = self._normalize_keyword(raw_direction)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_directions.append(normalized)

        return unique_directions

    @staticmethod
    def _normalize_keyword(text: str) -> str:
        return " ".join((text or "").strip().split())

    @staticmethod
    def _is_valid_search_keyword(text: str) -> bool:
        if not text:
            return False
        if len(text) < 2 or len(text) > 16:
            return False
        if any(mark in text for mark in ["，", "。", "！", "？", "；", ",", ".", "!", "?", "#", "\n"]):
            return False
        return True

    def _ensure_ai_ready(self) -> None:
        if not self._cfg.ai.enabled:
            raise RuntimeError("配置要求 AI 必须启用（ai.enabled=true）")

        self._ai_client.ensure_available()
        self._logger.info("[AI] startup health check passed")

    def _limit_reached(self, round_comments: int) -> bool:
        if round_comments >= self._cfg.runtime.max_comments_per_round:
            self._logger.info(
                "[AUTOMATION] round max reached (%s)",
                self._cfg.runtime.max_comments_per_round,
            )
            return True

        today_count = self._store.count_today()
        if today_count >= self._cfg.runtime.daily_comment_limit:
            self._logger.info(
                "[AUTOMATION] daily max reached (%s)",
                self._cfg.runtime.daily_comment_limit,
            )
            return True

        return False

    def _random_wait(self, wait_range: WaitRange, reason: str) -> None:
        seconds = random.uniform(wait_range.min_seconds, wait_range.max_seconds)
        self._logger.info("[AUTOMATION] wait %.2fs (%s)", seconds, reason)
        time.sleep(seconds)
