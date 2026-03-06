from __future__ import annotations

import re
from typing import Dict, List
from urllib.parse import quote, unquote, urljoin

from playwright.sync_api import BrowserContext, Error, Page, TimeoutError, sync_playwright

from app.models import PostContext, PostItem


class KuaishouClient:
    def __init__(
        self,
        selectors: Dict[str, str],
        user_data_dir: str,
        headless: bool,
        post_load_wait_seconds: float,
        logger,
    ) -> None:
        self.selectors = selectors
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.post_load_wait_seconds = post_load_wait_seconds
        self.logger = logger

        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "KuaishouClient":
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            self.user_data_dir,
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 920},
        )
        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        self.page.set_default_timeout(20000)
        self.page.set_default_navigation_timeout(45000)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()

    def ensure_login(self) -> None:
        page = self._require_page()
        page.goto("https://www.kuaishou.com", wait_until="domcontentloaded")
        self._wait_post_load()
        if self._has_visible(self.selectors.get("login_button", ""), 1500):
            raise RuntimeError("检测到未登录快手，请先在浏览器中完成登录后重试")

    def search_posts(self, keyword: str, limit: int) -> List[PostItem]:
        page = self._require_page()
        search_url = f"https://www.kuaishou.com/search/video?searchKey={quote(keyword)}"
        page.goto(search_url, wait_until="domcontentloaded")
        self._wait_post_load()

        posts = self._collect_link_posts(keyword, limit)
        if not posts:
            posts = self._collect_card_posts(keyword, limit)
        return posts

    def fetch_post_context(self, post: PostItem) -> PostContext:
        page = self._require_page()
        self._open_post(post)
        self._wait_post_load()

        data = page.evaluate(
            """
            () => {
              const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
              const pickVisible = (nodes) => nodes.filter((el) => {
                const r = el.getBoundingClientRect();
                if (!r || r.width < 40 || r.height < 16) return false;
                const st = window.getComputedStyle(el);
                return st.display !== 'none' && st.visibility !== 'hidden';
              });

              const titleNodes = pickVisible(Array.from(document.querySelectorAll('h1,h2,[class*=title],[data-e2e*=desc]')));
              let postText = '';
              for (const el of titleNodes) {
                const text = norm(el.textContent);
                if (text && text.length >= 6) {
                  postText = text;
                  break;
                }
              }
              if (!postText) {
                postText = norm(document.title || '');
              }

              const commentNodes = pickVisible(Array.from(document.querySelectorAll('[class*=comment] p,[class*=comment] span,li p,li span')));
              const comments = [];
              const seen = new Set();
              for (const el of commentNodes) {
                const text = norm(el.textContent);
                if (!text || text.length < 4 || text.length > 80) continue;
                if (seen.has(text)) continue;
                seen.add(text);
                comments.push(text);
                if (comments.length >= 5) break;
              }

              return {
                post_text: postText,
                hot_comments: comments,
              };
            }
            """
        )

        post_text = str(data.get("post_text") or post.title or "").strip()
        hot_comments = [str(x).strip() for x in (data.get("hot_comments") or []) if str(x).strip()]
        return PostContext(post_text=post_text, hot_comments=hot_comments)

    def submit_comment(self, post: PostItem, content: str) -> bool:
        page = self._require_page()
        self._open_post(post)
        self._wait_post_load()

        comment_button = self.selectors.get("comment_button", "")
        if comment_button:
            self._click_first(comment_button, 2500)
            self._wait_ms(400)

        typed = self._fill_first(self.selectors.get("comment_input", ""), content, 4000)
        if not typed:
            typed = self._fill_fallback(content)
        if not typed:
            raise RuntimeError("未找到可输入评论的位置")

        submitted = self._click_first(self.selectors.get("comment_submit", ""), 3000)
        if not submitted:
            submitted = bool(
                page.evaluate(
                    """
                    () => {
                      const input = document.querySelector("input[placeholder*='说点什么'],textarea[placeholder*='说点什么']");
                      if (!input) return false;
                      const send = document.querySelector('.send-btn:not(.disabled), .send-btn');
                      if (!send) return false;
                      send.click();
                      return true;
                    }
                    """
                )
            )

        self._wait_ms(1200)
        return submitted

    def _collect_link_posts(self, keyword: str, limit: int) -> List[PostItem]:
        page = self._require_page()
        candidates = self._split_candidates(self.selectors.get("post_link", ""))
        posts: List[PostItem] = []
        seen_ids = set()

        for selector in candidates:
            locator = page.locator(selector)
            count = min(locator.count(), max(limit * 4, 60))
            for idx in range(count):
                if len(posts) >= limit:
                    break
                link = locator.nth(idx)
                href = ""
                try:
                    href = (link.get_attribute("href") or "").strip()
                except Error:
                    continue
                if not href:
                    continue
                url = urljoin(page.url, href)
                post_id = self._extract_post_id(url)
                if not post_id or post_id in seen_ids:
                    continue

                title = ""
                try:
                    title = (link.inner_text() or "").strip()
                except Error:
                    title = ""
                if not title:
                    title = post_id

                posts.append(PostItem(post_id=post_id, url=url, title=title[:200], keyword=keyword))
                seen_ids.add(post_id)
            if len(posts) >= limit:
                break
        return posts

    def _collect_card_posts(self, keyword: str, limit: int) -> List[PostItem]:
        page = self._require_page()
        cards = page.evaluate(
            """
            ({ q, maxCount }) => {
              const normalizedQuery = (q || '').toLowerCase().trim();
              const bad = /(www\.kuaishou\.com|京ICP备|京公网安备|违法和不良信息举报|举报专区|未成年人关怀热线|推荐\s*发现\s*关注\s*直播\s*赛事)/i;
              const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();

              const nodes = Array.from(document.querySelectorAll('main div'));
              const picked = [];
              for (const el of nodes) {
                if (!el.querySelector('img')) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 120 || rect.height < 80) continue;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const text = norm(el.innerText);
                if (!text || text.length < 8 || text.length > 220) continue;
                if (bad.test(text)) continue;
                if (normalizedQuery && !text.toLowerCase().includes(normalizedQuery)) continue;
                picked.push(text.slice(0, 200));
                if (picked.length >= Math.max(maxCount * 4, 40)) break;
              }
              return Array.from(new Set(picked));
            }
            """,
            {"q": keyword, "maxCount": max(1, limit)},
        )

        posts: List[PostItem] = []
        for idx, title in enumerate(cards[: max(1, limit)]):
            posts.append(
                PostItem(
                    post_id=f"kscard:{quote(keyword)}:{idx}",
                    url=page.url,
                    title=str(title),
                    keyword=keyword,
                )
            )
        return posts

    def _open_post(self, post: PostItem) -> None:
        page = self._require_page()
        card = self._parse_card_id(post.post_id)
        if card:
            search_url = f"https://www.kuaishou.com/search/video?searchKey={quote(card['keyword'])}"
            page.goto(search_url, wait_until="domcontentloaded")
            self._wait_post_load()
            ok = page.evaluate(
                """
                ({ q, targetIndex }) => {
                  const normalizedQuery = (q || '').toLowerCase().trim();
                  const bad = /(www\.kuaishou\.com|京ICP备|京公网安备|违法和不良信息举报|举报专区|未成年人关怀热线|推荐\s*发现\s*关注\s*直播\s*赛事)/i;
                  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
                  const nodes = Array.from(document.querySelectorAll('main div'));
                  const picked = [];
                  for (const el of nodes) {
                    if (!el.querySelector('img')) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 120 || rect.height < 80) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const text = norm(el.innerText);
                    if (!text || text.length < 8 || text.length > 220) continue;
                    if (bad.test(text)) continue;
                    if (normalizedQuery && !text.toLowerCase().includes(normalizedQuery)) continue;
                    picked.push(el);
                  }
                  const target = picked[Math.max(0, targetIndex)];
                  if (!target) return false;
                  target.scrollIntoView({ behavior: 'instant', block: 'center' });
                  target.click();
                  return true;
                }
                """,
                {"q": card["keyword"], "targetIndex": card["index"]},
            )
            if not ok:
                raise RuntimeError(f"无法打开快手卡片帖子: {post.post_id}")
            self._wait_post_load()
            return

        if post.url:
            page.goto(post.url, wait_until="domcontentloaded")
        else:
            page.goto(f"https://www.kuaishou.com/short-video/{post.post_id}", wait_until="domcontentloaded")

    @staticmethod
    def _extract_post_id(url: str) -> str:
        for pattern in [
            r"/short-video/([0-9A-Za-z_-]+)",
            r"/video/([0-9A-Za-z_-]+)",
            r"[?&]photoId=([0-9A-Za-z_-]+)",
        ]:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _parse_card_id(post_id: str):
        match = re.match(r"^kscard:([^:]+):(\d+)$", post_id or "")
        if not match:
            return None
        keyword = unquote(match.group(1))
        index = int(match.group(2))
        return {"keyword": keyword, "index": index}

    def _split_candidates(self, selector: str) -> List[str]:
        return [v.strip() for v in (selector or "").split(",") if v.strip()]

    def _click_first(self, selector: str, timeout_ms: int) -> bool:
        page = self._require_page()
        for candidate in self._split_candidates(selector):
            loc = page.locator(candidate).first
            try:
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click(timeout=timeout_ms)
                return True
            except (TimeoutError, Error):
                continue
        return False

    def _fill_first(self, selector: str, text: str, timeout_ms: int) -> bool:
        page = self._require_page()
        for candidate in self._split_candidates(selector):
            loc = page.locator(candidate).first
            try:
                loc.wait_for(state="visible", timeout=timeout_ms)
                tag = (loc.evaluate("el => el.tagName.toLowerCase()") or "").strip()
                loc.click(timeout=timeout_ms)
                if tag in {"input", "textarea"}:
                    loc.fill("")
                    loc.type(text, delay=25)
                else:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.type(text, delay=25)
                return True
            except (TimeoutError, Error):
                continue
        return False

    def _fill_fallback(self, text: str) -> bool:
        page = self._require_page()
        return bool(
            page.evaluate(
                """
                (value) => {
                  const target = document.querySelector("textarea,input[placeholder*='说点什么'],div[contenteditable='true']");
                  if (!target) return false;
                  target.focus?.();
                  if (target.isContentEditable) {
                    target.textContent = value;
                  } else {
                    target.value = value;
                  }
                  target.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                  target.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }
                """,
                text,
            )
        )

    def _has_visible(self, selector: str, timeout_ms: int) -> bool:
        if not selector:
            return False
        page = self._require_page()
        for candidate in self._split_candidates(selector):
            try:
                return page.locator(candidate).first.is_visible(timeout=timeout_ms)
            except (TimeoutError, Error):
                continue
        return False

    def _wait_post_load(self) -> None:
        self._wait_ms(int(self.post_load_wait_seconds * 1000))

    def _wait_ms(self, ms: int) -> None:
        page = self._require_page()
        page.wait_for_timeout(max(0, ms))

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("browser page is not initialized")
        return self.page
