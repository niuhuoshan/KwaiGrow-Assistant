from __future__ import annotations

import random
import time
from datetime import date
from typing import List

from app.browser.kuaishou_client import KuaishouClient
from app.browser.selectors_loader import SelectorLoader
from app.config import AppConfig
from app.llm import OpenAICompatClient
from app.models import PostItem
from app.rules import pick_comment
from app.store import SQLiteStore


class CommentOrchestrator:
    def __init__(self, cfg: AppConfig, logger) -> None:
        self.cfg = cfg
        self.logger = logger
        self.store = SQLiteStore(cfg.dedup.db_path)
        self.llm = OpenAICompatClient(cfg.model)

    def close(self) -> None:
        self.store.close()

    def run_forever(self) -> None:
        self.logger.info("[AUTO] 启动持续运行模式")
        while True:
            self.run_round()
            sleep_seconds = max(10, self.cfg.run.loop_interval_seconds)
            self.logger.info("[AUTO] 本轮结束，%s 秒后进入下一轮", sleep_seconds)
            time.sleep(sleep_seconds)

    def run_round(self) -> None:
        round_comments = 0
        daily_count = self.store.daily_comment_count(date.today())
        selectors = SelectorLoader.load(self.cfg.platform.selectors_file)

        self.logger.info("[AUTO] 本轮启动 | topics=%s | 今日已评论=%s", len(self.cfg.topics), daily_count)

        with KuaishouClient(
            selectors=selectors,
            user_data_dir=self.cfg.run.user_data_dir,
            headless=self.cfg.run.headless,
            post_load_wait_seconds=self.cfg.run.post_load_wait_seconds,
            logger=self.logger,
        ) as browser:
            browser.ensure_login()

            for topic in self.cfg.topics:
                if self._reach_limit(round_comments, daily_count):
                    break

                keywords = self.llm.expand_keywords(topic, self.cfg.ai.expand_keywords_count)
                self.logger.info("[AI][关键词拓展] topic=%s -> %s", topic, keywords)
                self.store.log_action("keyword_expand", "ok", f"{topic} => {keywords}")

                for keyword in keywords:
                    if self._reach_limit(round_comments, daily_count):
                        break

                    posts = browser.search_posts(keyword, self.cfg.run.max_posts_per_keyword)
                    self.logger.info("[AUTO][搜索] keyword=%s | posts=%s", keyword, len(posts))
                    self.store.log_action("search", "ok", f"keyword={keyword}, posts={len(posts)}")

                    for post in posts:
                        if self._reach_limit(round_comments, daily_count):
                            break

                        if self._process_post(browser, post):
                            round_comments += 1
                            daily_count += 1
                            self._sleep_random()

        self.logger.info("[AUTO] 本轮完成 | 新增评论=%s | 今日累计=%s", round_comments, daily_count)

    def _process_post(self, browser: KuaishouClient, post: PostItem) -> bool:
        title_hash = self.store.hash_text(post.title)
        self.store.upsert_post_seen(post, title_hash)

        if self.store.is_post_commented(
            post_id=post.post_id,
            post_url=post.url,
            title_hash=title_hash,
            skip_same_title_hash=self.cfg.dedup.skip_same_title_hash,
        ):
            self.logger.info("[AUTO][去重] 跳过已评论或重复帖子 | post_id=%s", post.post_id)
            self.store.log_action("dedup", "skip", "already commented or same title", post.post_id)
            return False

        context = browser.fetch_post_context(post)
        post_text = context.post_text.strip() or post.title.strip()
        hot_comments = context.hot_comments

        gate_result = {
            "should_comment": True,
            "reason": "comment gate disabled",
            "post_summary": post_text,
            "hot_comment_summary": "；".join(hot_comments[:5]),
        }

        if self.cfg.ai.enable_comment_gate:
            gate_result = self.llm.should_comment(post_text, hot_comments, self.cfg.comment.requirement)

        self.logger.info(
            "[AI][评论判定] post_id=%s | should=%s | reason=%s",
            post.post_id,
            gate_result.get("should_comment"),
            gate_result.get("reason"),
        )
        self.store.log_action(
            "comment_gate",
            "ok",
            f"should={gate_result.get('should_comment')}, reason={gate_result.get('reason')}",
            post.post_id,
        )

        if not gate_result.get("should_comment"):
            return False

        candidates = self.llm.generate_comments(
            post_summary=gate_result.get("post_summary") or post_text,
            hot_comment_summary=gate_result.get("hot_comment_summary") or "；".join(hot_comments[:5]),
            requirement=self.cfg.comment.requirement,
            candidate_count=self.cfg.comment.candidate_count,
        )

        chosen, rejected = pick_comment(
            candidates=candidates,
            max_length=self.cfg.comment.max_length,
            banned_words=self.cfg.comment.banned_words,
            duplicate_checker=lambda text: self.store.is_recent_comment_duplicate(
                text,
                self.cfg.dedup.recent_comment_window,
            ),
        )

        if rejected:
            self.logger.info("[AUTO][规则过滤] post_id=%s | rejected=%s", post.post_id, rejected)

        if not chosen:
            self.logger.warning("[AUTO][规则过滤] 无可用评论 | post_id=%s", post.post_id)
            self.store.log_action("rule_filter", "skip", "no valid comment", post.post_id)
            return False

        if self.cfg.run.dry_run:
            self.logger.info("[AUTO][DRY_RUN] 拟发布评论 | post_id=%s | comment=%s", post.post_id, chosen)
            self.store.log_action("submit", "dry_run", chosen, post.post_id)
            return False

        submitted = browser.submit_comment(post, chosen)
        if not submitted:
            self.logger.warning("[AUTO][发布] 发布失败 | post_id=%s", post.post_id)
            self.store.log_action("submit", "failed", "submit button not found", post.post_id)
            return False

        self.store.mark_commented(post, title_hash, chosen)
        self.store.log_action("submit", "ok", chosen, post.post_id)
        self.logger.info("[AUTO][发布] 成功 | post_id=%s | comment=%s", post.post_id, chosen)
        return True

    def _reach_limit(self, round_comments: int, daily_count: int) -> bool:
        if round_comments >= self.cfg.run.per_round_comment_limit:
            self.logger.info("[AUTO][限流] 达到本轮评论上限: %s", self.cfg.run.per_round_comment_limit)
            return True

        if daily_count >= self.cfg.run.daily_comment_limit:
            self.logger.info("[AUTO][限流] 达到每日评论上限: %s", self.cfg.run.daily_comment_limit)
            return True

        return False

    def _sleep_random(self) -> None:
        seconds = random.uniform(self.cfg.run.sleep_min_seconds, self.cfg.run.sleep_max_seconds)
        self.logger.info("[AUTO][节奏] 随机等待 %.1f 秒", seconds)
        time.sleep(seconds)
