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
        self._current_post_signature: Optional[str] = None
        self._autoplay_checked_signature: Optional[str] = None

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
        self._clear_post_state()

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
        self._clear_post_state()

        self._apply_sort_and_filter(sort_by=sort_by, time_range=time_range)
        self._sleep(self._cfg.post_load_wait_ms)

        posts = self._collect_posts(keyword, limit)
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
                    self._recover_comment_entry(post, comment_button, attempt)
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
                        self._recover_comment_entry(post, comment_button, attempt)
                        continue
                    raise RuntimeError("comment submit button not found for kuaishou")

                self._sleep(2200)
            finally:
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass

            if self._confirm_comment_submitted(comment_text, responses):
                self._close_video_overlay(post)
                return

            self._logger.warning(
                "comment submit not confirmed, retry attempt=%s post_id=%s responses=%s",
                attempt,
                post.post_id,
                responses[:5],
            )
            if attempt < max_attempts:
                self._recover_comment_entry(post, comment_button, attempt, reopen=True)

        raise RuntimeError("comment submit not confirmed")

    def open_post(self, post: Post, force: bool = False) -> None:
        page = self._require_page()
        if not force and self._current_post_matches(post) and self._is_video_overlay_open():
            self._ensure_post_ready(post)
            return

        card = self._parse_card_post_id(post.post_id)
        if card:
            keyword = post.search_keyword or card["keyword"]
            search_url = f"https://www.kuaishou.com/search/video?searchKey={quote(keyword)}"

            current_url_keyword = self._extract_search_keyword_from_url(page.url)
            on_search_page = "/search/video" in (page.url or "")
            overlay_open = self._is_video_overlay_open()

            need_refresh = (
                force
                or self._cfg.search_each_post
                or (self._current_search_keyword != keyword)
                or (not on_search_page)
                or (current_url_keyword != keyword)
                or overlay_open
            )
            if overlay_open and (force or not self._current_post_matches(post)):
                self._close_video_overlay(post)
                overlay_open = False
            if need_refresh:
                page.goto(search_url, wait_until="domcontentloaded")
                self._sleep(self._cfg.post_load_wait_ms)
                self._current_search_keyword = keyword

            try:
                self._open_kuaishou_card(post)
            except Exception:
                page.goto(search_url, wait_until="domcontentloaded")
                self._sleep(self._cfg.post_load_wait_ms)
                self._current_search_keyword = keyword
                self._open_kuaishou_card(post)

            self._sleep(self._cfg.post_load_wait_ms)
            self._mark_post_open(post)
            self._ensure_post_ready(post)
            return

        if post.url:
            if force and self._is_video_overlay_open():
                self._close_video_overlay(post)
            page.goto(post.url, wait_until="domcontentloaded")
            self._sleep(self._cfg.post_load_wait_ms)
            self._mark_post_open(post)
            self._ensure_post_ready(post)
            self._current_search_keyword = None
            return

        if force and self._is_video_overlay_open():
            self._close_video_overlay(post)
        page.goto(f"https://www.kuaishou.com/short-video/{post.post_id}", wait_until="domcontentloaded")
        self._sleep(self._cfg.post_load_wait_ms)
        self._mark_post_open(post)
        self._ensure_post_ready(post)
        self._current_search_keyword = None

    def _recover_comment_entry(self, post: Post, comment_button: str, attempt: int, reopen: bool = False) -> None:
        if not reopen and attempt == 1 and self._current_post_matches(post) and self._is_video_overlay_open():
            self._prepare_comment_input(comment_button)
            self._sleep(420)
            return
        self.open_post(post, force=True)

    def _mark_post_open(self, post: Post) -> None:
        self._current_post_signature = self._post_signature(post)

    def _clear_post_state(self) -> None:
        self._current_post_signature = None
        self._autoplay_checked_signature = None

    def _current_post_matches(self, post: Optional[Post]) -> bool:
        return bool(post and self._current_post_signature and self._current_post_signature == self._post_signature(post))

    def _post_signature(self, post: Post) -> str:
        if post.post_id:
            return f"post:{post.post_id}"
        if post.url:
            return f"url:{post.url}"
        hint = normalize_spaces(post.locator_hint or post.title)
        return f"hint:{short_hash(hint)}"

    def _ensure_post_ready(self, post: Post) -> None:
        signature = self._post_signature(post)
        if self._autoplay_checked_signature == signature:
            return
        self._disable_autoplay_if_enabled()
        self._autoplay_checked_signature = signature

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

    def _close_video_overlay(self, post: Optional[Post] = None) -> bool:
        page = self._require_page()
        if not self._is_video_overlay_open():
            self._clear_post_state()
            return True

        if self._close_overlay_via_button():
            self._sleep(420)
            if not self._is_video_overlay_open():
                self._clear_post_state()
                return True

        try:
            page.keyboard.press("Escape")
            self._sleep(420)
            if not self._is_video_overlay_open():
                self._clear_post_state()
                return True
        except Exception:
            pass

        search_keyword = self._search_keyword_for_post(post)
        if self._return_to_search_results(search_keyword):
            self._clear_post_state()
            return True

        try:
            page.go_back(wait_until="domcontentloaded", timeout=min(4500, self._cfg.navigation_timeout_ms))
            self._sleep(self._cfg.post_load_wait_ms)
            if not self._is_video_overlay_open():
                self._clear_post_state()
                return True
        except Exception:
            pass

        closed = not self._is_video_overlay_open()
        if closed:
            self._clear_post_state()
        return closed

    def _disable_autoplay_if_enabled(self) -> None:
        page = self._require_page()
        last_state: Optional[dict] = None
        for attempt in range(1, 4):
            self._reveal_video_controls()
            state = self._autoplay_control_state()
            last_state = state

            if not state.get("found"):
                self._sleep(180)
                continue

            if not state.get("enabled"):
                return

            if state.get("mode") == "exact-dom":
                exact_clicked = self._click_exact_autoplay_toggle()
                if exact_clicked:
                    self._sleep(360)
                    self._reveal_video_controls()
                    verify = self._autoplay_control_state()
                    last_state = verify
                    if verify.get("found") and not verify.get("enabled"):
                        self._logger.info(
                            "[BROWSER] autoplay disabled | label=%s via=%s",
                            state.get("label") or "",
                            "exact-dom-click",
                        )
                        self._sleep(220)
                        return
            point = self._pick_autoplay_point(state)
            if not point and state.get("mode") == "exact-dom":
                fallback_state = self._autoplay_control_state(allow_exact=False)
                if fallback_state.get("found") and fallback_state.get("enabled"):
                    point = self._pick_autoplay_point(fallback_state)
                    if point:
                        state = fallback_state

            if point:
                try:
                    x = float(point.get("x"))
                    y = float(point.get("y"))
                except (TypeError, ValueError):
                    point = None

            if point:
                kind = str(point.get("kind") or "unknown")
                self._logger.info(
                    "[BROWSER] autoplay on, click toggle | attempt=%s label=%s via=%s",
                    attempt,
                    state.get("label") or "",
                    kind,
                )
                try:
                    page.mouse.click(x, y)
                except Exception:
                    continue

                self._sleep(260)
                self._reveal_video_controls()
                verify = self._autoplay_control_state()
                last_state = verify
                if verify.get("found") and not verify.get("enabled"):
                    self._logger.info(
                        "[BROWSER] autoplay disabled | label=%s via=%s",
                        state.get("label") or "",
                        kind,
                    )
                    self._sleep(220)
                    return

            shortcut_clicked = self._toggle_autoplay_via_shortcut()
            if shortcut_clicked:
                self._sleep(320)
                self._reveal_video_controls()
                verify = self._autoplay_control_state()
                last_state = verify
                if verify.get("found") and not verify.get("enabled"):
                    self._logger.info(
                        "[BROWSER] autoplay disabled | label=%s via=%s",
                        state.get("label") or "",
                        "shortcut-k",
                    )
                    self._sleep(220)
                    return

            self._sleep(180)

        if last_state and last_state.get("found") and last_state.get("enabled"):
            self._logger.warning(
                "[BROWSER] autoplay toggle still enabled after retries | label=%s",
                last_state.get("label") or "",
            )

    def _pick_autoplay_point(self, state: Optional[dict]) -> Optional[dict]:
        if not state:
            return None
        points = list(state.get("points") or [])
        if not points:
            return None

        priority = {
            "exact-switch-center": 0,
            "exact-button-center": 1,
            "toggle-center": 2,
            "exact-switch-left": 3,
            "exact-button-left": 4,
            "label-left": 5,
            "label-center": 6,
            "container-center": 7,
        }
        ranked = sorted(
            points,
            key=lambda point: priority.get(str(point.get("kind") or ""), 99),
        )
        return ranked[0] if ranked else None

    def _click_exact_autoplay_toggle(self) -> bool:
        page = self._require_page()
        try:
            result = page.evaluate(
                r"""
                () => {
                  const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                  const fire = (node, type, init = {}) => {
                    const EventCtor = type.startsWith('pointer') ? window.PointerEvent : window.MouseEvent;
                    node.dispatchEvent(new EventCtor(type, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                      pointerType: 'mouse',
                      ...init,
                    }));
                  };
                  const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    if (!rect || rect.width < 8 || rect.height < 8) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                  };

                  const roots = Array.from(document.querySelectorAll(".hover-tip.autoPlay, .autoPlay"));
                  for (const root of roots) {
                    if (!isVisible(root)) continue;
                    const button = root.querySelector('.auto-play-btn');
                    if (!button || !isVisible(button)) continue;

                    const switchEl = button.querySelector("[role='switch'], .toggle-switch");
                    const hoverName = norm(root.querySelector('.hover-content .name')?.textContent || '');
                    const ariaChecked = switchEl ? String(switchEl.getAttribute('aria-checked') || '').toLowerCase() : '';
                    const enabled = hoverName === '关闭连播' || hoverName === '关闭自动连播' || ariaChecked === 'true';
                    if (!enabled) continue;

                    button.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                    const rect = button.getBoundingClientRect();
                    const clientX = rect.left + rect.width / 2;
                    const clientY = rect.top + rect.height / 2;
                    const target =
                      document.elementFromPoint(clientX, clientY)?.closest('.auto-play-btn, [role="switch"], .toggle-switch')
                      || button;

                    ['pointerenter', 'mouseenter', 'pointermove', 'mousemove', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']
                      .forEach((eventName) => fire(target, eventName, { clientX, clientY }));

                    if (typeof target.click === 'function') {
                      target.click();
                    }
                    return true;
                  }
                  return false;
                }
                """
            )
            return bool(result)
        except Exception:
            return False

    def _toggle_autoplay_via_shortcut(self) -> bool:
        page = self._require_page()
        try:
            page.keyboard.press("KeyK")
            return True
        except Exception:
            return False

    def _reveal_video_controls(self) -> None:
        page = self._require_page()
        try:
            viewport = page.viewport_size or {}
            if not viewport:
                viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })") or {}
            width = int(viewport.get("width") or 0)
            height = int(viewport.get("height") or 0)
            if width <= 0 or height <= 0:
                return
            x = max(80, int(width * 0.68))
            y = max(40, int(height - 38))
            page.mouse.move(x, y)
            self._sleep(120)
            page.mouse.move(max(40, x - 26), y)
            self._sleep(120)
        except Exception:
            return

    def _autoplay_control_state(self, allow_exact: bool = True) -> dict:
        return self._autoplay_control_state_internal(allow_exact=allow_exact)

    def _is_video_overlay_open(self) -> bool:
        page = self._require_page()
        try:
            result = page.evaluate(
                r"""
                () => {
                  const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    if (!rect || rect.width < 12 || rect.height < 12) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                  };

                  if (/\/short-video\/|\/video\//.test(window.location.pathname) && !/\/search\/video/.test(window.location.pathname)) {
                    return true;
                  }

                  const selectors = [
                    '.hover-tip.autoPlay',
                    '.auto-play-btn',
                    '.side-area',
                    '[class*="comment-panel"]',
                    '[class*="commentPanel"]',
                  ];
                  for (const selector of selectors) {
                    if (Array.from(document.querySelectorAll(selector)).some(isVisible)) return true;
                  }

                  return Array.from(
                    document.querySelectorAll("textarea,input[placeholder*='说点什么'],input[placeholder*='评论'],div[contenteditable='true'],[role='textbox']")
                  ).some((el) => {
                    if (!isVisible(el)) return false;
                    return !!el.closest('.side-area, .tab-nav-wrapper, [class*=comment], [data-e2e*=comment]');
                  });
                }
                """
            )
            return bool(result)
        except Exception:
            url = page.url or ""
            return "/search/video" not in url

    def _search_keyword_for_post(self, post: Optional[Post] = None) -> str:
        if post:
            if post.search_keyword:
                return str(post.search_keyword).strip()
            card = self._parse_card_post_id(post.post_id)
            if card:
                return str(card.get("keyword") or "").strip()
            from_post_url = self._extract_search_keyword_from_url(post.url)
            if from_post_url:
                return from_post_url

        page = self._require_page()
        current_url_keyword = self._extract_search_keyword_from_url(page.url)
        if current_url_keyword:
            return current_url_keyword
        return str(self._current_search_keyword or "").strip()

    def _close_overlay_via_button(self) -> bool:
        page = self._require_page()
        try:
            result = page.evaluate(
                r"""
                () => {
                  const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                  const isVisible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    if (!rect || rect.width < 14 || rect.height < 14) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                  };

                  const nodes = Array.from(document.querySelectorAll("button,[role='button'],div,span,a"));
                  const candidates = nodes
                    .filter((el) => {
                      if (!isVisible(el)) return false;
                      const rect = el.getBoundingClientRect();
                      if (rect.left > window.innerWidth * 0.22) return false;
                      if (rect.top > window.innerHeight * 0.22) return false;
                      if (rect.width > 96 || rect.height > 96) return false;
                      const text = norm(el.innerText || el.textContent || '');
                      const meta = `${text} ${el.className || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`;
                      if (/关闭|返回|back|close|icon-close|btn-close/i.test(meta)) return true;
                      if (text === '×' || text === '✕' || text === '╳' || text === '关闭') return true;
                      return !!el.querySelector('svg') && rect.left < 80 && rect.top < 80;
                    })
                    .sort((a, b) => {
                      const ra = a.getBoundingClientRect();
                      const rb = b.getBoundingClientRect();
                      return (ra.left + ra.top * 2) - (rb.left + rb.top * 2);
                    });

                  const target = candidates[0];
                  if (!target) return false;
                  if (typeof target.click === 'function') target.click();
                  else target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                  return true;
                }
                """
            )
            return bool(result)
        except Exception:
            return False

    def _return_to_search_results(self, search_keyword: str) -> bool:
        keyword = (search_keyword or "").strip()
        if not keyword:
            return False

        page = self._require_page()
        try:
            page.goto(f"https://www.kuaishou.com/search/video?searchKey={quote(keyword)}", wait_until="domcontentloaded")
            self._sleep(self._cfg.post_load_wait_ms)
            self._current_search_keyword = keyword
            return not self._is_video_overlay_open()
        except Exception:
            return False

    def _autoplay_control_state_internal(self, allow_exact: bool = True) -> dict:
        page = self._require_page()
        result = page.evaluate(
            r"""
            ({ allowExact }) => {
              const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const isVisible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (!rect || rect.width < 8 || rect.height < 8) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
              };
              const isRedLike = (value) => {
                const text = String(value || '');
                const match = text.match(/rgba?\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
                if (!match) return false;
                const r = Number(match[1]);
                const g = Number(match[2]);
                const b = Number(match[3]);
                return r >= 170 && g <= 145 && b <= 160;
              };
              const isActiveNode = (el, labelText = '') => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                const stateText = [
                  el.getAttribute('aria-checked') || '',
                  el.getAttribute('aria-pressed') || '',
                  el.getAttribute('data-checked') || '',
                  el.getAttribute('data-selected') || '',
                  el.getAttribute('data-state') || '',
                  String(el.className || ''),
                  labelText,
                ].join(' ');
                if (/\b(true|checked|selected|active|open|on|enabled|is-active|is-selected|is-on|switch-on)\b/i.test(stateText)) {
                  return true;
                }
                if (/关闭连播|关闭自动连播|停止连播|取消连播|连播中|自动连播中|已开启连播|连播已开/.test(stateText)) {
                  return true;
                }
                return (
                  isRedLike(style.backgroundColor)
                  || isRedLike(style.borderColor)
                  || isRedLike(style.color)
                  || isRedLike(style.boxShadow)
                );
              };
              const point = (x, y, kind) => ({
                x: Number(x.toFixed(1)),
                y: Number(y.toFixed(1)),
                kind,
              });
              const bottomThreshold = window.innerHeight * 0.72;
              const pushUniq = (items, node) => {
                if (node && !items.includes(node)) items.push(node);
              };
              if (allowExact) {
                const exactRoots = Array.from(document.querySelectorAll(".hover-tip.autoPlay, .autoPlay"));
                for (const root of exactRoots) {
                  if (!isVisible(root)) continue;

                  const button = root.querySelector('.auto-play-btn') || root;
                  if (!isVisible(button)) continue;

                  const switchEl = button.querySelector("[role='switch'], .toggle-switch");
                  const labelEl = Array.from(button.querySelectorAll('span')).find((node) => {
                    const text = norm(node.innerText || node.textContent || '');
                    return text === '连播' || text === '自动连播';
                  }) || button;
                  const hoverName = norm(root.querySelector('.hover-content .name')?.textContent || '');
                  const labelText = norm(labelEl.innerText || labelEl.textContent || '') || '连播';
                  const buttonRect = button.getBoundingClientRect();
                  const ariaChecked = switchEl ? String(switchEl.getAttribute('aria-checked') || '').toLowerCase() : '';
                  const ariaDisabled = switchEl ? String(switchEl.getAttribute('aria-disabled') || '').toLowerCase() : '';
                  const enabledByHover = hoverName === '关闭连播' || hoverName === '关闭自动连播';
                  const disabledByHover = hoverName === '开启连播' || hoverName === '开启自动连播';
                  let enabled = false;
                  if (disabledByHover) {
                    enabled = false;
                  } else if (enabledByHover) {
                    enabled = true;
                  } else if (ariaChecked === 'false') {
                    enabled = false;
                  } else if (ariaChecked === 'true') {
                    enabled = true;
                  } else {
                    enabled = isActiveNode(switchEl || button, labelText);
                  }

                  const points = [];
                  const seenPoints = new Set();
                  const addPoint = (x, y, kind) => {
                    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
                    const key = `${Math.round(x)}:${Math.round(y)}:${kind}`;
                    if (seenPoints.has(key)) return;
                    seenPoints.add(key);
                    points.push(point(x, y, kind));
                  };

                  addPoint(buttonRect.left + buttonRect.width / 2, buttonRect.top + buttonRect.height / 2, 'exact-button-center');
                  addPoint(
                    buttonRect.left + Math.min(24, Math.max(14, buttonRect.height * 0.95)),
                    buttonRect.top + buttonRect.height / 2,
                    'exact-button-left',
                  );

                  if (switchEl && isVisible(switchEl) && ariaDisabled !== 'true') {
                    const switchRect = switchEl.getBoundingClientRect();
                    addPoint(switchRect.left + switchRect.width / 2, switchRect.top + switchRect.height / 2, 'exact-switch-center');
                    addPoint(switchRect.left + Math.min(16, Math.max(10, switchRect.width * 0.38)), switchRect.top + switchRect.height / 2, 'exact-switch-left');
                  }

                  return {
                    found: true,
                    label: labelText,
                    enabled,
                    points,
                    mode: 'exact-dom',
                    hoverName,
                    ariaChecked,
                    ariaDisabled,
                  };
                }
              }

              const speedNodes = Array.from(document.querySelectorAll("button,[role='button'],div,span"))
                .filter((el) => {
                  if (!isVisible(el)) return false;
                  const rect = el.getBoundingClientRect();
                  if (rect.top < bottomThreshold) return false;
                  return norm(el.innerText || el.textContent || '') === '倍速';
                });

              const labelCandidates = Array.from(document.querySelectorAll("button,[role='button'],[role='switch'],div,span"))
                .filter((el) => {
                  if (!isVisible(el)) return false;
                  const rect = el.getBoundingClientRect();
                  if (rect.top < bottomThreshold) return false;
                  const text = norm(el.innerText || el.textContent || '');
                  if (!text || text.length > 12) return false;
                  return text === '连播' || text === '自动连播' || /^连播\b/.test(text);
                })
                .map((el) => ({ el, text: norm(el.innerText || el.textContent || '') }));

              labelCandidates.sort((a, b) => {
                if (!speedNodes.length) return 0;
                const score = (item) => {
                  const rect = item.el.getBoundingClientRect();
                  const cx = rect.left + rect.width / 2;
                  const cy = rect.top + rect.height / 2;
                  let best = Number.POSITIVE_INFINITY;
                  for (const speedEl of speedNodes) {
                    const speedRect = speedEl.getBoundingClientRect();
                    const sx = speedRect.left + speedRect.width / 2;
                    const sy = speedRect.top + speedRect.height / 2;
                    best = Math.min(best, Math.abs(cx - sx) + Math.abs(cy - sy) * 4);
                  }
                  return best;
                };
                return score(a) - score(b);
              });

              for (const item of labelCandidates) {
                const labelEl = item.el;
                const labelRect = labelEl.getBoundingClientRect();
                const labelMidY = labelRect.top + labelRect.height / 2;
                const container =
                  labelEl.closest("button,[role='button'],[role='switch']")
                  || labelEl.parentElement
                  || labelEl;
                const nearby = [];
                pushUniq(nearby, labelEl);
                pushUniq(nearby, container);
                pushUniq(nearby, labelEl.previousElementSibling);
                pushUniq(nearby, labelEl.nextElementSibling);
                pushUniq(nearby, container.previousElementSibling);
                pushUniq(nearby, container.nextElementSibling);
                pushUniq(nearby, container.parentElement);
                Array.from(container.querySelectorAll('*')).slice(0, 24).forEach((node) => pushUniq(nearby, node));
                if (container.parentElement) {
                  Array.from(container.parentElement.children).slice(0, 12).forEach((node) => pushUniq(nearby, node));
                }

                const toggleNodes = nearby
                  .filter((node) => {
                    if (!isVisible(node)) return false;
                    const rect = node.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    if (Math.abs(midY - labelMidY) > 30) return false;
                    if (rect.width < 16 || rect.width > 96 || rect.height < 10 || rect.height > 42) return false;
                    if (rect.left > labelRect.left + 16) return false;
                    const text = norm(node.innerText || node.textContent || '');
                    return !text || text.length <= 4;
                  })
                  .sort((a, b) => {
                    const ra = a.getBoundingClientRect();
                    const rb = b.getBoundingClientRect();
                    const targetLeft = labelRect.left - 8;
                    const scoreA = Math.abs((ra.left + ra.width / 2) - targetLeft) - (isActiveNode(a, item.text) ? 18 : 0);
                    const scoreB = Math.abs((rb.left + rb.width / 2) - targetLeft) - (isActiveNode(b, item.text) ? 18 : 0);
                    return scoreA - scoreB;
                  });

                const enabled = nearby.some((node) => isActiveNode(node, item.text));
                const points = [];
                const seenPoints = new Set();
                const addPoint = (x, y, kind) => {
                  const key = `${Math.round(x)}:${Math.round(y)}:${kind}`;
                  if (seenPoints.has(key)) return;
                  seenPoints.add(key);
                  points.push(point(x, y, kind));
                };

                if (toggleNodes.length) {
                  const rect = toggleNodes[0].getBoundingClientRect();
                  addPoint(rect.left + rect.width / 2, rect.top + rect.height / 2, 'toggle-center');
                }

                if (labelRect.left > 24) {
                  addPoint(labelRect.left - Math.min(30, Math.max(18, labelRect.height * 1.35)), labelMidY, 'label-left');
                }

                addPoint(labelRect.left + labelRect.width / 2, labelMidY, 'label-center');

                const containerRect = container.getBoundingClientRect();
                if (containerRect.width > 20 && containerRect.width < 180) {
                  addPoint(containerRect.left + containerRect.width / 2, containerRect.top + containerRect.height / 2, 'container-center');
                }

                return {
                  found: true,
                  label: item.text,
                  enabled,
                  points,
                };
              }

              return {
                found: false,
                enabled: false,
                labels: labelCandidates.slice(0, 3).map((item) => item.text),
              };
            }
            """
            ,
            {"allowExact": allow_exact},
        )
        if isinstance(result, dict):
            return result
        return {"found": False, "enabled": False}

    def _collect_posts(self, keyword: str, limit: int) -> List[Post]:
        page = self._require_page()
        for _ in range(2):
            self._scroll_search_results(1200, 420)

        selector = self._selectors.get("post_link", "a[href*='/short-video/'], a[href*='/video/'], a[href*='photoId=']")
        result = page.evaluate(
            r"""
            ({ selector, maxCount }) => {
              const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
              const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (!rect || rect.width < 16 || rect.height < 16) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
              };

              const anchors = Array.from(document.querySelectorAll(selector))
                .filter(visible)
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  return {
                    href: el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('data-url') || '',
                    text: norm(el.innerText || ''),
                    parentText: norm(el.closest('.photo-card, a, div')?.innerText || ''),
                    top: Number(rect.top.toFixed(1)),
                    left: Number(rect.left.toFixed(1)),
                  };
                })
                .filter((item) => item.href);

              anchors.sort((a, b) => (a.top - b.top) || (a.left - b.left));
              return anchors.slice(0, maxCount);
            }
            """,
            {"selector": selector, "maxCount": min(max(limit * 5, 80), 240)},
        )
        if not isinstance(result, list):
            return []

        posts: List[Post] = []
        seen = set()
        for item in result:
            if not isinstance(item, dict):
                continue
            href = str(item.get("href") or "").strip()
            if not href:
                continue
            abs_url = urljoin(page.url, href)
            post_id = self._extract_post_id(abs_url) or f"ksurl:{short_hash(abs_url)}"
            if post_id in seen:
                continue
            title = normalize_spaces(str(item.get("text") or item.get("parentText") or ""))
            posts.append(
                Post(
                    post_id=post_id,
                    title=title[:200],
                    url=abs_url,
                    search_keyword=keyword,
                    locator_hint=title[:120],
                    rank=len(posts),
                )
            )
            seen.add(post_id)
            if len(posts) >= limit:
                break

        if posts:
            self._reset_search_results_view()
        return posts

    def _collect_cards(self, keyword: str, limit: int) -> List[Post]:
        page = self._require_page()
        posts: List[Post] = []
        seen_keys = set()
        last_fingerprint = ""
        stagnant_rounds = 0
        max_scroll_rounds = max(4, min(20, limit + 8))

        for _ in range(max_scroll_rounds):
            batch = self._scan_search_cards(keyword, max_count=max(limit * 4, 80))
            fingerprint = "|".join(item["text"][:48] for item in batch[:6])
            if fingerprint == last_fingerprint:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0
                last_fingerprint = fingerprint

            for item in batch:
                title = normalize_spaces(str(item.get("text") or ""))
                if not title:
                    continue
                card_key = short_hash(f"{keyword}|{title}")
                if card_key in seen_keys:
                    continue
                seen_keys.add(card_key)
                posts.append(
                    Post(
                        post_id=f"kscard:{quote(keyword)}:{card_key}",
                        title=title[:200],
                        url=f"{page.url}#card-{card_key}",
                        search_keyword=keyword,
                        locator_hint=title[:120],
                        rank=len(posts),
                    )
                )
                if len(posts) >= limit:
                    break

            if len(posts) >= limit or stagnant_rounds >= 2:
                break

            self._scroll_search_results()

        if posts:
            self._reset_search_results_view()
        return posts

    def _open_kuaishou_card(self, post: Post) -> None:
        hint = normalize_spaces(post.locator_hint or post.title)
        keyword = post.search_keyword or self._search_keyword_for_post(post)
        if not hint:
            raise RuntimeError("failed to open kuaishou card: empty locator hint")

        max_scroll_rounds = max(4, min(20, (post.rank if post.rank >= 0 else 0) + 6))
        last_fingerprint = ""
        stagnant_rounds = 0

        for _ in range(max_scroll_rounds):
            result = self._click_search_card(keyword, hint)
            if result.get("ok"):
                return

            visible = result.get("visible") or []
            fingerprint = "|".join(str(item)[:40] for item in visible[:6])
            if fingerprint == last_fingerprint:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0
                last_fingerprint = fingerprint

            if stagnant_rounds >= 2:
                break

            self._scroll_search_results()

        raise RuntimeError(f"failed to open kuaishou card hint={hint[:48]!r}")

    def _scan_search_cards(self, keyword: str, max_count: int = 120) -> List[dict]:
        page = self._require_page()
        result = page.evaluate(
            r"""
            ({ q, maxCount }) => {
              const normalizedQuery = (q || '').toLowerCase().trim();
              const bad = /(www\.kuaishou\.com|京ICP备|京公网安备|违法和不良信息举报|举报专区|未成年人关怀热线|可灵AI|Acfun|推荐\s*发现\s*关注\s*直播\s*赛事)/i;
              const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
              const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (!rect || rect.width < 120 || rect.height < 80) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
              };

              const preferred = Array.from(document.querySelectorAll('main .photo-card, main div.photo-card'));
              const sourceNodes = preferred.length ? preferred : Array.from(document.querySelectorAll('main div'));
              const seen = new Set();
              const matched = [];
              const fallback = [];

              for (const el of sourceNodes) {
                if (!visible(el) || !el.querySelector('img')) continue;
                const style = window.getComputedStyle(el);
                const clickable = style.cursor === 'pointer' || el.getAttribute('role') === 'button' || !!el.onclick || el.classList.contains('photo-card');
                if (!clickable) continue;

                const text = norm(el.innerText);
                if (!text || text.length < 8 || text.length > 220 || bad.test(text)) continue;
                if (seen.has(text)) continue;
                seen.add(text);

                const rect = el.getBoundingClientRect();
                const item = {
                  text: text.slice(0, 200),
                  top: Number(rect.top.toFixed(1)),
                  left: Number(rect.left.toFixed(1)),
                  width: Number(rect.width.toFixed(1)),
                  height: Number(rect.height.toFixed(1)),
                };
                if (normalizedQuery && text.toLowerCase().includes(normalizedQuery)) matched.push(item);
                else fallback.push(item);

                if (matched.length + fallback.length >= maxCount * 2) break;
              }

              const combined = [...matched, ...fallback];
              combined.sort((a, b) => (a.top - b.top) || (a.left - b.left));
              return combined.slice(0, maxCount);
            }
            """,
            {"q": keyword, "maxCount": max(1, max_count)},
        )
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def _click_search_card(self, keyword: str, hint: str) -> dict:
        page = self._require_page()
        result = page.evaluate(
            r"""
            ({ q, hint }) => {
              const normalizedQuery = (q || '').toLowerCase().trim();
              const normalizedHint = String(hint || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const hintPrefix = normalizedHint.slice(0, Math.min(32, normalizedHint.length));
              const bad = /(www\.kuaishou\.com|京ICP备|京公网安备|违法和不良信息举报|举报专区|未成年人关怀热线|可灵AI|Acfun|推荐\s*发现\s*关注\s*直播\s*赛事)/i;
              const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
              const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (!rect || rect.width < 120 || rect.height < 80) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
              };

              const preferred = Array.from(document.querySelectorAll('main .photo-card, main div.photo-card'));
              const sourceNodes = preferred.length ? preferred : Array.from(document.querySelectorAll('main div'));
              const seen = new Set();
              const candidates = [];

              for (const el of sourceNodes) {
                if (!visible(el) || !el.querySelector('img')) continue;
                const style = window.getComputedStyle(el);
                const clickable = style.cursor === 'pointer' || el.getAttribute('role') === 'button' || !!el.onclick || el.classList.contains('photo-card');
                if (!clickable) continue;

                const text = norm(el.innerText);
                if (!text || text.length < 8 || text.length > 220 || bad.test(text)) continue;
                if (seen.has(text)) continue;
                seen.add(text);

                const lower = text.toLowerCase();
                let score = 99;
                if (normalizedHint && lower === normalizedHint) score = 0;
                else if (normalizedHint && lower.includes(normalizedHint)) score = 1;
                else if (normalizedHint && normalizedHint.includes(lower)) score = 2;
                else if (hintPrefix && lower.includes(hintPrefix)) score = 3;
                else if (normalizedQuery && lower.includes(normalizedQuery) && hintPrefix) score = 8;
                else if (normalizedQuery && lower.includes(normalizedQuery)) score = 10;

                const rect = el.getBoundingClientRect();
                candidates.push({ el, text, score, top: rect.top, left: rect.left });
              }

              candidates.sort((a, b) => (a.score - b.score) || (a.top - b.top) || (a.left - b.left));
              const target = candidates[0];
              if (!target || target.score >= 20) {
                return { ok: false, visible: candidates.slice(0, 6).map((item) => item.text.slice(0, 80)) };
              }

              target.el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' });
              if (typeof target.el.click === 'function') target.el.click();
              else target.el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));

              return { ok: true, text: target.text.slice(0, 120), score: target.score };
            }
            """,
            {"q": keyword, "hint": hint},
        )
        if isinstance(result, dict):
            return result
        return {"ok": False, "visible": []}

    def _scroll_search_results(self, distance: int = 1400, settle_ms: int = 650) -> None:
        page = self._require_page()
        page.mouse.wheel(0, distance)
        self._sleep(settle_ms)

    def _reset_search_results_view(self) -> None:
        page = self._require_page()
        try:
            page.evaluate("() => window.scrollTo(0, 0)")
            self._sleep(220)
        except Exception:
            return

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
        match = re.match(r"^kscard:([^:]+):([^:]+)$", post_id or "")
        if not match:
            return None
        return {"keyword": unquote(match.group(1)), "token": match.group(2)}

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
