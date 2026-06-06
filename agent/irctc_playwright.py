"""
agent/irctc_playwright.py
-------------------------
IRCTC automation with Playwright (Python), modeled after:
  - https://github.com/SwapnilChakraborty/irctc-automation
  - https://github.com/Anik-mitra08/playwright-automation-irctc

Claude API is used ONLY when CAPTCHA_MODE=claude. Everything else is plain
Playwright selectors + .env credentials.
"""

from __future__ import annotations

import asyncio
import base64
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from agent.irctc_config import IRCTCConfig, Passenger

HumanCallback = Callable[[str], str]

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: { id: undefined }, loadTimes: function(){}, csi: function(){} };
"""

CLASS_LABELS = {
    "SL": "Sleeper (SL)",
    "3A": "AC 3 Tier (3A)",
    "2A": "AC 2 Tier (2A)",
    "1A": "AC First Class (1A)",
    "CC": "AC Chair Car (CC)",
    "EC": "Executive Class (EC)",
    "2S": "Second Sitting (2S)",
    "3E": "AC 3 Economy (3E)",
}

CLASS_TAB_TEXT = {
    "SL": ["Sleeper (SL)", "Sleeper"],
    "3A": ["AC 3 Tier (3A)", "3A"],
    "2A": ["AC 2 Tier (2A)", "2A"],
    "1A": ["AC First Class (1A)", "1A"],
    "3E": ["AC 3 Economy (3E)", "3E"],
    "CC": ["AC Chair Car (CC)", "CC"],
    "2S": ["Second Sitting (2S)", "2S"],
}


class IRCTCPlaywrightBot:
    IRCTC_URL = "https://www.irctc.co.in/nget/train-search"

    def __init__(
        self,
        config: IRCTCConfig,
        human_callback: Optional[HumanCallback] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.human_callback = human_callback
        self._log_fn = log_fn or print
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.status = "idle"
        self.steps_done: list[str] = []
        self.log: list[str] = []
        self._post_book_dialog_handled = False

    # ─── lifecycle ───────────────────────────────────────────────────────

    async def launch(self) -> None:
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        launch_kwargs = {
            "headless": self.config.headless,
            "slow_mo": self.config.slow_mo,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1366,768",
            ],
        }
        if not self.config.headless:
            self._log("Launching visible Chrome (IRCTC blocks headless browsers)")
        else:
            self._log("Launching headless Chromium (may be blocked by IRCTC)")

        self.browser = await self.playwright.chromium.launch(**launch_kwargs)
        ignore_ssl = os.getenv("PLAYWRIGHT_IGNORE_HTTPS_ERRORS", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            ignore_https_errors=ignore_ssl,
        )
        await self.context.add_init_script(STEALTH_INIT_SCRIPT)
        self.page = await self.context.new_page()

    async def close(self) -> None:
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    # ─── public flow ─────────────────────────────────────────────────────

    async def run(self) -> str:
        self.status = "running"
        try:
            await self.launch()
            await self._step_open()
            self.steps_done.append("open_irctc")

            await self._step_login()
            self.steps_done.append("login")

            if self.config.login_only:
                self._log(
                    "ℹ️ LOGIN_ONLY=true — skipping search/booking. "
                    "Set LOGIN_ONLY=false in .env for full ticket flow."
                )
                await self._keep_session_alive()
                self.status = "done"
                return self.status

            self._log("📍 Step 3+: Starting ticket booking flow...")

            await self._step_search()
            self.steps_done.append("search")

            await self._step_select_train()
            self.steps_done.append("select_train")

            await self._step_passengers()
            self.steps_done.append("passengers")

            if self.config.stop_before_payment:
                await self._step_payment_handoff()
            else:
                await self._step_payment()

            self.steps_done.append("payment")
            self.status = "done"
            return self.status
        except Exception as exc:
            self._log(f"❌ {exc}")
            self.status = "failed"
            if self.page:
                try:
                    await self.page.screenshot(path="irctc_error.png")
                    self._log("Screenshot saved: irctc_error.png")
                except Exception:
                    pass
            raise
        finally:
            if self.config.login_only and self.config.keep_alive_seconds <= 0:
                pass  # caller may want browser left open on failure

    # ─── steps ───────────────────────────────────────────────────────────

    async def _step_open(self) -> None:
        self._log("📍 Step 1: Opening IRCTC")
        await self.page.goto(self.IRCTC_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        await self._dismiss_popup()
        self._log("✅ IRCTC loaded")

    async def _step_login(self) -> None:
        self._log("📍 Step 2: Login")
        await self._ensure_logged_in(full=True)

    async def _ensure_logged_in(self, full: bool = False) -> None:
        """Re-login if session expired (IRCTC often asks login again before Book Now)."""
        if await self._is_logged_in():
            if full:
                self._log("  Already logged in")
            return

        if not full:
            self._log("  ⚠ Session expired — re-logging in...")

        await self._dismiss_popup()
        await self._open_login_modal()

        try:
            await self.page.wait_for_selector(
                'input[placeholder="User Name"], input[formcontrolname="userid"]',
                timeout=12000,
            )
        except Exception:
            if await self._is_logged_in():
                self._log("  ✅ Logged in (modal skipped)")
                return
            raise RuntimeError("Login modal did not open — click LOGIN in Chrome")

        user_sel = 'input[placeholder="User Name"], input[formcontrolname="userid"]'
        pass_sel = 'input[placeholder="Password"], input[formcontrolname="password"]'

        self._log(f"  Typing credentials for {self.config.username[:12]}...")
        await self.page.fill(user_sel, self.config.username)
        await self.page.fill(pass_sel, self.config.password)

        captcha = await self._resolve_captcha()
        if captcha:
            cap_sel = 'input[placeholder="Enter Captcha"], input[formcontrolname="captcha"]'
            await self.page.fill(cap_sel, captcha.strip())
            self._log("  CAPTCHA filled")

        await self._click_sign_in()
        await asyncio.sleep(4)

        if await self._is_logged_in():
            self._log("✅ Login successful")
            return

        if await self._maybe_handle_otp():
            await asyncio.sleep(4)

        if await self._is_logged_in():
            self._log("✅ Login successful")
            return

        self._log("  ⚠ Finish CAPTCHA/OTP in Chrome if shown")
        reply = await self._ask_human(
            "Complete login in Chrome, then type DONE when MY ACCOUNT or Logout appears:"
        )
        if reply.strip().upper() != "DONE" or not await self._is_logged_in():
            raise RuntimeError("Login not completed")
        self._log("✅ Login confirmed")

    async def _step_search(self) -> None:
        self._log("📍 Step 3: Search trains")
        ui_date = (self.config.journey_date or "").strip()
        env_date = os.getenv("IRCTC_JOURNEY_DATE", "").strip()
        if ui_date:
            self.config.journey_date = ui_date
        elif env_date:
            self.config.journey_date = env_date
        else:
            self.config.journey_date = (
                datetime.now() + timedelta(days=self.config.journey_date_offset_days)
            ).strftime("%d/%m/%Y")
        self._log(
            f"  {self.config.from_name} ({self.config.from_station}) → "
            f"{self.config.to_name} ({self.config.to_station}) · "
            f"{self.config.journey_date} · {self.config.train_class}"
        )

        await self._ensure_logged_in()

        if "train-search" not in (self.page.url or ""):
            await self.page.goto(self.IRCTC_URL, wait_until="domcontentloaded", timeout=60000)
        await self._dismiss_popup()
        await self._ensure_logged_in()
        await asyncio.sleep(1)

        await self._fill_station("origin", self.config.from_name, self.config.from_station)
        await self._fill_station("destination", self.config.to_name, self.config.to_station)
        await self._fill_journey_date(self.config.journey_date)
        # Anik repo: select Sleeper (SL) in #journeyClass BEFORE search
        await self._select_journey_class_anik(self.config.train_class)

        if not await self._click_search():
            raise RuntimeError("Search Trains button not found")

        await self._anik_pause(5, 5)
        await self._ensure_logged_in()
        if not await self._has_train_results():
            raise RuntimeError("No train results visible after search")
        self._log("✅ Train results found")

    async def _anik_pause(self, base: float = 4, extra: float = 3) -> None:
        """Human-like pause matching Anik-mitra08/playwright-automation-irctc."""
        await asyncio.sleep(base + random.random() * extra)

    async def _step_select_train(self) -> None:
        """
        Train booking on results page — based on:
        https://github.com/Anik-mitra08/playwright-automation-irctc
        Flow: find train → Refresh → class tab (SL) → date card → Book Now
        """
        self._log("📍 Step 4: Select train (Anik flow)")
        await self._ensure_logged_in()

        preferred = self.config.preferred_train.strip()
        class_code = self.config.train_class.upper()

        trains = self.page.locator("app-train-list app-train-avl-enq")
        if await trains.count() == 0:
            trains = self.page.locator("app-train-avl-enq")

        count = await trains.count()
        if count == 0:
            raise RuntimeError("No train rows on page")

        row = None
        train_no = ""
        for i in range(count):
            candidate = trains.nth(i)
            text = (await candidate.text_content()) or ""
            nums = re.findall(r"\b\d{5}\b", text)
            tno = nums[0] if nums else ""
            if preferred and preferred not in text:
                continue
            row = candidate
            train_no = tno or f"row-{i + 1}"
            self._log(f"  → Target train {train_no}" + (f" (preferred {preferred})" if preferred else ""))
            break

        if row is None:
            row = trains.nth(0)
            text0 = (await row.text_content()) or ""
            nums0 = re.findall(r"\b\d{5}\b", text0)
            train_no = nums0[0] if nums0 else "first"
            if preferred:
                self._log(f"  ⚠ Train {preferred} not found — using {train_no}")
            else:
                self._log(f"  → Using first train ({train_no})")

        self._log(f"  {count} trains in results")
        await row.scroll_into_view_if_needed()
        await self._anik_pause(2, 2)

        # 1) Refresh availability (Anik: trainRow.locator("text=Refresh"))
        refresh = row.get_by_text("Refresh", exact=False)
        if await refresh.count() > 0 and await refresh.first.is_visible():
            await refresh.first.click(force=True)
            self._log("  → Refreshed availability")
            await self._anik_pause(4, 3)

        # 2) Class tab — desktop UI: click "Sleeper (SL)" tab (orange underline)
        if not await self._select_class_tab_anik(row, class_code):
            raise RuntimeError(
                f"Could not click {class_code} class tab — see Chrome (Sleeper/SL tab row)"
            )

        await self._anik_pause(3, 2)

        # 3) Date card — match Streamlit journey date; ask user if auto pick fails
        if not await self._select_journey_date_card(row, self.config.journey_date):
            self._log(f"  ⚠ Date card miss for {self.config.journey_date}")
            if not await self._ask_user_for_date_card(row, self.config.journey_date):
                raise RuntimeError(
                    f"Could not select date card for {self.config.journey_date}. "
                    "Click the date in Chrome or enter it when prompted."
                )

        await self._anik_pause(4, 3)

        # 4) Book Now — only enabled after class + date selected
        if not await self._click_book_now_anik(row):
            raise RuntimeError("Book Now not clicked — select SL tab + date card in Chrome")

        await self._handle_wl_confirm_dialog()
        await self._anik_pause(3, 2)
        await self._ensure_logged_in()

        try:
            await self.page.wait_for_url(re.compile(r"book|psgn|passenger|review", re.I), timeout=15000)
        except Exception:
            pass

        await self._handle_post_book_dialog(class_code)
        if not await self._wait_for_passenger_form(timeout=45):
            raise RuntimeError(
                "Passenger form not loaded — finish login/CAPTCHA in Chrome or click Book Now manually"
            )

        self._log("✅ Train selected — passenger page open")

    async def _select_journey_class_anik(self, class_code: str) -> None:
        """Anik: #journeyClass dropdown → Sleeper (SL) before Search."""
        label = CLASS_LABELS.get(class_code, class_code)
        trigger = self.page.locator("#journeyClass div, #journeyClass").first
        if not await trigger.is_visible():
            await self._select_search_class(class_code=class_code, all_classes=False)
            return
        await trigger.click()
        await self._anik_pause(2, 2)
        option = self.page.locator(
            f"#journeyClass li:has-text('{label}'), "
            f"p-dropdownitem li:has-text('{label}'), "
            f"li:has-text('{label}')"
        ).first
        if await option.is_visible():
            await option.click()
            self._log(f"  → Search class: {label} (#journeyClass)")
        else:
            await self.page.locator(f"li:has-text('{class_code}')").first.click()
            self._log(f"  → Search class: {class_code}")

    async def _select_class_tab_anik(self, row, class_code: str) -> bool:
        """Click horizontal class tab e.g. 'Sleeper (SL)' on expanded train row."""
        label = CLASS_LABELS.get(class_code, class_code)
        for text in (label, f"{class_code}", *CLASS_TAB_TEXT.get(class_code, [])):
            tab = row.get_by_text(text, exact=True)
            if await tab.count() == 0:
                tab = row.get_by_text(text, exact=False)
            try:
                if await tab.count() > 0 and await tab.first.is_visible():
                    await tab.first.click(force=True)
                    self._log(f"  → Class tab: {text}")
                    await self._anik_pause(2, 1)
                    return True
            except Exception:
                continue

        handle = await row.element_handle()
        if not handle:
            return False
        clicked = await self.page.evaluate(
            """
            ([row, label, code]) => {
                const targets = [label, code, 'Sleeper (SL)', 'Sleeper'];
                for (const t of targets) {
                    for (const el of row.querySelectorAll('span, a, div, li, button, label')) {
                        const txt = (el.innerText || el.textContent || '').trim();
                        if (txt !== t && !txt.includes(t)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 10 || r.height < 8) continue;
                        el.click();
                        return true;
                    }
                }
                return false;
            }
            """,
            [handle, label, class_code],
        )
        if clicked:
            self._log(f"  → Class tab: {label} (JS)")
            return True
        return False

    async def _select_journey_date_card(self, row, date_str: str) -> bool:
        """Pick the availability date card matching the Streamlit / config journey date."""
        if not (date_str or "").strip():
            return False
        await self._scroll_train_row_date_strip(row)
        if await self._select_date_anik(row, date_str):
            return True
        if await self._select_date_card(row, date_str):
            return True
        if await self._select_date_by_components(row, date_str):
            return True
        if await self._select_date_in_availability_grid(row, date_str):
            return True
        return False

    async def _scroll_train_row_date_strip(self, row) -> None:
        """Scroll date availability strip into view (horizontal IRCTC layout)."""
        handle = await row.element_handle()
        if not handle:
            return
        await self.page.evaluate(
            """
            (row) => {
                for (const el of row.querySelectorAll(
                    '[class*="avail"], [class*="date"], table, .scroll, div'
                )) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 120 && r.height > 30 && r.height < 200) {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        return;
                    }
                }
                row.scrollIntoView({ block: 'center', inline: 'nearest' });
            }
            """,
            handle,
        )
        await asyncio.sleep(0.4)

    async def _select_date_in_availability_grid(self, row, date_str: str) -> bool:
        """Click date tile in IRCTC availability grid (parent of strong date label)."""
        patterns = self._journey_date_patterns(date_str)
        handle = await row.element_handle()
        if not handle:
            return False
        clicked = await self.page.evaluate(
            """
            ([row, patterns]) => {
                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const blocks = [];
                for (const el of row.querySelectorAll('div, td, li, span, a, strong')) {
                    const t = norm(el.innerText);
                    if (!t || t.length > 72) continue;
                    const matched = patterns.some((p) => t.toLowerCase().includes(p.toLowerCase()));
                    if (!matched) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 24 || r.height < 16) continue;
                    const block = norm((el.closest('div, td, li') || el).innerText);
                    const score =
                        (/WL\\d+|AVAILABLE|AVL|RAC/i.test(block) ? 8 : 0) +
                        (el.tagName === 'STRONG' ? 4 : 0) +
                        (r.width * r.height > 800 ? 2 : 0);
                    blocks.push({ el, score, t: block.slice(0, 60) });
                }
                blocks.sort((a, b) => b.score - a.score);
                for (const { el, t } of blocks) {
                    try {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        el.click();
                        return t;
                    } catch (e) { /* next */ }
                }
                return '';
            }
            """,
            [handle, patterns],
        )
        if clicked:
            self._log(f"  → Date tile: {clicked}")
            await self._anik_pause(2, 1)
            return True
        return False

    async def _ask_user_for_date_card(self, row, default_date: str) -> bool:
        """Prompt user (Streamlit or terminal) when automatic date-card click fails."""
        try:
            hint = self._journey_date_patterns(default_date)[0]
        except ValueError:
            hint = "Mon, 1 Jun"
        for attempt in range(3):
            reply = await self._ask_human(
                "[DATE_CARD] Auto date pick failed. Enter **DD/MM/YYYY** "
                f"(e.g. {default_date}) or the exact label from Chrome (e.g. **{hint}**). "
                "Type **DONE** if you already clicked the date card manually."
            )
            reply = (reply or "").strip()
            if not reply or reply.upper() == "CANCEL":
                return False
            if reply.upper() == "DONE":
                self._log("  → User selected date manually in Chrome")
                return True
            if "/" in reply and await self._select_journey_date_card(row, reply):
                self.config.journey_date = reply
                return True
            if await self._select_date_by_visible_text(row, reply):
                return True
            self._log(f"  ⚠ Could not click date '{reply}' (attempt {attempt + 1}/3)")
        return False

    async def _select_date_by_visible_text(self, row, text: str) -> bool:
        needle = text.strip()
        if not needle:
            return False
        await self._scroll_train_row_date_strip(row)
        for loc_factory in (
            lambda: row.locator("strong").filter(has_text=re.compile(re.escape(needle[:24]), re.I)),
            lambda: row.get_by_text(needle, exact=False),
        ):
            loc = loc_factory()
            for i in range(min(await loc.count(), 10)):
                el = loc.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True, timeout=8000)
                    self._log(f"  → Date card (user): {needle}")
                    await self._anik_pause(2, 1)
                    return True
                except Exception:
                    continue
        handle = await row.element_handle()
        if not handle:
            return False
        clicked = await self.page.evaluate(
            """
            ([row, needle]) => {
                const n = needle.toLowerCase();
                for (const el of row.querySelectorAll('strong, div, span, td, a, button')) {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!t.toLowerCase().includes(n)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) continue;
                    const st = getComputedStyle(el);
                    if (st.visibility === 'hidden' || st.display === 'none') continue;
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    el.click();
                    return t.slice(0, 60);
                }
                return '';
            }
            """,
            [handle, needle],
        )
        if clicked:
            self._log(f"  → Date card (user label): {clicked}")
            await self._anik_pause(2, 1)
            return True
        return False

    async def _select_date_by_components(self, row, date_str: str) -> bool:
        """JS fallback: match day + month abbrev in train row date cells."""
        try:
            dt = datetime.strptime(date_str.strip(), "%d/%m/%Y")
        except ValueError:
            return False

        handle = await row.element_handle()
        if not handle:
            return False

        clicked = await self.page.evaluate(
            """
            ([row, day, monthAbbrev, monthFull]) => {
                const dayRe = new RegExp('\\\\b0?' + day + '\\\\b');
                const monthRe = new RegExp(monthAbbrev + '|' + monthFull, 'i');
                const statusRe = /WL\\d*|AVAILABLE|AVL|RAC|REGRET/i;
                const candidates = [];
                for (const el of row.querySelectorAll('strong, div, span, td, a, button')) {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!t || t.length > 48) continue;
                    if (!dayRe.test(t) || !monthRe.test(t)) continue;
                    if (!/\\d{1,2}\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i.test(t)
                        && !/(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/i.test(t)) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 12 || r.height < 8) continue;
                    const block = (el.closest('div[class], td, li') || el).innerText || t;
                    candidates.push({
                        el,
                        score: (statusRe.test(block) ? 10 : 0) + (el.tagName === 'STRONG' ? 5 : 0),
                    });
                }
                candidates.sort((a, b) => b.score - a.score);
                for (const { el } of candidates) {
                    try {
                        el.click();
                        return (el.innerText || '').trim().slice(0, 40);
                    } catch (e) { /* try next */ }
                }
                return '';
            }
            """,
            [handle, dt.day, dt.strftime("%b"), dt.strftime("%B")],
        )
        if clicked:
            self._log(f"  → Date card: {clicked} (matched {date_str})")
            await self._anik_pause(2, 1)
            return True
        return False

    async def _select_date_anik(self, row, date_str: str) -> bool:
        """Anik: row.locator('strong', { hasText: 'Sun, 31 May' }).click()"""
        patterns = self._journey_date_patterns(date_str)
        for pat in patterns:
            strong = row.locator("strong").filter(has_text=re.compile(re.escape(pat), re.I))
            if await strong.count() > 0:
                try:
                    await strong.first.click(force=True)
                    self._log(f"  → Date selected: {pat} (strong)")
                    await self._anik_pause(2, 1)
                    return True
                except Exception:
                    pass

            card = row.locator("div, span, td").filter(has_text=re.compile(re.escape(pat), re.I))
            for i in range(min(await card.count(), 8)):
                el = card.nth(i)
                if not await el.is_visible():
                    continue
                txt = (await el.text_content()) or ""
                if re.search(r"WL\d+|AVAILABLE|RAC|REGRET|AVL", txt, re.I):
                    await el.click(force=True)
                    self._log(f"  → Date card: {pat}")
                    await self._anik_pause(2, 1)
                    return True
        return False

    async def _click_book_now_anik(self, row) -> bool:
        """Click orange Book Now button inside train row."""
        selectors = [
            row.locator("button").filter(has_text=re.compile(r"Book\s*Now", re.I)),
            row.locator("span button").filter(has_text=re.compile(r"Book\s*Now", re.I)),
            row.get_by_role("button", name=re.compile(r"Book\s*Now", re.I)),
            self.page.locator("app-train-avl-enq button").filter(has_text=re.compile(r"Book\s*Now", re.I)),
        ]
        await self._anik_pause(2, 2)
        for loc in selectors:
            try:
                if await loc.count() == 0:
                    continue
                btn = loc.first
                if not await btn.is_visible():
                    continue
                await btn.scroll_into_view_if_needed()
                for _ in range(10):
                    if await btn.is_enabled():
                        await btn.click(force=True)
                        self._log("  → Clicked Book Now")
                        return True
                    await asyncio.sleep(0.6)
            except Exception:
                continue
        return False

    async def _handle_wl_confirm_dialog(self) -> None:
        """Confirm waitlist booking popup if IRCTC shows it after Book Now."""
        for sel in (
            'button:has-text("OK")',
            'button:has-text("Yes")',
            'button:has-text("Confirm")',
            'span:has-text("OK")',
        ):
            btn = self.page.locator(sel).first
            try:
                if await btn.is_visible():
                    await btn.click(force=True)
                    self._log("  → Confirmed WL/booking popup")
                    await asyncio.sleep(1.5)
                    return
            except Exception:
                continue

    async def _ensure_train_panel_expanded(self, row, class_code: str) -> None:
        """Expand train row if class tabs (Sleeper SL, etc.) are not yet visible."""
        tab_labels = CLASS_TAB_TEXT.get(class_code, [CLASS_LABELS.get(class_code, class_code)])
        for label in tab_labels:
            tab = row.get_by_text(label, exact=False)
            if await tab.count() > 0 and await tab.first.is_visible():
                return

        # Collapsed row — click class/status summary to expand panel
        for sel in (
            row.locator("div, span, td").filter(has_text=re.compile(r"WL\d+|AVAILABLE|RAC|REGRET|AVL", re.I)),
            row.locator(f'strong:has-text("{class_code}")'),
        ):
            if await sel.count() > 0:
                try:
                    await sel.first.click()
                    self._log("  → Expanded train availability panel")
                    await asyncio.sleep(1.5)
                    return
                except Exception:
                    continue

    async def _select_class_tab(self, row, class_code: str) -> bool:
        """
        IRCTC new UI: horizontal tabs like 'Sleeper (SL)' must be clicked
        before date cards and Book Now become active.
        """
        labels = CLASS_TAB_TEXT.get(class_code, [CLASS_LABELS.get(class_code, class_code)])
        for label in labels:
            candidates = [
                row.get_by_text(label, exact=True),
                row.get_by_text(label, exact=False),
                row.locator("span, div, a, li, button").filter(has_text=re.compile(
                    rf"{re.escape(label)}|{class_code}\)", re.I
                )),
            ]
            for loc in candidates:
                try:
                    if await loc.count() == 0:
                        continue
                    tab = loc.first
                    if not await tab.is_visible():
                        continue
                    await tab.scroll_into_view_if_needed()
                    await tab.click()
                    self._log(f"  → Class tab selected: {label}")
                    await asyncio.sleep(1.2)
                    return True
                except Exception:
                    continue
        return False

    def _journey_date_patterns(self, date_str: str) -> list[str]:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        wd = dt.strftime("%a")
        day = dt.day
        mon = dt.strftime("%b")
        mon_full = dt.strftime("%B")
        return [
            f"{wd}, {day} {mon}",
            f"{wd}, {day:02d} {mon}",
            f"{wd}, {day} {mon_full}",
            f"{wd},{day} {mon}",
            f"{day} {mon}",
            f"{day:02d} {mon}",
            f"{day} {mon_full}",
            f"{day:02d} {mon_full}",
            f"{day}-{mon}",
            f"{day:02d}-{mon}",
        ]

    async def _select_date_card(self, row, date_str: str) -> bool:
        """Click the date card (e.g. 'Sun, 31 May' with WL83) for journey date."""
        patterns = self._journey_date_patterns(date_str)
        for pat in patterns:
            # Date cards are boxed cells containing day + month + status
            card = row.locator("div, span, strong, td").filter(has_text=re.compile(
                re.escape(pat), re.I
            ))
            for i in range(await card.count()):
                el = card.nth(i)
                if not await el.is_visible():
                    continue
                text = ((await el.text_content()) or "").upper()
                if not re.search(r"\d{1,2}\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", text):
                    continue
                try:
                    await el.click()
                    self._log(f"  → Date card selected: {pat}")
                    await asyncio.sleep(1.2)
                    return True
                except Exception:
                    continue

        # JS: click box containing date pattern
        handle = await row.element_handle()
        if handle:
            for pat in patterns:
                clicked = await self.page.evaluate(
                    """
                    ([row, pattern]) => {
                        const re = new RegExp(pattern.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'i');
                        for (const el of row.querySelectorAll('div, span, strong, td, a')) {
                            const t = (el.innerText || '').trim();
                            if (!re.test(t)) continue;
                            if (!/\\d{1,2}\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i.test(t)) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 20 || r.height < 10) continue;
                            el.click();
                            return true;
                        }
                        return false;
                    }
                    """,
                    [handle, pat],
                )
                if clicked:
                    self._log(f"  → Date card selected: {pat}")
                    await asyncio.sleep(1.2)
                    return True
        return False

    async def _select_first_date_card(self, row) -> bool:
        """Fallback: click first visible date card in the row."""
        cards = row.locator("div, span").filter(
            has_text=re.compile(
                r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun).*"
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
                re.I,
            )
        )
        if await cards.count() > 0:
            await cards.first.click()
            self._log("  → First date card selected")
            await asyncio.sleep(1)
            return True
        return False

    async def _click_book_now(self, row) -> bool:
        """Book Now only enables after class tab + date card are selected."""
        book = row.get_by_role("button", name=re.compile(r"Book Now", re.I))
        if await book.count() == 0:
            book = self.page.get_by_role("button", name=re.compile(r"Book Now", re.I))

        for _ in range(15):
            try:
                btn = book.first
                if await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    self._log("  → Clicked Book Now")
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def _handle_post_book_dialog(self, class_code: str) -> bool:
        """
        Handle class/quota dialog ONLY after Book Now — scoped to modals/sidebars.
        Does NOT touch the search form #journeyClass dropdown (that caused infinite loops).
        """
        if self._post_book_dialog_handled:
            return False
        if await self._is_passenger_page():
            self._post_book_dialog_handled = True
            return False

        label = CLASS_LABELS.get(class_code, class_code)
        dialog = self.page.locator(
            'p-dialog, [role="dialog"], p-sidebar, .ui-dialog, app-review-booking'
        ).filter(has=self.page.locator("button, span, label"))

        if await dialog.count() == 0:
            return False

        root = dialog.first
        if not await root.is_visible():
            return False

        self._log("  Post-book dialog detected")
        acted = False

        for loc in (
            root.locator(f'li:has-text("{label}")'),
            root.locator(f'li:has-text("{class_code}")'),
            root.get_by_text(label, exact=False),
        ):
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click()
                    self._log(f"  → Selected {class_code} in booking dialog")
                    acted = True
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

        continue_btn = root.locator(
            'button:has-text("Continue"), button:has-text("Book Now"), button:has-text("Confirm")'
        ).first
        if await continue_btn.is_visible():
            await continue_btn.click()
            self._log("  → Confirmed booking dialog")
            acted = True
            await asyncio.sleep(2)

        if acted:
            self._post_book_dialog_handled = True
        return acted

    async def _is_passenger_page(self) -> bool:
        """True when editable passenger fields are visible."""
        if await self._passenger_fields_ready():
            return True
        url = (self.page.url or "").lower()
        return any(part in url for part in ("/book", "psgn", "passenger-input", "review-book"))

    async def _wait_for_passenger_form(self, timeout: float = 30) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._login_modal_visible():
                await self._ensure_logged_in()
            if await self._passenger_fields_ready():
                return True
            if not self._post_book_dialog_handled:
                await self._handle_post_book_dialog(self.config.train_class)
            await asyncio.sleep(0.8)
        return False

    async def _passenger_fields_ready(self) -> bool:
        """Wait until name or mobile inputs are visible and interactable."""
        selectors = [
            'app-passenger p-autocomplete input',
            'app-passenger input[formcontrolname="passengerName"]',
            'input[placeholder*="Passenger Name" i]',
            '#mobileNumber',
            "input[formcontrolname='mobileNumber']",
            'input[placeholder*="Mobile" i]',
        ]
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible() and await loc.is_enabled():
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_passenger_fields(self, timeout: float = 25) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._passenger_fields_ready():
                await asyncio.sleep(0.5)
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Passenger input fields not ready — scroll in Chrome or complete any popup")

    async def _login_modal_visible(self) -> bool:
        modal = self.page.locator(
            'input[placeholder="User Name"], input[formcontrolname="userid"]'
        )
        try:
            return await modal.first.is_visible()
        except Exception:
            return False

    async def _step_passengers(self) -> None:
        self._log("📍 Step 5: Passenger details")
        await self._wait_for_passenger_fields(timeout=30)

        for idx, passenger in enumerate(self.config.passengers):
            if idx > 0:
                add_btn = self.page.locator(
                    'button:has-text("Add Passenger"), span:has-text("Add Passenger")'
                )
                if await add_btn.first.is_visible():
                    await add_btn.first.click()
                    await asyncio.sleep(1)

            await self._fill_passenger_row(idx, passenger)
            await self._dismiss_name_autocomplete()

        if self.config.mobile:
            await self._dismiss_name_autocomplete()
            if not await self._fill_mobile(self.config.mobile):
                self._log("  ⚠ Mobile field not found — enter manually in Chrome if needed")
            else:
                self._log(f"  Mobile: {self.config.mobile[:4]}******")

        if not await self._passenger_sidebar_visible():
            self._log(
                "  ⚠ Passenger panel closed — re-open from train results (Book Now) "
                "or enter mobile + Continue manually in Chrome"
            )
            reply = await self._ask_human(
                "Passenger form closed in Chrome. Re-open it, fill mobile, click Continue, "
                "then type DONE:"
            )
            if reply.strip().upper() != "DONE":
                raise RuntimeError("Passenger form not available after mobile step")
        else:
            self._log("  → Clicking Continue on passenger form...")
            await self._click_passenger_continue("passenger")

        phase = await self._wait_booking_phase_transition(
            after="passenger_continue", timeout=20
        )
        self._log(f"  → Booking phase: {phase}")

        if phase == "captcha":
            await self._handle_booking_captcha()
            phase = await self._wait_booking_phase_transition(
                after="captcha", timeout=30
            )
            self._log(f"  → After CAPTCHA: {phase}")

        if phase == "payment":
            self._log("  → On payment page — passenger step complete")
        elif phase in ("review", "passenger", "unknown"):
            await self._click_passenger_continue("review")
        self._log("✅ Passenger details submitted")

    async def _step_payment(self) -> None:
        self._log("📍 Step 6: Payment automation")
        await asyncio.sleep(2)

        method = self.config.payment_method
        provider = self.config.payment_provider

        if method == "UPI":
            upi = self.page.locator('span:has-text("UPI"), label:has-text("UPI")').first
            if await upi.is_visible():
                await upi.click()
                self._log("  → UPI selected")
        else:
            ipay = self.page.locator(
                '#pay-type span, div:has-text("IRCTC iPay"), '
                'span:has-text("IRCTC iPay"), label:has-text("IRCTC")'
            ).first
            if await ipay.is_visible():
                await ipay.click()
                self._log("  → IRCTC iPay selected")

        await self._click_payment_continue()
        await self._handle_booking_captcha()

        card = self.page.locator("div").filter(
            has_text=re.compile(
                r"Credit.*Debit.*Net Banking|Powered by IRCTC|IRCTC iPay",
                re.I,
            )
        ).first
        if await card.is_visible():
            await card.click(force=True)
            self._log("  → IRCTC payment card selected")

        await self._click_payment_continue(sidebar=True)

        pay_book = self.page.get_by_role("button", name=re.compile(r"Pay\s*&\s*Book", re.I))
        if await pay_book.is_visible():
            await pay_book.click()
            self._log("  → Pay & Book clicked")
            await asyncio.sleep(3)

        if provider:
            provider_btn = self.page.get_by_text(provider, exact=False).first
            if await provider_btn.is_visible():
                await provider_btn.click(force=True)
                self._log(f"  → {provider} selected on gateway")

        pay_btn = self.page.get_by_role("button", name=re.compile(r"^Pay$", re.I))
        if await pay_btn.is_visible():
            await pay_btn.click(force=True)
            self._log("  → Pay button clicked — complete UPI OTP / bank auth in browser")

        self._log("  ⚠ Finish OTP / PIN in Chrome if prompted")
        reply = await self._ask_human(
            "Complete final payment (UPI OTP / bank PIN) in Chrome. Type DONE when PNR appears:"
        )
        if reply.strip().upper() == "DONE":
            self._log("✅ Booking confirmed")
        else:
            self._log("ℹ️ Payment step ended — check browser for PNR")

    async def _dismiss_name_autocomplete(self) -> None:
        """Close name suggestion dropdown only — do not Escape the passenger sidebar."""
        panel = self.page.locator(
            "p-autocomplete-panel, .ui-autocomplete-panel, .p-autocomplete-panel"
        )
        try:
            if await panel.count() > 0 and await panel.first.is_visible():
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.25)
        except Exception:
            pass
        try:
            await self.page.locator("body").click(position={"x": 8, "y": 8}, force=True)
            await asyncio.sleep(0.15)
        except Exception:
            pass

    async def _passenger_sidebar_visible(self) -> bool:
        """True while the orange passenger booking sidebar is still open."""
        try:
            form = self.page.locator("#psgn-form, app-passenger").first
            if await form.count() == 0:
                return False
            return await form.is_visible()
        except Exception:
            return False

    async def _select_payment_on_passenger_page(self) -> None:
        """
        IRCTC disables Continue until a payment option is chosen (Anik flow).
        """
        method = (self.config.payment_method or "IRCTC_IPAY").upper()
        root = self.page.locator("#psgn-form")
        if await root.count() == 0:
            return

        clicked = False
        if method == "UPI":
            for sel in (
                'xpath=//*[@id="psgn-form"]//*[@id="2"]/div/div[2]/span',
                "#psgn-form #pay-type span:has-text('UPI')",
                "#psgn-form label:has-text('UPI')",
                "#psgn-form span:has-text('UPI')",
            ):
                loc = self.page.locator(sel).first
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(force=True)
                        clicked = True
                        self._log("  → UPI payment option selected")
                        break
                except Exception:
                    continue
        else:
            for sel in (
                "xpath=//*[@id='pay-type']/span/div[1]",
                "#pay-type span div:first-child",
                "#psgn-form #pay-type span",
                "#psgn-form span:has-text('IRCTC iPay')",
                "#psgn-form label:has-text('IRCTC')",
            ):
                loc = self.page.locator(sel).first
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(force=True)
                        clicked = True
                        self._log("  → IRCTC iPay payment option selected")
                        break
                except Exception:
                    continue

        if not clicked:
            try:
                clicked = await root.evaluate(
                    """(form, method) => {
                        const wantUpi = method === 'UPI';
                        const nodes = form.querySelectorAll(
                            '#pay-type span, label, span, div[role="radio"]'
                        );
                        for (const el of nodes) {
                            const t = (el.innerText || '').trim();
                            if (!t) continue;
                            if (wantUpi && /\\bUPI\\b/i.test(t)) {
                                el.click();
                                return true;
                            }
                            if (!wantUpi && /iPay|IRCTC/i.test(t)) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""",
                    method,
                )
                if clicked:
                    self._log(f"  → {method} payment option selected (JS)")
            except Exception:
                pass

        await asyncio.sleep(0.8)

    async def _dismiss_dimmer(self) -> None:
        """
        Unblock Continue clicks without Escape — IRCTC closes the passenger
        sidebar when Escape is pressed after mobile entry.
        """
        try:
            await self.page.evaluate("""
                () => {
                    for (const sel of [
                        '.dimmer', '.ui-widget-overlay',
                        '.p-dialog-mask', '.cdk-overlay-backdrop'
                    ]) {
                        document.querySelectorAll(sel).forEach((el) => {
                            const r = el.getBoundingClientRect();
                            const covers = r.width > window.innerWidth * 0.5
                                && r.height > window.innerHeight * 0.5;
                            if (covers) {
                                el.style.pointerEvents = 'none';
                                el.style.opacity = '0';
                            }
                        });
                    }
                }
            """)
        except Exception:
            pass
        await asyncio.sleep(0.2)

    async def _passenger_continue_enabled(self, btn) -> bool:
        try:
            if not await btn.is_visible():
                return False
            disabled = await btn.is_disabled()
            if disabled:
                return False
            cls = (await btn.get_attribute("class") or "").lower()
            aria = (await btn.get_attribute("aria-disabled") or "").lower()
            if "disable" in cls or aria == "true":
                return False
            return True
        except Exception:
            return False

    async def _detect_booking_phase(self) -> str:
        """Where we are after passenger Continue: captcha, review, payment, or still passenger."""
        try:
            captcha = self.page.locator(
                "#captcha, input[formcontrolname='captcha'], "
                'input[placeholder*="captcha" i]'
            )
            if await captcha.count() > 0 and await captcha.first.is_visible():
                return "captcha"
        except Exception:
            pass
        try:
            if await self.page.locator("app-payment, #pay-type").first.is_visible():
                return "payment"
        except Exception:
            pass
        try:
            review = self.page.locator("app-review-booking, app-review")
            if await review.count() > 0 and await review.first.is_visible():
                return "review"
        except Exception:
            pass
        body = ""
        try:
            body = (await self.page.inner_text("body"))[:4000].lower()
        except Exception:
            pass
        if "review booking" in body or "booking details" in body:
            return "review"
        if "credit" in body and "debit" in body and "irctc" in body:
            return "payment"
        if await self._passenger_fields_ready():
            return "passenger"
        return "unknown"

    async def _wait_booking_phase_transition(
        self, *, after: str, timeout: float = 20
    ) -> str:
        """Wait until phase changes away from passenger-only (or captcha clears)."""
        deadline = time.time() + timeout
        prev = await self._detect_booking_phase()
        while time.time() < deadline:
            phase = await self._detect_booking_phase()
            if after == "passenger_continue" and phase != "passenger":
                return phase
            if after == "captcha" and phase not in ("captcha", "passenger"):
                return phase
            if phase != prev and phase != "unknown":
                return phase
            await asyncio.sleep(0.4)
        return await self._detect_booking_phase()

    async def _click_passenger_continue_js(self, *, pick: str = "first") -> bool:
        """
        Click Continue in #psgn-form.
        pick='first' — passenger sidebar (Anik first Continue)
        pick='last'  — review/payment sidebar (second Continue)
        """
        try:
            return bool(
                await self.page.evaluate(
                    """(pick) => {
                        const form = document.querySelector('#psgn-form');
                        if (!form) return false;
                        const isContinue = (el) => {
                            const t = (el.innerText || el.textContent || '').trim();
                            return /continue/i.test(t) && !/search/i.test(t);
                        };
                        const canClick = (btn) => {
                            if (!btn || btn.offsetParent === null) return false;
                            if (btn.disabled) return false;
                            const c = (btn.className || '').toLowerCase();
                            if (c.includes('disable')) return false;
                            return isContinue(btn);
                        };
                        const all = [...form.querySelectorAll('button')].filter(canClick);
                        if (!all.length) return false;
                        const btn = pick === 'last' ? all[all.length - 1] : all[0];
                        btn.scrollIntoView({ block: 'center', inline: 'center' });
                        btn.click();
                        return true;
                    }""",
                    pick,
                )
            )
        except Exception:
            return False

    async def _click_passenger_continue(self, step: str = "passenger") -> None:
        """
        Continue inside #psgn-form sidebar only — NOT the search form train_Search button.
        Anik: #psgn-form/form/div/div[1]/p-sidebar/div/div/div[2]/button
        """
        if step in ("review", "post-captcha") and await self._detect_booking_phase() == "payment":
            self._log(f"  → {step} skipped (already on payment)")
            return

        await self._dismiss_dimmer()
        if step == "passenger":
            await self._select_payment_on_passenger_page()
        await self._anik_pause(2, 1)

        js_pick = "first" if step == "passenger" else "last"
        anik_xpath = (
            "xpath=//*[@id='psgn-form']/form/div/div[1]/p-sidebar/div/div/div[2]/button"
        )
        if step in ("review", "post-captcha"):
            locator_chain = [
                self.page.locator(
                    "xpath=//*[@id='psgn-form']//p-sidebar[2]//button"
                ).filter(has_text=re.compile(r"Continue", re.I)),
                self.page.locator("#psgn-form p-sidebar").last.locator("button").filter(
                    has_text=re.compile(r"Continue", re.I)
                ),
                self.page.get_by_role("button", name=re.compile(r"^Continue$", re.I)),
                self.page.locator("#psgn-form").get_by_role(
                    "button", name=re.compile(r"^Continue$", re.I)
                ),
                self.page.locator(
                    '#psgn-form button:has-text("Continue"):not(.train_Search)'
                ),
            ]
        else:
            locator_chain = [
                self.page.locator(anik_xpath),
                self.page.locator(
                    "#psgn-form form div p-sidebar div div div:nth-child(2) button"
                ),
                self.page.locator("#psgn-form").get_by_role(
                    "button", name=re.compile(r"^Continue$", re.I)
                ),
                self.page.locator("#psgn-form p-sidebar button").filter(
                    has_text=re.compile(r"Continue", re.I)
                ),
                self.page.locator(
                    '#psgn-form button:has-text("Continue"):not(.train_Search)'
                ),
            ]

        deadline = time.time() + (25 if step == "passenger" else 15)
        last_err = ""
        while time.time() < deadline:
            if await self._click_passenger_continue_js(pick=js_pick):
                self._log(f"  → {step} Continue (JS)")
                await self._anik_pause(3, 2)
                if step == "passenger":
                    new_phase = await self._wait_booking_phase_transition(
                        after="passenger_continue", timeout=8
                    )
                    if new_phase != "passenger":
                        return
                else:
                    return

            for loc in locator_chain:
                try:
                    count = await loc.count()
                    if count == 0:
                        continue
                    btn = loc.last if step != "passenger" or count > 1 else loc.first
                    if not await self._passenger_continue_enabled(btn):
                        continue
                    await btn.scroll_into_view_if_needed()
                    try:
                        await btn.click(force=True, timeout=5000)
                    except Exception as exc:
                        last_err = str(exc)
                        await btn.evaluate("(el) => el.click()")
                    self._log(f"  → {step} Continue (#psgn-form)")
                    await self._anik_pause(3, 2)
                    return
                except Exception as exc:
                    last_err = str(exc)
                    continue

            await asyncio.sleep(0.5)

        phase = await self._detect_booking_phase()
        if phase == "payment":
            self._log(f"  → {step} skipped (payment page visible)")
            return

        if step == "passenger":
            hint = (
                "In Chrome: pick **UPI** or **IRCTC iPay**, then click orange **Continue**."
            )
        elif phase == "captcha":
            hint = (
                "In Chrome: type the **booking CAPTCHA** in the box, then click **Continue**."
            )
        else:
            hint = (
                "In Chrome: on **Review Booking**, click orange **Continue** "
                "(second sidebar if two are shown)."
            )

        self._log(f"  ⚠ Need manual step (phase={phase})")
        reply = await self._ask_human(f"{hint} Type DONE when captcha/review/payment appears:")
        if reply.strip().upper() == "DONE":
            self._log(f"  → {step} Continue (manual)")
            await self._anik_pause(2, 1)
            return

        detail = f" ({last_err})" if last_err else ""
        raise RuntimeError(
            f"Continue not clicked at step '{step}' (phase={phase})."
            + detail
        )

    async def _click_payment_continue(self, sidebar: bool = False) -> None:
        await self._dismiss_dimmer()
        if sidebar:
            btn = self.page.locator(
                "#psgn-form app-payment p-sidebar button, "
                "#psgn-form p-sidebar:nth-of-type(2) button"
            ).filter(has_text=re.compile(r"Continue", re.I)).last
        else:
            btn = self.page.locator("#psgn-form app-payment button").filter(
                has_text=re.compile(r"Continue", re.I)
            ).first
        if await btn.count() > 0 and await btn.first.is_visible():
            try:
                await btn.first.click(force=True)
            except Exception:
                await btn.first.evaluate("(el) => el.click()")
            self._log("  → payment Continue")
            await self._anik_pause(3, 2)

    async def _click_continue(self, label: str = "Continue") -> None:
        """Generic Continue — prefer scoped helpers for passenger/payment."""
        await self._dismiss_dimmer()
        btn = self.page.locator(
            '#psgn-form button:has-text("Continue"):not(.train_Search), '
            'p-sidebar button:has-text("Continue"):not(.train_Search)'
        ).last
        if await btn.count() > 0 and await btn.is_visible():
            try:
                await btn.click(force=True, timeout=8000)
            except Exception:
                await btn.evaluate("(el) => el.click()")
            self._log(f"  → {label}")
            await asyncio.sleep(2 + random.random())

    async def _handle_booking_captcha(self) -> None:
        captcha = self.page.locator(
            "#captcha, input[formcontrolname='captcha'], "
            'input[placeholder*="captcha" i]'
        )
        deadline = time.time() + 12
        visible = False
        while time.time() < deadline:
            try:
                if await captcha.count() > 0 and await captcha.first.is_visible():
                    visible = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4)
        if not visible:
            return

        self._log("  Booking CAPTCHA detected — solve in Chrome (15–25 sec)")
        text = await self._resolve_captcha()
        if text:
            await captcha.first.fill(text.strip())
            await self._click_passenger_continue("post-captcha")
        else:
            reply = await self._ask_human(
                "Enter the **booking CAPTCHA** in Chrome and click **Continue**. "
                "Type DONE when review or payment page loads:"
            )
            if reply.strip().upper() != "DONE":
                raise RuntimeError("Booking CAPTCHA not completed")

    async def _step_payment_handoff(self) -> None:
        self._log("📍 Step 6: Payment handoff (manual)")
        self._log("  ⚠ Payment is NOT automated — complete it in the browser")
        await self._ask_human(
            "Complete payment in Chrome. Type DONE when you see PNR / booking confirmation:"
        )
        self._log("✅ Booking flow finished")

    async def _keep_session_alive(self) -> None:
        seconds = self.config.keep_alive_seconds
        if seconds <= 0:
            return
        self._log(f"  Keeping session alive for {seconds}s (page refresh every 30s)")
        end = time.time() + seconds
        while time.time() < end:
            await asyncio.sleep(30)
            await self.page.reload(wait_until="domcontentloaded")
            self._log("  Session refresh")

    # ─── captcha (Claude only here) ────────────────────────────────────────

    async def _resolve_captcha(self) -> str:
        cap_input = self.page.locator(
            '#captcha, input[placeholder="Enter Captcha"], input[formcontrolname="captcha"]'
        )
        if not await cap_input.first.is_visible():
            return ""

        mode = self.config.captcha_mode
        self._log(f"  CAPTCHA detected (mode={mode})")

        if mode == "claude":
            text = await self._captcha_via_claude()
            if text:
                return text
            self._log("  Claude CAPTCHA failed — falling back to manual input")

        if mode == "terminal":
            return self._captcha_via_terminal()

        return await self._ask_human("Type the CAPTCHA exactly as shown in the login popup:")

    async def _captcha_via_claude(self) -> str:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return ""

        path = "captcha.png"
        canvas = self.page.locator("canvas").first
        if await canvas.is_visible():
            await canvas.screenshot(path=path)
        else:
            form = self.page.locator('form:has(input[placeholder="Enter Captcha"])').first
            await form.screenshot(path=path)

        with open(path, "rb") as fh:
            image_b64 = base64.standard_b64encode(fh.read()).decode()

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=self.config.anthropic_model,
            max_tokens=32,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is an IRCTC login CAPTCHA. Reply with ONLY the captcha "
                            "characters, no spaces or punctuation."
                        ),
                    },
                ],
            }],
        )
        text = response.content[0].text.strip()
        self._log(f"  Claude CAPTCHA guess: {text[:6]}...")
        return text

    def _captcha_via_terminal(self) -> str:
        try:
            return input("🔑 CAPTCHA (see captcha.png or browser): ").strip()
        except EOFError:
            return ""

    async def _maybe_handle_otp(self) -> bool:
        otp_input = self.page.locator('input[formcontrolname="otp"], input[placeholder*="OTP" i]')
        if not await otp_input.first.is_visible():
            return False

        otp = await self._ask_human("Enter OTP sent to your registered mobile:")
        if not otp.strip():
            return False
        await otp_input.first.fill(otp.strip())
        await self._click_sign_in()
        return True

    # ─── DOM helpers ───────────────────────────────────────────────────────

    async def _dismiss_popup(self) -> None:
        for selector in ('button:has-text("OK")', 'button:has-text("Accept")', 'text=OK'):
            btn = self.page.locator(selector).first
            if await btn.is_visible():
                await btn.click()
                await asyncio.sleep(0.5)
                return

    async def _open_login_modal(self) -> None:
        link = self.page.locator('a[aria-label="Click here to Login in application"]')
        if await link.is_visible():
            await link.click()
            return
        login = self.page.locator("a.loginText, a:has-text('LOGIN'), button:has-text('LOGIN')").first
        if await login.is_visible():
            await login.click()
            return
        await self.page.evaluate(
            "() => document.querySelector('a[aria-label=\"Click here to Login in application\"]')?.click()"
        )

    async def _click_sign_in(self) -> None:
        btn = self.page.get_by_role("button", name="Sign In")
        if await btn.is_visible():
            await btn.click()
            return
        submit = self.page.locator('button[type="submit"]:has-text("SIGN IN")').first
        if await submit.is_visible():
            await submit.click()

    async def _is_logged_in(self) -> bool:
        """Strict check — booking form alone does NOT mean logged in."""
        checks = [
            'a:has-text("Logout")',
            'a:has-text("LOGOUT")',
            'span:has-text("MY ACCOUNT")',
            'a:has-text("My Account")',
            '.user-name',
            'a.loginText.user-name',
        ]
        for sel in checks:
            try:
                if await self.page.locator(sel).first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _human_type(self, selector: str, text: str) -> None:
        loc = self.page.locator(selector).first
        await loc.click()
        await loc.fill("")
        for char in text:
            await loc.type(char, delay=100 + random.randint(0, 100))

    async def _fill_station(self, control: str, name: str, code: str) -> None:
        label = "From" if control == "origin" else "To"
        selectors = (
            'input[aria-label*="From station" i], input[formcontrolname="origin"], input[placeholder*="From" i]'
            if control == "origin"
            else 'input[aria-label*="To station" i], input[formcontrolname="destination"], input[placeholder*="To" i]'
        )
        exclude = ["BEACH", "MSB"] if code.upper() == "MAS" else []

        field = self.page.locator(selectors).first
        await field.click()
        await field.fill("")

        # Type station code first — avoids picking "Chennai Beach" when MAS is intended
        for query in (code.upper(), name[:30]):
            await field.click()
            await field.fill("")
            await self._human_type(selectors, query)
            await asyncio.sleep(1.4 + random.random())
            if await self._pick_station_from_autocomplete(code.upper(), exclude):
                break
            await self.page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.25)
            await self.page.keyboard.press("Enter")
            await asyncio.sleep(0.5)
            if await self._station_value_ok(selectors, code.upper(), exclude):
                break

        if not await self._station_value_ok(selectors, code.upper(), exclude):
            raise RuntimeError(
                f"{label} station not set to {code} — got wrong autocomplete (e.g. Chennai Beach?). "
                "Fix IRCTC_TO_STATION=MAS in .env and retry."
            )

        value = await self.page.locator(selectors).first.input_value()
        self._log(f"  → {label}: {code} ({value[:40]})")

    async def _pick_station_from_autocomplete(self, code: str, exclude: list[str]) -> bool:
        exclude_json = str(exclude).replace("'", '"')
        code_json = code
        return bool(
            await self.page.evaluate(
                f"""
                () => {{
                    const code = "{code_json}";
                    const exclude = {exclude_json};
                    const items = document.querySelectorAll(
                        'p-autocomplete-panel li, .ui-autocomplete-list-item, '
                        + '[role="listbox"] [role="option"], .cdk-overlay-pane li'
                    );
                    for (const li of items) {{
                        const r = li.getBoundingClientRect();
                        if (r.width < 2 || r.height < 2) continue;
                        const t = (li.innerText || '').trim().toUpperCase();
                        if (!t.includes(code)) continue;
                        if (exclude.some((w) => t.includes(w))) continue;
                        li.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true }}));
                        li.dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
                        li.click();
                        return true;
                    }}
                    return false;
                }}
                """
            )
        )

    async def _station_value_ok(self, selectors: str, code: str, exclude: list[str]) -> bool:
        value = (await self.page.locator(selectors).first.input_value()).upper()
        if code not in value:
            return False
        return not any(word in value for word in exclude)

    async def _fill_journey_date(self, date_str: str) -> None:
        """Set journey date — Anik uses #jDate calendar click."""
        day, month, year = date_str.split("/")
        target_day = int(day)

        jdate = self.page.locator("#jDate span input, #jDate input").first
        if await jdate.is_visible():
            await jdate.click()
            await asyncio.sleep(0.6)
            day_link = self.page.locator(
                f"#jDate td a:has-text('{target_day}'), "
                f"#jDate table tbody td a:has-text('{target_day}')"
            )
            if await day_link.count() > 0:
                await day_link.first.click()
                self._log(f"  → Date: {date_str} (#jDate calendar)")
                return

        field = self.page.locator(
            'input[formcontrolname="journeyDate"], #jDate input, input[placeholder*="Date" i]'
        ).first
        if not await field.is_visible():
            return

        await field.click()
        await asyncio.sleep(0.6)

        # Calendar day link (PrimeNG / IRCTC datepicker)
        day_link = self.page.locator(
            f'.ui-datepicker-calendar td a:has-text("{target_day}"), '
            f'table tbody td a:has-text("{target_day}"), '
            f'.p-datepicker-calendar td span:has-text("{target_day}")'
        ).filter(has_not_text=re.compile(r"^\s*$"))

        if await day_link.count() > 0:
            for i in range(await day_link.count()):
                link = day_link.nth(i)
                if await link.is_visible():
                    await link.click()
                    self._log(f"  → Date: {date_str} (calendar)")
                    await asyncio.sleep(0.4)
                    return

        await field.fill("")
        await self._human_type(
            'input[formcontrolname="journeyDate"], #jDate input, input[placeholder*="Date" i]',
            date_str,
        )
        await self.page.keyboard.press("Tab")
        self._log(f"  → Date: {date_str} (typed)")
        await asyncio.sleep(0.3)

    async def _select_class_filter_on_results(self, class_code: str) -> None:
        """Some IRCTC layouts show a class dropdown after search — set SL here."""
        label = CLASS_LABELS.get(class_code, class_code)
        triggers = self.page.locator(
            'p-dropdown[formcontrolname*="class" i], #journeyClass, '
            'p-dropdown:has-text("Class"), p-dropdown:has-text("All Classes")'
        )
        if await triggers.count() == 0:
            return
        trigger = triggers.first
        if not await trigger.is_visible():
            return
        current = (await trigger.inner_text()).upper()
        if class_code in current and "ALL" not in current:
            return
        await trigger.click()
        await asyncio.sleep(0.5)
        opt = self.page.locator(
            f'li:has-text("{label}"), li:has-text("{class_code}"), p-dropdownitem li:has-text("{class_code}")'
        ).first
        if await opt.is_visible():
            await opt.click()
            self._log(f"  → Results filtered to {class_code}")
            await asyncio.sleep(2)

    async def _select_search_class(self, class_code: str = "", all_classes: bool = False) -> None:
        """Set class on search form — default All Classes, pick SL on train row later."""
        trigger = self.page.locator(
            '#journeyClass, p-dropdown[formcontrolname*="class" i], p-dropdown[formcontrolname*="Class" i]'
        ).first
        if not await trigger.is_visible():
            return
        await trigger.click()
        await asyncio.sleep(0.6)
        if all_classes:
            option = self.page.locator(
                'li:has-text("All Classes"), span:has-text("All Classes")'
            ).first
            if await option.is_visible():
                await option.click()
                self._log("  → Search class: All Classes (SL picked on train row)")
                return
        label = CLASS_LABELS.get(class_code, class_code)
        option = self.page.locator(f'li:has-text("{label}"), li:has-text("{class_code}")').first
        if await option.is_visible():
            await option.click()
            self._log(f"  → Search class: {class_code}")

    async def _select_class(self, class_code: str) -> None:
        await self._select_search_class(class_code=class_code, all_classes=False)

    async def _click_search(self) -> bool:
        btn = self.page.locator(
            'button.search_btn, button:has-text("Search Trains"), button:has-text("Search")'
        ).first
        if await btn.is_visible():
            await btn.click()
            return True
        return False

    async def _has_train_results(self) -> bool:
        rows = self.page.locator("app-train-list app-train-avl-enq, app-train-avl-enq")
        if await rows.count() >= 1:
            return True
        text = await self.page.inner_text("body")
        preferred = self.config.preferred_train
        return bool(preferred and preferred in text)

    async def _fill_passenger_row(self, index: int, passenger: Passenger) -> None:
        self._log(f"  Passenger {index + 1}: {passenger.name}")

        name_selectors = [
            "app-passenger p-autocomplete input",
            "app-passenger input[formcontrolname='passengerName']",
            'input[placeholder*="Passenger Name" i]',
            "app-passenger input[type='text']",
        ]
        for sel in name_selectors:
            inputs = self.page.locator(sel)
            if await inputs.count() > index:
                field = inputs.nth(index)
                if await field.is_visible():
                    await field.scroll_into_view_if_needed()
                    await field.click()
                    await field.fill(passenger.name)
                    break

        age_selectors = [
            "app-passenger input[formcontrolname='passengerAge']",
            "app-passenger input[type='number']",
            'input[placeholder*="Age" i]',
        ]
        for sel in age_selectors:
            inputs = self.page.locator(sel)
            if await inputs.count() > index:
                field = inputs.nth(index)
                if await field.is_visible():
                    await field.fill(passenger.age)
                    break

        row = self.page.locator("app-passenger").nth(index)
        row_selects = row.locator("select")
        row_count = await row_selects.count()
        if row_count >= 1:
            gender_label = passenger.gender
            if not gender_label.lower().startswith(("m", "f", "t")):
                gender_label = "Male"
            try:
                await row_selects.nth(0).select_option(label=gender_label)
            except Exception:
                try:
                    g = "M" if gender_label.upper().startswith("M") else "F"
                    await row_selects.nth(0).select_option(value=g)
                except Exception:
                    self._log(f"  ⚠ Gender not set for passenger {index + 1}")
        elif await self.page.locator("app-passenger select, #psgn-form select").count() > index:
            gs = self.page.locator("app-passenger select, #psgn-form select")
            try:
                await gs.nth(index).select_option(label=passenger.gender)
            except Exception:
                pass

        if passenger.berth and row_count >= 2:
            try:
                await row_selects.nth(1).select_option(label=passenger.berth)
            except Exception:
                pass
        elif passenger.berth:
            berth_selects = self.page.locator("app-passenger select")
            n = await berth_selects.count()
            berth_idx = min(index * 2 + 1, n - 1) if n > 1 else index
            if n > berth_idx:
                try:
                    await berth_selects.nth(berth_idx).select_option(label=passenger.berth)
                except Exception:
                    pass

    async def _fill_mobile(self, mobile_number: str) -> bool:
        """Fill mobile inside passenger form only — never the first page-wide tel input."""
        digits = re.sub(r"\D", "", mobile_number or "")
        if len(digits) != 10:
            return False

        root = self.page.locator("#psgn-form, app-passenger").first
        selectors = [
            "#psgn-form #mobileNumber",
            "#psgn-form input[formcontrolname='mobileNumber']",
            "#psgn-form input[name='mobileNumber']",
            "app-passenger input[formcontrolname='mobileNumber']",
            '#psgn-form input[placeholder*="Mobile" i]',
            '#psgn-form input[placeholder*="Contact" i]',
        ]

        async def _try_field(loc) -> bool:
            try:
                if await loc.count() == 0 or not await loc.first.is_visible():
                    return False
                field = loc.first
                maxlen = await field.get_attribute("maxlength") or ""
                if maxlen.isdigit() and int(maxlen) < 10:
                    return False
                await field.scroll_into_view_if_needed()
                await field.click()
                await field.fill("")
                await field.fill(digits)
                val = re.sub(r"\D", "", await field.input_value() or "")
                if val == digits or digits in val:
                    await field.press("Tab")
                    await asyncio.sleep(0.3)
                    return True
            except Exception:
                return False
            return False

        for attempt in range(3):
            for sel in selectors:
                if await _try_field(root.locator(sel)):
                    return True
            try:
                label_field = root.get_by_label(
                    re.compile(r"mobile|contact", re.I)
                ).first
                if await _try_field(label_field):
                    return True
            except Exception:
                pass
            try:
                await root.evaluate(
                    """(el, mobile) => {
                        const inputs = el.querySelectorAll('input');
                        for (const inp of inputs) {
                            const ph = (inp.placeholder || '').toLowerCase();
                            const fc = (inp.getAttribute('formcontrolname') || '').toLowerCase();
                            const id = (inp.id || '').toLowerCase();
                            const name = (inp.name || '').toLowerCase();
                            const blob = ph + fc + id + name;
                            if (!/mobile|contact/.test(blob)) continue;
                            const max = parseInt(inp.maxLength || '99', 10);
                            if (max > 0 && max < 10) continue;
                            inp.focus();
                            inp.value = mobile;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                        return false;
                    }""",
                    digits,
                )
                check = root.locator(
                    "input[formcontrolname='mobileNumber'], #mobileNumber"
                ).first
                if await check.count() > 0:
                    val = re.sub(r"\D", "", await check.input_value() or "")
                    if val == digits:
                        return True
            except Exception:
                pass
            await self.page.evaluate(
                "document.querySelector('#psgn-form')?.scrollBy(0, 350)"
            )
            await asyncio.sleep(0.6)
        return False

    async def _ask_human(self, question: str) -> str:
        self.status = "waiting"
        self._log(f"HUMAN INPUT NEEDED: {question}")
        if self.human_callback:
            return self.human_callback(question)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: input(f"\n{question}\n> ").strip())

    def _log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log.append(line)
        self._log_fn(line)
