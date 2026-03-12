from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from time import perf_counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse, parse_qsl, urljoin
from urllib.request import urlopen
from urllib.error import URLError

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ..config import BrowserConfig
from ..schema import Post, PostContext
from ..utils.text import normalize_spaces, short_hash


class KuaishouClient:
    def __init__(self, cfg: BrowserConfig, selectors: Dict[str, str]):
        self._cfg = cfg
        self._selectors = selectors
        self._logger = logging.getLogger(self.__class__.__name__)

        self._playwright: Optional[Playwright] = None
        self._cdp_browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._current_search_keyword: Optional[str] = None

    def start(self) -> None:
        if self._context:
            self._logger.info("[BROWSER] start skipped: context already initialized")
            return

        start_ts = perf_counter()
        self._logger.info(
            "[BROWSER] start begin | mode=%s | headless=%s | ws_url=%s | user_data_dir=%s | action_timeout_ms=%s",
            "cdp" if self._cfg.ws_url else "persistent",
            self._cfg.headless,
            self._cfg.ws_url if self._cfg.ws_url else "",
            self._cfg.user_data_dir,
            self._cfg.action_timeout_ms,
        )

        _proxy_keys = ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]
        _saved_proxy = {k: os.environ.pop(k) for k in _proxy_keys if k in os.environ}
        try:
            self._logger.info("[BROWSER] init playwright runtime")
            self._playwright = sync_playwright().start()
            self._logger.info("[BROWSER] playwright runtime ready")
        except PermissionError as exc:
            raise RuntimeError(
                "Playwright 启动被系统拒绝访问（WinError 5）。请以管理员权限运行，"
                "并检查安全软件是否拦截 python/node 子进程。"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Playwright runtime start failed: {exc}") from exc
        finally:
            os.environ.update(_saved_proxy)

        if self._cfg.ws_url:
            port = self._cfg.remote_debugging_port
            if self._cfg.auto_launch_chrome and not self._is_cdp_port_ready(port):
                self._logger.info("[BROWSER] CDP port not ready, auto-launching Chrome...")
                self._auto_launch_chrome(port)
            self._logger.info("[BROWSER] connect CDP | url=%s", self._cfg.ws_url)
            try:
                self._cdp_browser = self._playwright.chromium.connect_over_cdp(
                    self._cfg.ws_url,
                    timeout=self._cfg.action_timeout_ms,
                )
                self._logger.info("[BROWSER] CDP connected")
            except Exception as exc:
                raise RuntimeError(
                    f"CDP 连接失败（{self._cfg.ws_url}）。"
                    "请确认 Chrome 已用 --remote-debugging-port 启动，或检查 ws_url 配置。"
                ) from exc
            contexts = self._cdp_browser.contexts
            self._logger.info("[BROWSER] CDP contexts=%s", len(contexts))
            if not contexts:
                self._context = self._cdp_browser.new_context()
            else:
                self._context = contexts[0]
        else:
            user_data_dir = Path(self._cfg.user_data_dir)
            user_data_dir.mkdir(parents=True, exist_ok=True)
            self._logger.info("[BROWSER] launch persistent context | dir=%s", user_data_dir)

            launch_kwargs = {
                "headless": self._cfg.headless,
                "args": ["--disable-blink-features=AutomationControlled"],
                "viewport": {"width": 1440, "height": 920},
            }
            if self._cfg.executable_path:
                launch_kwargs["executable_path"] = self._cfg.executable_path

            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    **launch_kwargs,
                )
            except Exception as exc:
                raise RuntimeError(f"persistent browser launch failed: {exc}") from exc

        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        page.set_default_timeout(self._cfg.action_timeout_ms)
        page.set_default_navigation_timeout(self._cfg.navigation_timeout_ms)

        self._page = page
        elapsed = perf_counter() - start_ts
        self._logger.info("[BROWSER] start success | page_ready=true | elapsed=%.2fs", elapsed)

    def close(self) -> None:
        if self._context:
            self._context.close()
            self._context = None
        if self._cdp_browser:
            self._cdp_browser.close()
            self._cdp_browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None
        self._current_search_keyword = None

    def check_login_state(self) -> bool:
        page = self._require_page()
        page.goto("https://www.kuaishou.com", wait_until="domcontentloaded")
        self._sleep(self._cfg.post_load_wait_ms)

        if self._has_visible(self._selectors.get("login_button", "button:has-text('登录')"), timeout=1200):
            return False

        if self._has_visible("text=消息", timeout=1200):
            return True

        if self._has_visible("text=我的", timeout=1200):
            return True

        return not self._has_visible(self._selectors.get("login_button", "button:has-text('登录')"), timeout=800)

    def start_login_flow(self) -> None:
        page = self._require_page()
        page.goto("https://www.kuaishou.com", wait_until="domcontentloaded")
        self._sleep(600)
        clicked = self._click_first(self._selectors.get("login_button", "button:has-text('登录')"), timeout=2500)
        if not clicked:
            raise RuntimeError("failed to start login flow")

    def search_posts(self, keyword: str, limit: int, sort_by: str = "latest", time_range: str = "week") -> List[Post]:
        page = self._require_page()
        search_url = f"https://www.kuaishou.com/search/video?searchKey={quote(keyword)}"

        page.goto(search_url, wait_until="domcontentloaded")
        self._sleep(self._cfg.post_load_wait_ms)

        self._apply_sort_and_filter(sort_by=sort_by, time_range=time_range)
        self._sleep(self._cfg.post_load_wait_ms)

        posts = self._collect_posts(limit)
        if not posts:
            posts = self._collect_cards(keyword, limit)

        self._current_search_keyword = keyword
        return posts

    def fetch_post_context(self, post: Post) -> PostContext:
        page = self._require_page()
        self.open_post(post)

        content_summary = normalize_spaces(
            page.evaluate(
                r"""
                () => {
                  const candidates = [
                    ...document.querySelectorAll('h1, h2, [class*=\"title\"], [class*=\"desc\"]'),
                  ];
                  const values = [];
                  for (const el of candidates) {
                    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
                    if (!text) continue;
                    if (text.length < 3) continue;
                    values.push(text.slice(0, 160));
                    if (values.length >= 3) break;
                  }
                  return values.join(' | ');
                }
                """
            )
            or ""
        )

        if not content_summary:
            content_summary = normalize_spaces(post.title)

        hot_comments_summary = normalize_spaces(
            page.evaluate(
                r"""
                () => {
                  const selectors = [
                    '[class*=\"comment\"] p',
                    '[class*=\"comment\"] span',
                    '[data-e2e*=\"comment\"] span',
                  ];

                  const picked = [];
                  for (const sel of selectors) {
                    const nodes = Array.from(document.querySelectorAll(sel));
                    for (const node of nodes) {
                      const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
                      if (!text || text.length < 3 || text.length > 120) continue;
                      picked.push(text);
                      if (picked.length >= 5) break;
                    }
                    if (picked.length >= 5) break;
                  }

                  return Array.from(new Set(picked)).slice(0, 5).join(' | ');
                }
                """
            )
            or ""
        )

        return PostContext(post=post, content_summary=content_summary, hot_comments_summary=hot_comments_summary)

    def submit_comment(self, post: Post, comment_text: str) -> None:
        self.open_post(post)

        comment_button = self._selectors.get("comment_button", "")
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            self._prepare_comment_input(comment_button)

            typed = self._fill_first(self._selectors.get("comment_input", ""), comment_text, timeout=4200)
            if not typed:
                typed = self._fill_kuaishou_fallback(comment_text)

            if not typed:
                self._logger.warning("comment input not found, retry attempt=%s post_id=%s", attempt, post.post_id)
                if attempt < max_attempts:
                    self.open_post(post)
                    continue
                raise RuntimeError("comment input not found for kuaishou")

            page = self._require_page()
            responses: List[Tuple[int, str, str]] = []

            def on_response(resp):
                try:
                    url = (resp.url or "").lower()
                    method = (resp.request.method or "").upper()
                    if "comment" not in url:
                        return
                    responses.append((int(resp.status), method, url))
                except Exception:
                    return

            page.on("response", on_response)
            try:
                submitted = self._click_submit_first(self._selectors.get("comment_submit", ""), timeout=3200)
                if not submitted:
                    submitted = self._submit_kuaishou_fallback()

                if not submitted:
                    self._logger.warning("comment submit button not found, retry attempt=%s post_id=%s", attempt, post.post_id)
                    if attempt < max_attempts:
                        self.open_post(post)
                        continue
                    raise RuntimeError("comment submit button not found for kuaishou")

                self._sleep(2200)
            finally:
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass

            if self._confirm_comment_submitted(comment_text, responses):
                self._close_video_overlay()
                return

            self._logger.warning(
                "comment submit not confirmed, retry attempt=%s post_id=%s responses=%s",
                attempt,
                post.post_id,
                responses[:5],
            )
            if attempt < max_attempts:
                self.open_post(post)

        raise RuntimeError("comment submit not confirmed")

    def open_post(self, post: Post) -> None:
        page = self._require_page()

        card = self._parse_card_post_id(post.post_id)
        if card:
            self._close_video_overlay()
            keyword = card["keyword"]
            index = card["index"]
            search_url = f"https://www.kuaishou.com/search/video?searchKey={quote(keyword)}"

            current_url_keyword = self._extract_search_keyword_from_url(page.url)
            on_search_page = "/search/video" in (page.url or "")

            need_refresh = (
                self._cfg.search_each_post
                or (self._current_search_keyword != keyword)
                or (not on_search_page)
                or (current_url_keyword != keyword)
            )
            if need_refresh:
                page.goto(search_url, wait_until="domcontentloaded")
                self._sleep(self._cfg.post_load_wait_ms)
                self._current_search_keyword = keyword

            try:
                self._open_kuaishou_card(keyword, index)
            except Exception:
                page.goto(search_url, wait_until="domcontentloaded")
                self._sleep(self._cfg.post_load_wait_ms)
                self._current_search_keyword = keyword
                self._open_kuaishou_card(keyword, index)

            self._sleep(self._cfg.post_load_wait_ms)
            return

        if post.url:
            page.goto(post.url, wait_until="domcontentloaded")
            self._sleep(self._cfg.post_load_wait_ms)
            self._current_search_keyword = None
            return

        page.goto(f"https://www.kuaishou.com/short-video/{post.post_id}", wait_until="domcontentloaded")
        self._sleep(self._cfg.post_load_wait_ms)
        self._current_search_keyword = None

    def _find_chrome_executable(self) -> Optional[str]:
        if self._cfg.executable_path:
            return self._cfg.executable_path

        username = os.environ.get("USERNAME") or os.environ.get("LOGNAME") or ""

        # Windows native paths (when running on Windows directly)
        candidate_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        if username:
            candidate_paths.append(
                rf"C:\Users\{username}\AppData\Local\Google\Chrome\Application\chrome.exe"
            )

        # WSL paths (when running inside WSL)
        candidate_paths += [
            "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
            "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        ]
        if username:
            candidate_paths.append(
                f"/mnt/c/Users/{username}/AppData/Local/Google/Chrome/Application/chrome.exe"
            )

        for p in candidate_paths:
            if Path(p).exists():
                return p

        # Linux system paths
        for name in ("google-chrome", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                return found

        return None

    def _is_cdp_port_ready(self, port: int) -> bool:
        url = f"http://127.0.0.1:{port}/json/version"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                urlopen(url, timeout=1)
                return True
            except (URLError, OSError):
                time.sleep(0.5)
        return False

    def _auto_launch_chrome(self, port: int) -> None:
        exe = self._find_chrome_executable()
        if not exe:
            raise RuntimeError("找不到 Chrome，请在配置中设置 browser.executable_path")

        user_data_dir = str(Path(self._cfg.user_data_dir).resolve())
        cmd = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._logger.info("[BROWSER] auto-launched Chrome port=%s exe=%s", port, exe)

        if not self._is_cdp_port_ready(port):
            raise RuntimeError(f"Chrome 启动超时，调试端口 {port} 未就绪")

    def _close_video_overlay(self) -> None:
        """Close video overlay/player if currently open, returning to search page state."""
        page = self._require_page()
        if "/search/video" not in (page.url or ""):
            try:
                page.keyboard.press("Escape")
                self._sleep(400)
            except Exception:
                pass

    def _collect_posts(self, limit: int) -> List[Post]:
        page = self._require_page()
        # Scroll to load more content before scanning
        for _ in range(3):
            page.mouse.wheel(0, 1200)
            self._sleep(600)
        selector = self._selectors.get("post_link", "a[href*='/short-video/'], a[href*='/video/'], a[href*='photoId=']")

        posts: List[Post] = []
        seen = set()

        locator = page.locator(selector)
        total = locator.count()
        scan = min(total, max(limit * 5, 80))

        for idx in range(scan):
            link = locator.nth(idx)
            href = (link.get_attribute("href") or "").strip()
            if not href:
                try:
                    href = (
                        link.evaluate(
                            """
                            (el) => el.getAttribute('data-href')
                              || el.getAttribute('data-url')
                              || el.closest('a')?.getAttribute('href')
                              || ''
                            """
                        )
                        or ""
                    ).strip()
                except Exception:  # noqa: BLE001
                    href = ""

            if not href:
                continue

            abs_url = urljoin(page.url, href)
            post_id = self._extract_post_id(abs_url) or f"ksurl:{short_hash(abs_url)}"
            if post_id in seen:
                continue

            try:
                title = normalize_spaces(link.inner_text() or "")
            except Exception:  # noqa: BLE001
                title = ""
            if not title:
                try:
                    title = normalize_spaces(
                        link.evaluate("(el) => (el.closest('div')?.innerText || '')") or ""
                    )
                except Exception:  # noqa: BLE001
                    title = ""

            posts.append(Post(post_id=post_id, title=title[:200], url=abs_url))
            seen.add(post_id)
            if len(posts) >= limit:
                break

        return posts

    def _collect_cards(self, keyword: str, limit: int) -> List[Post]:
        page = self._require_page()

        def collect_once() -> List[str]:
            result = page.evaluate(
                r"""
                ({ q, maxCount }) => {
                  const normalizedQuery = (q || '').toLowerCase().trim();
                  const bad = /(www\.kuaishou\.com|京ICP备|京公网安备|违法和不良信息举报|举报专区|未成年人关怀热线|可灵AI|Acfun|推荐\s*发现\s*关注\s*直播\s*赛事)/i;
                  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();

                  const nodes = Array.from(document.querySelectorAll('main div'));
                  const matched = [];
                  const fallback = [];

                  for (const el of nodes) {
                    if (!el.querySelector('img')) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 120 || rect.height < 80) continue;

                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (style.cursor !== 'pointer' && el.getAttribute('role') !== 'button' && !el.onclick) continue;

                    const text = norm(el.innerText);
                    if (!text || text.length < 8 || text.length > 220) continue;
                    if (bad.test(text)) continue;

                    if (normalizedQuery && text.toLowerCase().includes(normalizedQuery)) {
                      matched.push(text.slice(0, 200));
                    } else {
                      fallback.push(text.slice(0, 200));
                    }

                    if (matched.length + fallback.length >= Math.max(maxCount * 6, 120)) break;
                  }

                  return Array.from(new Set([...matched, ...fallback]));
                }
                """,
                {"q": keyword, "maxCount": max(1, limit)},
            )
            if isinstance(result, list):
                return [normalize_spaces(str(v)) for v in result if normalize_spaces(str(v))]
            return []

        collected_titles: List[str] = []
        seen_titles = set()
        max_scroll_rounds = max(2, min(14, limit + 4))

        for _ in range(max_scroll_rounds):
            batch = collect_once()
            for text in batch:
                if text in seen_titles:
                    continue
                seen_titles.add(text)
                collected_titles.append(text)
                if len(collected_titles) >= limit:
                    break

            if len(collected_titles) >= limit:
                break

            page.mouse.wheel(0, 1400)
            self._sleep(650)

        posts: List[Post] = []
        for idx, title in enumerate(collected_titles):
            posts.append(
                Post(
                    post_id=f"kscard:{quote(keyword)}:{idx}",
                    title=title,
                    url=f"{page.url}#card-{idx}-{short_hash(title)}",
                )
            )
            if len(posts) >= limit:
                break

        return posts

    def _open_kuaishou_card(self, keyword: str, index: int) -> None:
        page = self._require_page()

        result = page.evaluate(
            r"""
            ({ q, targetIndex }) => {
              const normalizedQuery = (q || '').toLowerCase().trim();
              const bad = /(www\.kuaishou\.com|京ICP备|京公网安备|违法和不良信息举报|举报专区|未成年人关怀热线|可灵AI|Acfun|推荐\s*发现\s*关注\s*直播\s*赛事)/i;
              const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();

              const nodes = Array.from(document.querySelectorAll('main div'));
              const matched = [];
              const fallback = [];

              for (const el of nodes) {
                if (!el.querySelector('img')) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 120 || rect.height < 80) continue;

                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                if (style.cursor !== 'pointer' && el.getAttribute('role') !== 'button' && !el.onclick) continue;

                const text = norm(el.innerText);
                if (!text || text.length < 8 || text.length > 220) continue;
                if (bad.test(text)) continue;

                if (normalizedQuery && text.toLowerCase().includes(normalizedQuery)) {
                  matched.push(el);
                } else {
                  fallback.push(el);
                }
              }

              const picked = [...matched, ...fallback];
              const target = picked[Math.max(0, targetIndex)];
              if (!target) return { ok: false, count: picked.length };

              target.scrollIntoView({ behavior: 'instant', block: 'center' });
              target.click();
              return { ok: true, count: picked.length };
            }
            """,
            {"q": keyword, "targetIndex": max(0, index)},
        )

        if not isinstance(result, dict) or not result.get("ok"):
            count = result.get("count") if isinstance(result, dict) else 0
            raise RuntimeError(f"failed to open kuaishou card at index {index}, candidates={count}")

    def _apply_sort_and_filter(self, sort_by: str, time_range: str) -> None:
        page = self._require_page()

        if sort_by == "latest":
            self._click_first(self._selectors.get("sort_latest", ""), timeout=2500)
            self._sleep(700)

        if time_range in {"", "all", None}:  # type: ignore[arg-type]
            return

        filter_selector = self._selectors.get("filter_button", "")
        if filter_selector:
            self._click_first(filter_selector, timeout=2500)
            self._sleep(500)

        label_regex = {
            "day": "一天内|24小时|24 小时",
            "week": "一周内|7天内",
            "month": "一个月内|30天内",
            "year": "一年内|12个月内",
        }.get(time_range)

        if not label_regex:
            return

        try:
            page.locator("button,span,div").filter(has_text=re.compile(label_regex)).first.click(timeout=2000)
            self._sleep(600)
        except Exception:  # noqa: BLE001
            pass

    def _prepare_comment_input(self, comment_button: str) -> None:
        if comment_button:
            self._click_first(comment_button, timeout=2200)
            self._sleep(380)

        if self._has_visible(self._selectors.get("comment_input", ""), timeout=1000):
            return

        page = self._require_page()
        page.evaluate(
            """
            () => {
              const clickables = [
                ...Array.from(document.querySelectorAll("button,div,span,a")),
              ].filter((el) => {
                const text = String(el.innerText || '').trim();
                if (!text) return false;
                if (!/评论|说点什么/.test(text)) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 20 && rect.height > 12;
              });

              if (clickables.length) {
                clickables[0].click();
              }

              window.scrollBy(0, 600);
            }
            """
        )
        self._sleep(420)

    def _fill_by_keyboard(self, text: str) -> bool:
        page = self._require_page()
        try:
            page.keyboard.press("Tab")
            self._sleep(80)
            page.keyboard.press("Tab")
            self._sleep(80)
            page.keyboard.type(text, delay=28)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _fill_kuaishou_fallback(self, text: str) -> bool:
        page = self._require_page()
        result = page.evaluate(
            """
            (value) => {
              const candidates = Array.from(
                document.querySelectorAll("textarea,input,div[contenteditable='true'],[role='textbox']")
              );

              const isCommentInput = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                if (!rect || rect.width < 20 || rect.height < 12) return false;

                const hint = `${el.getAttribute('placeholder') || ''} ${el.getAttribute('aria-label') || ''}`;
                if (/说点什么|评论|回复/.test(hint)) return true;

                return !!el.closest('[class*=comment], [data-e2e*=comment], .side-area, .comment');
              };

              const input = candidates.find(isCommentInput);
              if (!input) return false;

              if (typeof input.focus === 'function') input.focus();

              if (input.isContentEditable) {
                input.textContent = value;
              } else {
                const tag = (input.tagName || '').toUpperCase();
                const proto = tag === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                const setter = desc && desc.set;
                if (setter) setter.call(input, value);
                else input.value = value;
              }

              input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
              input.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }
            """,
            text,
        )
        return bool(result)

    def _submit_kuaishou_fallback(self) -> bool:
        page = self._require_page()
        result = page.evaluate(
            """
            () => {
              const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 14 && rect.height > 14;
              };

              const disabled = (el) => {
                if (!el) return true;
                if (el.hasAttribute('disabled')) return true;
                if ((el.getAttribute('aria-disabled') || '').toLowerCase() === 'true') return true;
                if (el.classList && el.classList.contains('disabled')) return true;
                return false;
              };

              const direct = document.querySelector('.send-btn:not(.disabled):not([disabled]), button:not([disabled]).send-btn');
              if (direct && visible(direct) && !disabled(direct)) {
                direct.click();
                return true;
              }

              const nodes = Array.from(document.querySelectorAll('button,div,span,a'));
              for (const el of nodes) {
                if (!visible(el) || disabled(el)) continue;
                const text = String(el.innerText || '').trim();
                const cls = String(el.className || '');
                const aria = String(el.getAttribute('aria-label') || '');
                if (/发送|发布|评论/.test(text) || /send|publish|comment/i.test(cls) || /发送|发布/.test(aria)) {
                  el.click();
                  return true;
                }
              }
              return false;
            }
            """
        )
        return bool(result)

    def _submit_by_keyboard(self) -> bool:
        page = self._require_page()
        try:
            page.keyboard.press("Enter")
            self._sleep(260)
            page.keyboard.press("Control+Enter")
            self._sleep(260)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _extract_post_id(self, url: str) -> str:
        patterns = [
            r"/short-video/([0-9A-Za-z_-]+)",
            r"/video/([0-9A-Za-z_-]+)",
            r"[?&]photoId=([0-9A-Za-z_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_search_keyword_from_url(url: str) -> str:
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            query = dict(parse_qsl(parsed.query))
            return str(query.get("searchKey") or "").strip()
        except Exception:
            return ""

    def _parse_card_post_id(self, post_id: str) -> Optional[dict]:
        match = re.match(r"^kscard:([^:]+):(\d+)$", post_id or "")
        if not match:
            return None
        return {"keyword": unquote(match.group(1)), "index": int(match.group(2))}

    def _split_candidates(self, selector: str) -> List[str]:
        if not selector:
            return []
        return [item.strip() for item in selector.split(",") if item.strip()]

    def _has_visible(self, selector: str, timeout: int = 1200) -> bool:
        page = self._require_page()
        if not selector:
            return False
        for candidate in self._split_candidates(selector):
            try:
                if page.locator(candidate).first.is_visible(timeout=timeout):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_first(self, selector: str, timeout: int = 3000) -> bool:
        page = self._require_page()
        for candidate in self._split_candidates(selector):
            try:
                loc = page.locator(candidate).first
                loc.wait_for(state="visible", timeout=timeout)
                loc.click(timeout=timeout)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_submit_first(self, selector: str, timeout: int = 3200) -> bool:
        page = self._require_page()
        for candidate in self._split_candidates(selector):
            try:
                loc = page.locator(candidate).first
                loc.wait_for(state="visible", timeout=timeout)
                disabled = bool(
                    loc.evaluate(
                        """
                        (el) => {
                          if (!el) return true;
                          if (el.hasAttribute('disabled')) return true;
                          if ((el.getAttribute('aria-disabled') || '').toLowerCase() === 'true') return true;
                          if (el.classList && el.classList.contains('disabled')) return true;
                          return false;
                        }
                        """
                    )
                )
                if disabled:
                    continue
                loc.click(timeout=timeout)
                return True
            except Exception:
                continue
        return False

    def _confirm_comment_submitted(self, comment_text: str, responses: List[Tuple[int, str, str]]) -> bool:
        # 1. Network signal: any 2xx POST to a comment-related URL
        success_by_post_api = any(
            200 <= status < 300 and method == "POST" and "comment" in url
            for status, method, url in responses
        )
        if success_by_post_api:
            return True

        page = self._require_page()
        snippet = normalize_spaces(comment_text)[:10]
        if not snippet:
            return False

        still_in_input = bool(
            page.evaluate(
                """
                (needle) => {
                  const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
                  const v = norm(needle);
                  if (!v) return false;

                  const nodes = [
                    ...document.querySelectorAll("textarea,input[placeholder*='说点什么'],div[contenteditable='true']"),
                  ];
                  for (const el of nodes) {
                    const rect = el.getBoundingClientRect();
                    if (!rect || rect.width < 20 || rect.height < 12) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const content = norm(el.isContentEditable ? el.textContent : el.value);
                    if (content && content.includes(v)) return true;
                  }
                  return false;
                }
                """,
                snippet,
            )
        )
        if still_in_input:
            return False

        # 2. Text is gone from input — treat as submitted (toast may have already faded)
        return True

    def _fill_first(self, selector: str, text: str, timeout: int = 4000) -> bool:
        page = self._require_page()
        for candidate in self._split_candidates(selector):
            loc = page.locator(candidate).first
            try:
                loc.wait_for(state="visible", timeout=timeout)
                is_comment_box = bool(
                    loc.evaluate(
                        """
                        (el) => {
                          if (!el) return false;
                          const text = `${el.getAttribute('placeholder') || ''} ${el.getAttribute('aria-label') || ''}`;
                          if (/说点什么|评论|回复/.test(text)) return true;
                          const panel = el.closest('[class*=comment], [data-e2e*=comment], .side-area, .comment');
                          return !!panel;
                        }
                        """
                    )
                )
                if not is_comment_box:
                    continue

                loc.click(timeout=timeout)
                tag_name = (loc.evaluate("(el) => el.tagName.toLowerCase()") or "").strip()
                if tag_name in {"input", "textarea"}:
                    loc.fill("")
                    loc.type(text, delay=26)
                    loc.evaluate(
                        """
                        (el, value) => {
                          const proto = el.tagName === 'TEXTAREA'
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                          if (setter) setter.call(el, value);
                          else el.value = value;
                          el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                          el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        """,
                        text,
                    )
                else:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.type(text, delay=30)
                return True
            except PlaywrightTimeoutError:
                continue
            except Exception:  # noqa: BLE001
                continue
        return False

    def _sleep(self, milliseconds: int) -> None:
        time.sleep(max(0, milliseconds) / 1000.0)

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("browser page not initialized, call start() first")
        return self._page

    def get_current_url(self) -> str:
        page = self._require_page()
        return page.url or ""

    def extract_post_id_from_url(self, url: str) -> str:
        return self._extract_post_id(url)
