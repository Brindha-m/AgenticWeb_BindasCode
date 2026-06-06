"""
agent/playwright_engine.py
--------------------------
Path A: High-level browser automation using Playwright.
Best for: fast development, most websites, production use.
"""

import asyncio
import base64
import json
import os
from typing import Optional


def _ignore_https_errors() -> bool:
    return os.getenv("PLAYWRIGHT_IGNORE_HTTPS_ERRORS", "true").lower() in (
        "1",
        "true",
        "yes",
    )


class PlaywrightEngine:
    """
    Browser engine built on Playwright.
    Handles navigation, interaction, and page observation.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    # ─── LIFECYCLE ──────────────────────────────────────────────────────────

    async def launch(self):
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768",
                "--disable-extensions",
            ],
        )

        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            ignore_https_errors=_ignore_https_errors(),
        )

        # Patch out automation signals so sites don't detect us
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en'] });
        """)

        self.page = await self.context.new_page()
        return self

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    # ─── OBSERVATION ────────────────────────────────────────────────────────

    async def observe(self) -> dict:
        """
        Capture everything the LLM planner needs to see:
        screenshot + interactive elements list + URL
        """
        # Screenshot as JPEG (smaller than PNG, good enough for vision LLM)
        screenshot_bytes = await self.page.screenshot(
            type="jpeg", quality=65, full_page=False
        )
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

        # Extract interactive elements via JS — cheaper than full accessibility tree
        try:
            elements_raw = await self.page.evaluate("""() => {
            function safeText(el) {
                const raw = el.innerText ?? el.value ?? el.placeholder
                    ?? el.getAttribute('aria-label') ?? el.getAttribute('title') ?? '';
                return String(raw == null ? '' : raw).trim();
            }
            const selectors = 'button, input, select, textarea, a[href], [role="button"], [role="link"], [onclick]';
            const all = document.querySelectorAll(selectors);
            const result = [];
            let idx = 0;
            for (const el of all) {
                try {
                    if (!el.offsetParent) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    const text = safeText(el).replace(/\\s+/g, ' ').slice(0, 80);
                    if (!text) continue;
                    result.push({
                        index: idx++,
                        tag: el.tagName.toLowerCase(),
                        type: String(el.type || ''),
                        text,
                        name: String(el.name || el.id || ''),
                        href: String(el.href || ''),
                    });
                    if (idx >= 60) break;
                } catch (e) { /* skip malformed nodes */ }
            }
            return result;
        }""")
        except Exception:
            elements_raw = []

        title = await self.page.title()

        return {
            "url": self.page.url,
            "page_title": title,
            "screenshot_b64": screenshot_b64,
            "interactive_elements": elements_raw,
        }

    async def get_screenshot_b64(self) -> str:
        """Just the screenshot — for UI display"""
        b = await self.page.screenshot(type="jpeg", quality=75)
        return base64.b64encode(b).decode()

    # ─── ACTIONS ────────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict:
        try:
            from agent.url_utils import urls_equivalent

            current = self.page.url if self.page else ""
            if current and urls_equivalent(url, current):
                await self.page.wait_for_timeout(300)
                return {"success": True, "url": current, "skipped": True}

            await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self.page.wait_for_timeout(500)
            return {"success": True, "url": self.page.url}
        except Exception as e:
            msg = str(e)
            if "ERR_CERT" in msg:
                hint = (
                    "SSL certificate error. Ensure Windows date/time is correct. "
                    "Set PLAYWRIGHT_IGNORE_HTTPS_ERRORS=true in .env (default) and restart Streamlit."
                )
                if not _ignore_https_errors():
                    return {"success": False, "error": f"{msg} — {hint}"}
            # Some sites (or flaky Playwright launches on Windows) can close the first page/context.
            # If that happens, recreate the page once and retry.
            if "Target page, context or browser has been closed" in msg:
                try:
                    if self.context and self.browser:
                        self.page = await self.context.new_page()
                        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        await self.page.wait_for_timeout(1000)
                        return {"success": True, "url": self.page.url, "recovered": True}
                except Exception as e2:
                    return {"success": False, "error": f"{msg} (retry failed: {e2})"}
            return {"success": False, "error": msg}

    async def click(self, index: int) -> dict:
        try:
            elements = await self._get_elements_list()
            if index >= len(elements):
                return {"success": False, "error": f"Index {index} out of range ({len(elements)} elements)"}

            el = elements[index]
            selector = self._build_selector(el)
            locator = self.page.locator(selector).first

            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.click(timeout=5000)
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=4000)
            except Exception:
                pass
            await self.page.wait_for_timeout(350)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def type_text(self, index: int, text: str) -> dict:
        try:
            elements = await self._get_elements_list()
            inputs = [e for e in elements if e["tag"] in ("input", "textarea", "select")]
            if index >= len(inputs):
                return {"success": False, "error": f"Input index {index} out of range"}

            el = inputs[index]
            selector = self._build_selector(el)
            locator = self.page.locator(selector).first

            await locator.click(timeout=3000)
            await locator.fill("")  # clear
            await self.page.wait_for_timeout(200)

            # Type char-by-char to bypass live-validation JS
            for char in text:
                await locator.type(char, delay=40)

            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def select_option(self, index: int, value: str) -> dict:
        try:
            elements = await self._get_elements_list()
            selects = [e for e in elements if e["tag"] == "select"]
            if index >= len(selects):
                return {"success": False, "error": "Select index out of range"}
            selector = self._build_selector(selects[index])
            await self.page.select_option(selector, value=value)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def scroll(self, direction: str = "down", amount: int = 400) -> dict:
        try:
            y = amount if direction == "down" else -amount
            await self.page.evaluate(f"window.scrollBy(0, {y})")
            await self.page.wait_for_timeout(500)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def wait(self, seconds: float = 2) -> dict:
        await asyncio.sleep(seconds)
        return {"success": True}

    # ─── HELPERS ────────────────────────────────────────────────────────────

    async def _get_elements_list(self) -> list:
        return await self.page.evaluate("""() => {
            function safeText(el) {
                const raw = el.innerText ?? el.value ?? '';
                return String(raw == null ? '' : raw).trim();
            }
            const selectors = 'button, input, select, textarea, a[href], [role="button"]';
            const all = document.querySelectorAll(selectors);
            const result = [];
            for (const el of all) {
                try {
                    if (!el.offsetParent) continue;
                    result.push({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.type || ''),
                        id: String(el.id || ''),
                        name: String(el.name || ''),
                        text: safeText(el).slice(0, 40),
                    });
                } catch (e) { /* skip */ }
            }
            return result;
        }""")

    def _build_selector(self, el: dict) -> str:
        # Prefer attribute selectors over #id because many modern sites generate IDs
        # that include characters (e.g. ':') which require CSS escaping.
        if el.get("id"):
            _id = str(el["id"])
            # Attribute selectors handle special characters safely.
            _id = _id.replace("\\", "\\\\").replace('"', '\\"')
            return f'[id="{_id}"]'
        if el.get("name"):
            _name = str(el["name"]).replace("\\", "\\\\").replace('"', '\\"')
            return f'[name="{_name}"]'
        tag = el.get("tag", "button")
        text = el.get("text", "")
        if text:
            return f"{tag}:has-text('{text[:30]}')"
        return tag
