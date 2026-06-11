"""Playwright automation for TTD Special Entry Darshan (Sri PAT) slot booking."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from agent.scripted_common import click_first, click_text, fill_first

if TYPE_CHECKING:
    from agent.human_prompts import PilgrimDetail
    from agent.playwright_engine import PlaywrightEngine
    from agent.travel_runner import TravelRun
else:
    from agent.human_prompts import PilgrimDetail

SPAT_SLOT_URL = (
    "https://ttdevasthanams.ap.gov.in/spat/slot-booking?flow=spat&flowIdentifier=spat"
)

TTD_URL_MARKERS = ("ttdevasthanams", "ttdsevaonline", "tirupatibalaji")

MOBILE_SELECTORS = (
    "#mobileNo",
    "input[name='mobile']",
    'input[placeholder*="mobile" i]',
    'input[id*="mobile" i]',
    'input[formcontrolname*="mobile" i]',
    'input[type="tel"]',
)

OTP_SELECTORS = (
    'input[placeholder*="otp" i]',
    'input[name*="otp" i]',
    'input[id*="otp" i]',
    'input[formcontrolname*="otp" i]',
    'input[inputmode="numeric"]',
)

SEND_OTP_SELECTORS = (
    'button:has-text("Send OTP")',
    'button:has-text("Get OTP")',
    'button:has-text("Generate OTP")',
    'button:has-text("Request OTP")',
    'button:has-text("Login")',
    'button:has-text("Sign In")',
    'button:has-text("Submit")',
    'input[value="Send OTP"]',
    'input[value="Login"]',
    'button[type="submit"]',
)

VERIFY_OTP_SELECTORS = (
    'button:has-text("Verify")',
    'button:has-text("Verify OTP")',
    'button:has-text("Submit")',
    'button:has-text("Login")',
    'button:has-text("Sign In")',
    'button[type="submit"]',
)


def _is_ttd_url(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in TTD_URL_MARKERS)


def _ttd_dom_js() -> str:
    """Shared DOM helpers for ttdevasthanams.ap.gov.in (Angular login modal)."""
    return """
        const bodyText = () => (document.body.innerText || '').slice(0, 12000).toLowerCase();
        const visibleButtons = () => [...document.querySelectorAll('button')].filter(
            (b) => b.offsetParent !== null && (b.innerText || '').trim()
        );
        const visibleInputs = () => [...document.querySelectorAll('input')].filter((inp) => {
            if (inp.type === 'hidden' || inp.disabled) return false;
            const r = inp.getBoundingClientRect();
            return r.width > 12 && r.height > 8;
        });
        const isCountryCodeField = (inp) => {
            const val = (inp.value || inp.getAttribute('name') || '').trim();
            return /^\\(\\+91\\)/i.test(val);
        };
        const getOtpButton = () => visibleButtons().find(
            (b) => /^get otp$/i.test((b.innerText || '').trim())
        );
        const loginSubmitButton = () => visibleButtons().find(
            (b) => /^login$/i.test((b.innerText || '').trim())
        );
        const otpDigitInputs = () => visibleInputs().filter((inp) => {
            if (isCountryCodeField(inp)) return false;
            const r = inp.getBoundingClientRect();
            return r.width >= 16 && r.width <= 72;
        });
        const mobileInput = () => {
            for (const inp of visibleInputs()) {
                if (isCountryCodeField(inp)) continue;
                const r = inp.getBoundingClientRect();
                if (r.width > 90) return inp;
            }
            const getOtp = getOtpButton();
            if (!getOtp) return null;
            const modal = getOtp.closest('div, section, form, dialog, [role="dialog"]');
            if (!modal) return null;
            for (const inp of modal.querySelectorAll('input')) {
                if (inp.type === 'hidden' || inp.disabled || isCountryCodeField(inp)) continue;
                const r = inp.getBoundingClientRect();
                if (r.width > 90) return inp;
            }
            return null;
        };
    """


async def _ttd_logged_in(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                f"""
                () => {{
                    {_ttd_dom_js()}
                    const t = bodyText();
                    if (/logout|sign out|my bookings|my profile|welcome back/i.test(t)) return true;
                    if (getOtpButton() || loginSubmitButton() || /waiting for otp/i.test(t)) return false;
                    const url = (location.href || '').toLowerCase();
                    if (/slot-booking|spat|pilgrim|calendar/i.test(url)) {{
                        const loginBtn = visibleButtons().find((b) => {{
                            const txt = (b.innerText || '').trim().toLowerCase();
                            return txt === 'login' || txt === 'sign in';
                        }});
                        if (!loginBtn) return true;
                    }}
                    if (/slot|calendar|darshan|pilgrim|dashboard/i.test(t) && !getOtpButton()) return true;
                    return false;
                }}
                """
            )
        )
    except Exception:
        return False


async def _ttd_login_screen_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                f"""
                () => {{
                    {_ttd_dom_js()}
                    if (/waiting for otp/i.test(bodyText())) return false;
                    if (otpDigitInputs().length >= 4) return false;
                    return !!getOtpButton();
                }}
                """
            )
        )
    except Exception:
        return False


async def _ttd_otp_screen_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                f"""
                () => {{
                    {_ttd_dom_js()}
                    const t = bodyText();
                    if (/waiting for otp|enter otp|verify otp|one time password|otp sent/i.test(t))
                        return true;
                    const digits = otpDigitInputs();
                    return digits.length >= 4 && !!loginSubmitButton();
                }}
                """
            )
        )
    except Exception:
        return False


async def _open_ttd_login(page, run: "TravelRun") -> bool:
    if await _ttd_login_screen_visible(page) or await _ttd_otp_screen_visible(page):
        return True
    for pattern in (r"^login$", r"sign\s*in", r"login\s*/\s*register"):
        if await click_text(page, pattern, timeout=2000):
            run._log("→ Opened TTD login")
            await asyncio.sleep(1.0)
            return True
    if await click_first(
        page,
        (
            'a:has-text("Login")',
            'button:has-text("Login")',
            'a:has-text("Sign In")',
        ),
    ):
        run._log("→ Opened TTD login")
        await asyncio.sleep(1.0)
        return True
    return False


async def _get_ttd_mobile_locator(page):
    """Wide visible input in TTD login modal (not the (+91) country-code field)."""
    try:
        count = await page.locator("input:visible").count()
        for i in range(count):
            loc = page.locator("input:visible").nth(i)
            val = (await loc.input_value()) or ""
            aria = (await loc.get_attribute("name")) or ""
            if "(+91)" in val or "(+91)" in aria:
                continue
            box = await loc.bounding_box()
            if box and box["width"] > 90:
                return loc
    except Exception:
        pass
    return None


async def _get_otp_button_state(page) -> str:
    """Return 'ready', 'disabled', or 'missing' for the Get OTP button."""
    try:
        return str(
            await page.evaluate(
                f"""
                () => {{
                    {_ttd_dom_js()}
                    const btn = getOtpButton();
                    if (!btn || btn.offsetParent === null) return 'missing';
                    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'disabled';
                    return 'ready';
                }}
                """
            )
        )
    except Exception:
        return "missing"


async def _focus_ttd_mobile_input(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                f"""
                () => {{
                    {_ttd_dom_js()}
                    const inp = mobileInput();
                    if (!inp) return false;
                    inp.focus();
                    inp.click();
                    return true;
                }}
                """
            )
        )
    except Exception:
        return False


async def _fill_ttd_mobile(page, run: "TravelRun", mobile: str) -> bool:
    from agent.scripted_common import _normalize_india_mobile

    digits = _normalize_india_mobile(mobile)
    if not digits:
        return False

    # Angular needs real keyboard input — JS .value= alone leaves Get OTP disabled.
    field = await _get_ttd_mobile_locator(page)
    if field:
        try:
            await field.scroll_into_view_if_needed()
            await field.click(click_count=3)
            await page.keyboard.press("Backspace")
            await field.press_sequentially(digits, delay=45)
            await asyncio.sleep(0.35)
            run._log(f"→ TTD mobile entered: {digits[:4]}******")
            return True
        except Exception:
            pass

    if await _focus_ttd_mobile_input(page):
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(digits, delay=45)
            await asyncio.sleep(0.35)
            run._log(f"→ TTD mobile entered: {digits[:4]}******")
            return True
        except Exception:
            pass

    if await fill_first(page, MOBILE_SELECTORS, digits):
        run._log(f"→ TTD mobile entered: {digits[:4]}******")
        return True
    return False


async def _click_ttd_send_otp(page, run: "TravelRun") -> bool:
    import time

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        state = await _get_otp_button_state(page)
        if state == "ready":
            try:
                clicked = await page.evaluate(
                    f"""
                    () => {{
                        {_ttd_dom_js()}
                        const btn = getOtpButton();
                        if (!btn || btn.disabled) return false;
                        btn.click();
                        return true;
                    }}
                    """
                )
                if clicked:
                    run._log("→ Clicked **Get OTP** on TTD")
                    await asyncio.sleep(2.0)
                    return True
            except Exception:
                pass
            try:
                btn = page.get_by_role("button", name=re.compile(r"get\s*otp", re.I)).first
                await btn.click(timeout=3000)
                run._log("→ Clicked **Get OTP** on TTD")
                await asyncio.sleep(2.0)
                return True
            except Exception:
                pass
        if state == "disabled":
            await asyncio.sleep(0.35)
            continue
        break

    for loc in (
        page.get_by_role("button", name=re.compile(r"get\s*otp", re.I)),
        page.locator('button:has-text("Get OTP")'),
    ):
        try:
            btn = loc.first
            if await btn.is_visible():
                await btn.click(force=True, timeout=3000)
                run._log("→ Clicked Get OTP on TTD (force)")
                await asyncio.sleep(2.0)
                return True
        except Exception:
            continue

    if await click_text(page, r"^get\s*otp$", timeout=2000):
        run._log("→ Clicked Get OTP on TTD")
        await asyncio.sleep(2.0)
        return True
    return False


async def _fill_mobile_and_get_otp(page, run: "TravelRun", digits: str) -> bool:
    """Type mobile with keyboard, wait for Get OTP to enable, then click it."""
    for attempt in range(3):
        if not await _fill_ttd_mobile(page, run, digits):
            run._log("⚠ Could not auto-fill mobile — check Chrome login modal")
            return False
        await asyncio.sleep(0.4)
        state = await _get_otp_button_state(page)
        if state == "ready" and await _click_ttd_send_otp(page, run):
            return True
        if attempt < 2:
            run._log(f"→ Get OTP not ready — retyping mobile (attempt {attempt + 2}/3)")
            await asyncio.sleep(0.5)
    return await _click_ttd_send_otp(page, run)


async def _submit_ttd_otp(page, run: "TravelRun", otp: str) -> bool:
    otp = re.sub(r"\D", "", otp or "")
    if not otp:
        return False

    filled = False
    try:
        narrow = []
        for i in range(await page.locator("input:visible").count()):
            field = page.locator("input:visible").nth(i)
            val = (await field.input_value()) or ""
            if "(+91)" in val:
                continue
            box = await field.bounding_box()
            if box and box["width"] <= 72:
                narrow.append(field)
        if len(narrow) >= 4:
            await narrow[0].click()
            await narrow[0].press_sequentially(otp[: len(narrow)], delay=60)
            filled = True
        elif len(narrow) == 1:
            await narrow[0].click()
            await narrow[0].press_sequentially(otp, delay=60)
            filled = True
    except Exception:
        pass

    if not filled:
        filled = bool(
            await page.evaluate(
                f"""
                (code) => {{
                    {_ttd_dom_js()}
                    const inputs = otpDigitInputs();
                    if (inputs.length >= 4 && inputs.length <= 8) {{
                        inputs[0].focus();
                        inputs[0].click();
                        return true;
                    }}
                    return false;
                }}
                """,
                otp,
            )
        )
        if filled:
            try:
                await page.keyboard.type(otp, delay=60)
            except Exception:
                filled = False

    if not filled:
        await fill_first(page, OTP_SELECTORS, otp)

    import time

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        try:
            login_btn = page.get_by_role("button", name=re.compile(r"^Login$", re.I)).first
            if await login_btn.is_visible() and not await login_btn.is_disabled():
                await login_btn.click()
                run._log("→ TTD OTP submitted — clicked Login")
                await asyncio.sleep(2.5)
                return True
        except Exception:
            pass
        try:
            clicked = await page.evaluate(
                f"""
                () => {{
                    {_ttd_dom_js()}
                    const btn = loginSubmitButton();
                    if (!btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
                    btn.click();
                    return true;
                }}
                """
            )
            if clicked:
                run._log("→ TTD OTP submitted — clicked Login")
                await asyncio.sleep(2.5)
                return True
        except Exception:
            pass
        await asyncio.sleep(0.35)

    if await click_first(page, VERIFY_OTP_SELECTORS):
        run._log("→ TTD OTP submitted")
        await asyncio.sleep(2.5)
        return True
    if await click_text(page, r"^login$|^verify|submit", timeout=3000):
        run._log("→ TTD OTP submitted")
        await asyncio.sleep(2.5)
        return True
    return filled


async def _wait_ttd_otp_screen(page, timeout: float = 12.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _ttd_otp_screen_visible(page):
            return True
        await asyncio.sleep(0.4)
    return await _ttd_otp_screen_visible(page)


async def ensure_ttd_logged_in(
    page,
    run: "TravelRun",
    wait_human: Callable[[str], Awaitable[str]],
    mobile: str = "",
) -> bool:
    """IRCTC-style TTD login: ask mobile → Get OTP in Chrome → ask OTP → Login."""
    from agent.human_prompts import TAG_CONFIRM_DONE, TAG_LOGIN_FORM, TAG_OTP
    from agent.scripted_common import _normalize_india_mobile, ask_tagged, dismiss_cookie_banner

    if not _is_ttd_url(page.url or ""):
        return True

    await dismiss_cookie_banner(page)

    # Only use mobile explicitly entered in the gov form — never silently from .env
    digits = _normalize_india_mobile(mobile)

    for _round in range(5):
        if await _ttd_logged_in(page):
            run._log("✅ TTD login confirmed")
            return True

        await _open_ttd_login(page, run)
        await dismiss_cookie_banner(page)

        if not await _ttd_login_screen_visible(page) and not await _ttd_otp_screen_visible(page):
            if await _ttd_logged_in(page):
                return True
            run._log("⚠ TTD login screen not detected — check Chrome")
            reply = await ask_tagged(
                wait_human,
                TAG_CONFIRM_DONE,
                "Complete **TTD login** in Chrome (mobile → Get OTP → OTP), then confirm.",
                "TTD login done",
            )
            if reply.upper() == "CANCEL":
                return False
            return await _ttd_logged_in(page)

        # Step 1 — mobile number (Streamlit asks first, then agent fills Chrome + Get OTP)
        if await _ttd_login_screen_visible(page):
            run._log("📱 TTD login — step 1: registered mobile number")
            if not digits:
                mobile_resp = await ask_tagged(
                    wait_human,
                    TAG_LOGIN_FORM,
                    "**Step 1 of 2:** Enter your **TTD registered mobile number**. "
                    "The agent will type it in Chrome and click **Get OTP**.",
                    "mobile",
                )
                if mobile_resp.upper() == "CANCEL":
                    return False
                digits = _normalize_india_mobile(mobile_resp)

            if digits:
                if not await _fill_mobile_and_get_otp(page, run, digits):
                    run._log("⚠ Get OTP failed — check mobile number in Chrome login modal")
                await _wait_ttd_otp_screen(page)
            else:
                run._log("⚠ No mobile — complete login manually in Chrome")
                reply = await ask_tagged(
                    wait_human,
                    TAG_CONFIRM_DONE,
                    "On TTD **login**, enter your **mobile**, click **Get OTP**, then confirm.",
                    "Mobile submitted",
                )
                if reply.upper() == "CANCEL":
                    return False
                await _wait_ttd_otp_screen(page, timeout=8.0)
                continue

        # Step 2 — OTP from SMS (only after Get OTP / 6-digit boxes visible)
        if await _ttd_otp_screen_visible(page):
            run._log("📱 TTD login — step 2: OTP from SMS")
            mask = f"{digits[:4]}******" if digits else "your mobile"
            otp = await ask_tagged(
                wait_human,
                TAG_OTP,
                f"**Step 2 of 2:** TTD sent a **6-digit OTP** to **{mask}**. "
                f"Enter the OTP from SMS below (type **SKIP** if already logged in).",
            )
            if otp.upper() in ("", "SKIP"):
                if await _ttd_logged_in(page):
                    return True
                continue
            if otp.upper() == "CANCEL":
                return False
            await _submit_ttd_otp(page, run, otp)
            await asyncio.sleep(3.0)
            if await _ttd_logged_in(page):
                run._log("✅ TTD login successful")
                return True
            continue

        if not await _ttd_login_screen_visible(page) and not await _ttd_otp_screen_visible(page):
            if await _ttd_logged_in(page):
                return True

    if await _ttd_login_screen_visible(page) or await _ttd_otp_screen_visible(page):
        run._log("⚠ TTD login incomplete — finish in Chrome")
        reply = await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Finish **TTD login** in Chrome, then confirm.",
            "TTD login done",
        )
        return reply.upper() != "CANCEL" and await _ttd_logged_in(page)

    return await _ttd_logged_in(page)

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _parse_date(value: str) -> tuple[int, int, int] | None:
    parts = value.replace("-", "/").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        if 1 <= day <= 31 and 1 <= month <= 12 and year >= 2020:
            return day, month, year
    except ValueError:
        pass
    return None


async def open_spat_slot_booking(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    *,
    fallback_urls: tuple[str, ...] = (),
) -> bool:
    from agent.scripted_common import open_url, open_url_with_fallbacks

    if await open_url(engine, run, SPAT_SLOT_URL, label="TTD Sri PAT slot booking"):
        return True
    urls = [u for u in fallback_urls if u and u != SPAT_SLOT_URL]
    if urls:
        run._log("→ Direct slot URL failed — trying portal home first")
        if await open_url_with_fallbacks(engine, run, urls, label="TTD portal"):
            if await click_text(engine.page, r"special\s*entry\s*darshan"):
                await asyncio.sleep(0.6)
            if await click_text(engine.page, r"darshan\s*slots"):
                await asyncio.sleep(0.8)
            return "slot" in (engine.page.url or "").lower() or "spat" in (engine.page.url or "").lower()
    return False


async def _select_month_tab(page, month: int, year: int) -> bool:
    label = f"{MONTH_NAMES[month - 1]} {year}"
    if await click_text(page, re.escape(label), timeout=5000):
        await asyncio.sleep(0.6)
        return True
    short = MONTH_NAMES[month - 1][:3]
    if await click_text(page, rf"{short}\.?\s*{year}", timeout=3000):
        await asyncio.sleep(0.6)
        return True
    return False


async def _ttd_calendar_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const t = (document.body.innerText || '').slice(0, 8000).toLowerCase();
                    return /darshan slots|special entry darshan/i.test(t)
                        && /available|quota is full|quota not released/i.test(t);
                }
                """
            )
        )
    except Exception:
        return False


async def _ttd_slot_screen_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const t = (document.body.innerText || '').slice(0, 12000).toLowerCase();
                    if (/select any\\s*1\\s*slot|number of tickets|slot time/i.test(t))
                        return true;
                    const radios = [...document.querySelectorAll('input[type="radio"]')].filter(
                        (r) => r.offsetParent !== null
                    );
                    return radios.length >= 2 && /available|₹200|please select a darshan/i.test(t);
                }
                """
            )
        )
    except Exception:
        return False


async def _ttd_pilgrim_screen_visible(page) -> bool:
    """True when pilgrim Name/Age/Gender fields exist (same page as slot picker is OK)."""
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const url = (location.pathname || '').toLowerCase();
                    if (/pilgrim-detail|pilgrim_details/.test(url)) return true;
                    const t = (document.body.innerText || '').slice(0, 16000).toLowerCase();
                    if (/pilgrim details|pilgrim detail|devotee detail|ticket holder detail/i.test(t))
                        return true;
                    if (/darshan details/i.test(t) && /photo id proof|photo id number/i.test(t))
                        return true;
                    const labels = [...document.querySelectorAll('label, mat-label, .mat-mdc-floating-label')].map(
                        (el) => (el.innerText || '').replace(/\\*/g, '').trim().toLowerCase()
                    );
                    const hasName = labels.some((l) => /^name$/.test(l) || l.startsWith('name'));
                    const hasAge = labels.some((l) => /^age$/.test(l) || l.startsWith('age'));
                    const hasGender = labels.some((l) => /gender/.test(l));
                    const hasId = labels.some((l) => /photo id/.test(l));
                    if (hasName && hasAge && hasGender) return true;
                    if (hasName && hasAge && hasId) return true;
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _ttd_slot_needs_selection(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                f"""
                () => {{
                    {_slot_page_js()}
                    return slotNeedsSelection();
                }}
                """
            )
        )
    except Exception:
        return False


def _slot_page_js() -> str:
    return """
        const slotRoot = () => {
            for (const el of document.querySelectorAll('div, section, main, form')) {
                const t = (el.innerText || '');
                if (!/select any\\s*1\\s*slot/i.test(t)) continue;
                if (t.length > 8000) continue;
                return el;
            }
            return document.body;
        };

        const slotSelected = () => {
            const root = slotRoot();
            if (root.querySelector('input[type="radio"]:checked')) return true;
            if (root.querySelector(
                'mat-radio-button.mat-mdc-radio-checked, mat-radio-button.mat-radio-checked, ' +
                '.mat-mdc-radio-checked, .mdc-radio--checked'
            )) return true;
            if (root.querySelector(
                'mat-radio-button[aria-checked="true"], [role="radio"][aria-checked="true"]'
            )) return true;
            for (const rb of root.querySelectorAll('mat-radio-button, [role="radio"]')) {
                const cls = (rb.className || '').toString().toLowerCase();
                if (/checked|selected/.test(cls)) return true;
                if (rb.getAttribute('aria-checked') === 'true') return true;
            }
            const t = (document.body.innerText || '').toLowerCase();
            if (/please select a darshan ticket/i.test(t)) return false;
            if (continueReady() === 'ready') return true;
            if (/darshan details/i.test(t) && /darshan slot/i.test(t)
                && /\\d{1,2}:\\d{2}\\s*(am|pm)/i.test(t)) return true;
            return false;
        };

        const slotNeedsSelection = () => {
            if (slotSelected()) return false;
            const t = (document.body.innerText || '').toLowerCase();
            return /please select a darshan ticket/i.test(t)
                || (/select any\\s*1\\s*slot/i.test(t) && !slotSelected());
        };

        const slotCards = () => {
            const root = slotRoot();
            const cards = [...root.querySelectorAll('div, section, label, article, li')].filter((el) => {
                const t = (el.innerText || '').trim();
                if (t.length < 12 || t.length > 260) return false;
                if (!/\\d+\\s*available/i.test(t)) return false;
                if (!/slot time|\\d{1,2}:\\d{2}\\s*(am|pm)/i.test(t)) return false;
                const r = el.getBoundingClientRect();
                return r.width > 70 && r.height > 28 && el.offsetParent !== null;
            });
            cards.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const uniq = [];
            const seen = new Set();
            for (const c of cards) {
                const key = (c.innerText || '').slice(0, 40);
                if (seen.has(key)) continue;
                seen.add(key);
                const nested = cards.some(
                    (o) => o !== c && o.contains(c) && (o.innerText || '').length < (c.innerText || '').length + 30
                );
                if (!nested) uniq.push(c);
            }
            return uniq.length ? uniq : cards;
        };

        const findContinueButton = () => {
            const candidates = [...document.querySelectorAll(
                'button, a, input[type="button"], input[type="submit"], [role="button"]'
            )].filter((b) => {
                const label = (b.innerText || b.value || b.textContent || '').trim();
                if (!/^continue$/i.test(label)) return false;
                const r = b.getBoundingClientRect();
                return b.offsetParent !== null && r.width > 28 && r.height > 18;
            });
            if (!candidates.length) return null;
            candidates.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
            return candidates[0];
        };

        const continueReady = () => {
            const btn = findContinueButton();
            if (!btn) return 'missing';
            if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'disabled';
            if (btn.classList.contains('mat-mdc-button-disabled') || btn.classList.contains('mat-button-disabled'))
                return 'disabled';
            return 'ready';
        };

        const clickContinueButton = () => {
            const btn = findContinueButton();
            if (!btn || continueReady() !== 'ready') return null;
            btn.scrollIntoView({ block: 'center', inline: 'center' });
            const r = btn.getBoundingClientRect();
            const x = r.x + r.width / 2;
            const y = r.y + r.height / 2;
            const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
            btn.dispatchEvent(new PointerEvent('pointerdown', opts));
            btn.dispatchEvent(new MouseEvent('mousedown', opts));
            btn.dispatchEvent(new PointerEvent('pointerup', opts));
            btn.dispatchEvent(new MouseEvent('mouseup', opts));
            btn.dispatchEvent(new MouseEvent('click', opts));
            btn.click();
            return { x, y };
        };

        const clickSlotAt = (el) => {
            if (!el) return null;
            el.scrollIntoView({ block: 'center', inline: 'center' });
            const r = el.getBoundingClientRect();
            const x = r.x + r.width / 2;
            const y = r.y + r.height / 2;
            const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
            el.click();
            const radio = el.querySelector?.(
                'input[type="radio"], mat-radio-button, [role="radio"]'
            ) || el.closest?.('mat-radio-button, label, div')?.querySelector(
                'input[type="radio"], mat-radio-button'
            );
            radio?.click();
            return { x, y, label: (el.innerText || '').slice(0, 80) };
        };

        const clickFirstSlot = () => {
            const cards = slotCards();
            for (const card of cards) {
                const rb = card.querySelector('mat-radio-button, [role="radio"]')
                    || card.parentElement?.querySelector('mat-radio-button, [role="radio"]');
                if (rb) {
                    const hit = clickSlotAt(rb);
                    if (hit) {
                        hit.label = (card.innerText || hit.label || '').replace(/\\s+/g, ' ').trim().slice(0, 90);
                        return hit;
                    }
                }
                const r = card.getBoundingClientRect();
                const rx = r.right - Math.min(28, Math.max(14, r.width * 0.1));
                const ry = r.top + r.height / 2;
                const opts = { bubbles: true, cancelable: true, view: window, clientX: rx, clientY: ry };
                card.dispatchEvent(new PointerEvent('pointerdown', opts));
                card.dispatchEvent(new MouseEvent('mousedown', opts));
                card.dispatchEvent(new PointerEvent('pointerup', opts));
                card.dispatchEvent(new MouseEvent('mouseup', opts));
                card.dispatchEvent(new MouseEvent('click', opts));
                return {
                    x: rx,
                    y: ry,
                    label: (card.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 90),
                };
            }
            const root = slotRoot();
            for (const rb of root.querySelectorAll('mat-radio-button, [role="radio"]')) {
                if (!rb.offsetParent) continue;
                const hit = clickSlotAt(rb);
                if (hit) return hit;
            }
            return null;
        };
    """


def _calendar_js() -> str:
    """DOM helpers for TTD multi-month darshan calendar."""
    return """
        const MONTHS = ['January','February','March','April','May','June','July','August',
            'September','October','November','December'];

        const parseRgb = (bg) => {
            const m = (bg || '').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
            return m ? [+m[1], +m[2], +m[3]] : null;
        };

        const isGreenAvailable = (el) => {
            let node = el;
            for (let d = 0; d < 6 && node; d++) {
                const rgb = parseRgb(getComputedStyle(node).backgroundColor);
                const cls = (node.className || '').toString().toLowerCase();
                if (/available|selectable|slot.?open|green/i.test(cls)) return true;
                if (rgb) {
                    const [r, g, b] = rgb;
                    if (g >= 90 && g > r + 25 && g > b + 15) return true;
                    if (b > 140 && b > g + 20) return false;
                    if (Math.abs(r - g) < 18 && Math.abs(g - b) < 18 && r < 190) return false;
                    if (r > 180 && g < 120) return false;
                }
                node = node.parentElement;
            }
            return false;
        };

        const isSelectedDay = (el) => {
            let node = el;
            for (let d = 0; d < 5 && node; d++) {
                const cls = (node.className || '').toString().toLowerCase();
                if (/selected|active|highlight|current|picked/i.test(cls)) return true;
                const rgb = parseRgb(getComputedStyle(node).backgroundColor);
                if (rgb) {
                    const [r, g, b] = rgb;
                    if (r >= 55 && b >= 90 && r > g + 10) return true;
                }
                node = node.parentElement;
            }
            return false;
        };

        const findMonthBlock = (monthName, year) => {
            const yearStr = String(year);
            const blocks = [];
            for (const el of document.querySelectorAll('div, section, table, mat-calendar')) {
                const raw = (el.innerText || '').trim();
                if (!raw || raw.length > 900) continue;
                const norm = raw.replace(/\\s+/g, ' ');
                if (!norm.toLowerCase().includes(monthName.toLowerCase())) continue;
                if (!norm.includes(yearStr)) continue;
                if (!/\\bS\\b.*\\bM\\b.*\\bT\\b.*\\bW\\b/i.test(raw) && !/sun|mon|tue|wed/i.test(raw)) continue;
                blocks.push(el);
            }
            if (blocks.length) {
                blocks.sort((a, b) => a.innerText.length - b.innerText.length);
                return blocks[0];
            }
            for (const h of document.querySelectorAll('div, span, h2, h3, h4, h5, h6')) {
                const t = (h.innerText || '').trim();
                if (new RegExp('^' + monthName + '\\\\s*' + yearStr + '$', 'i').test(t)) {
                    return h.closest('div, section, table') || h.parentElement;
                }
            }
            return null;
        };

        const dayClickTargets = (scope, dayStr) => {
            const out = [];
            for (const el of scope.querySelectorAll('td, button, div, span, a, [role="gridcell"], [role="button"]')) {
                const t = (el.innerText || '').trim();
                if (t !== dayStr) continue;
                if (!el.offsetParent) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 12 || r.height < 12 || r.width > 90) continue;
                out.push(el);
            }
            out.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
            return out;
        };

        const clickTargetAt = (el) => {
            el.scrollIntoView({ block: 'center', inline: 'center' });
            const r = el.getBoundingClientRect();
            const x = r.x + r.width / 2;
            const y = r.y + r.height / 2;
            const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
            if (typeof el.click === 'function') el.click();
            return { x, y, w: r.width, h: r.height };
        };
    """


def _spat_calendar_js() -> str:
    """SPAT / mat-calendar day-cell helpers (Angular Material, not table calendar)."""
    return r"""
        const spatDayTargets = (dayStr) => {
            const disabled = /disabled|unavailable|full|booked|quota.*full|past|grey/i;
            const available = /available|enable|active|open|slot|green/i;
            const out = [];

            for (const btn of document.querySelectorAll(
                'button.mat-calendar-body-cell, [role="gridcell"] button, ' +
                '.mat-calendar-body-cell-content, td.mat-calendar-body-cell, ' +
                'button.mat-calendar-body-cell-content'
            )) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (t !== dayStr) continue;
                if (!btn.offsetParent) continue;
                if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') continue;
                const cls = (btn.className || '').toString();
                if (disabled.test(cls)) continue;
                out.push({ el: btn, score: available.test(cls) ? 20 : 10 });
            }

            for (const el of document.querySelectorAll(
                'td, div.day, span.day, .calendar-day, .cal-day, ' +
                '[data-date], [class*="day"], [class*="date"]'
            )) {
                const t = (el.innerText || el.textContent || '').trim();
                if (t !== dayStr) continue;
                if (!el.offsetParent) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 12 || r.width > 80 || r.height < 12) continue;
                const cls = (el.className || '').toString();
                if (disabled.test(cls)) continue;
                if (el.getAttribute('aria-disabled') === 'true') continue;
                let blocked = false;
                let node = el.parentElement;
                for (let d = 0; d < 4 && node; d++, node = node.parentElement) {
                    if (disabled.test((node.className || '').toString())) {
                        blocked = true; break;
                    }
                }
                if (blocked) continue;
                out.push({ el, score: available.test(cls) ? 15 : 5 });
            }

            for (const el of document.querySelectorAll(
                'a, button, [role="button"], [role="gridcell"], [tabindex]'
            )) {
                const t = (el.innerText || el.textContent || '').trim();
                if (t !== dayStr) continue;
                if (!el.offsetParent) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 10 || r.width > 90) continue;
                const cls = (el.className || '').toString();
                if (disabled.test(cls)) continue;
                out.push({ el, score: 3 });
            }

            out.sort((a, b) => {
                if (b.score !== a.score) return b.score - a.score;
                const ra = a.el.getBoundingClientRect();
                const rb = b.el.getBoundingClientRect();
                if (Math.abs(ra.top - rb.top) > 10) return ra.top - rb.top;
                return ra.left - rb.left;
            });

            const seen = new Set();
            return out.filter(({ el }) => {
                if (seen.has(el)) return false;
                seen.add(el);
                return true;
            }).map(({ el }) => el);
        };

        const spatClickTarget = (el) => {
            el.scrollIntoView({ block: 'center', inline: 'nearest' });
            const r = el.getBoundingClientRect();
            const x = r.x + r.width / 2;
            const y = r.y + r.height / 2;
            const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
            ['pointerdown','mousedown','pointerup','mouseup','click'].forEach((ev) => {
                el.dispatchEvent(new (ev.startsWith('pointer') ? PointerEvent : MouseEvent)(ev, opts));
            });
            if (typeof el.click === 'function') el.click();
            return { x, y };
        };

        const spatMonthVisible = (monthName, year) => {
            const needle = `${monthName} ${year}`.toLowerCase();
            const body = (document.body.innerText || '').toLowerCase();
            return body.includes(needle) ||
                body.includes(monthName.slice(0, 3).toLowerCase() + ' ' + year);
        };

        const navigateToMonth = (monthName, year, direction) => {
            const arrows = [...document.querySelectorAll(
                'button.mat-calendar-previous-button, button.mat-calendar-next-button, ' +
                '[aria-label*="Previous"], [aria-label*="Next"], ' +
                'button[class*="prev"], button[class*="next"]'
            )].filter((b) => b.offsetParent !== null);
            const btn = direction === 'next'
                ? arrows.find((b) => /next|forward|right/i.test(b.getAttribute('aria-label') || b.className))
                    || arrows[arrows.length - 1]
                : arrows.find((b) => /prev|back|left/i.test(b.getAttribute('aria-label') || b.className))
                    || arrows[0];
            if (btn) { btn.click(); return true; }
            return false;
        };
    """


async def _wait_until(page, checker, timeout: float = 12.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await checker(page):
            return True
        await asyncio.sleep(0.4)
    return await checker(page)


async def _ttd_date_selection_registered(page, day: int, month: int, year: int) -> bool:
    """True when slot list appeared or the day cell shows selected styling."""
    if await _ttd_slot_screen_visible(page):
        return True
    month_name = MONTH_NAMES[month - 1]
    month_short = month_name[:3]
    try:
        return bool(
            await page.evaluate(
                f"""
                (args) => {{
                    {_calendar_js()}
                    const {{ day, monthName, monthShort, year }} = args;
                    const body = document.body.innerText || '';
                    if (/select any\\s*1\\s*slot/i.test(body)) return true;
                    if (new RegExp(`dated\\\\s+${{day}}\\\\s+${{monthShort}}`, 'i').test(body)) return true;
                    if (new RegExp(`${{day}}\\\\s+${{monthShort}}`, 'i').test(body) && /slot time/i.test(body))
                        return true;
                    const block = findMonthBlock(monthName, year);
                    if (!block) return false;
                    for (const el of dayClickTargets(block, String(day))) {{
                        if (isSelectedDay(el)) return true;
                    }}
                    for (const el of document.querySelectorAll(
                        '[class*="selected"], [class*="active"], [aria-selected="true"], ' +
                        '.mat-calendar-body-selected, .mat-calendar-body-cell-content'
                    )) {{
                        if ((el.innerText || '').trim() === String(day)) return true;
                    }}
                    return false;
                }}
                """,
                {
                    "day": day,
                    "monthName": month_name,
                    "monthShort": month_short,
                    "year": year,
                },
            )
        )
    except Exception:
        return False


async def select_available_date(page, run: "TravelRun", target_date: str) -> bool:
    """Pick target date — SPAT mat-calendar first, legacy month-table fallback."""
    parsed = _parse_date(target_date)
    if not parsed:
        run._log(f"⚠ Invalid target date: {target_date}")
        return False

    day, month, year = parsed
    month_name = MONTH_NAMES[month - 1]
    run._log(f"→ SPAT date select: day **{day}** in **{month_name} {year}**")

    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    await asyncio.sleep(0.5)

    for _nav in range(6):
        visible = await page.evaluate(
            f"""
            () => {{
                {_spat_calendar_js()}
                return spatMonthVisible({month_name!r}, {year});
            }}
            """
        )
        if visible:
            break
        navigated = await page.evaluate(
            f"""
            () => {{
                {_spat_calendar_js()}
                return navigateToMonth({month_name!r}, {year}, 'next');
            }}
            """
        )
        if not navigated:
            break
        await asyncio.sleep(0.7)

    for attempt in range(5):
        result = await page.evaluate(
            f"""
            (day) => {{
                {_spat_calendar_js()}
                const dayStr = String(day);
                const targets = spatDayTargets(dayStr);
                if (!targets.length) return {{ error: 'no-cells', count: 0, found: 0 }};
                const pos = spatClickTarget(targets[0]);
                return {{ ok: true, x: pos.x, y: pos.y,
                          label: (targets[0].innerText || '').trim() }};
            }}
            """,
            day,
        )

        if result and result.get("ok"):
            try:
                await page.mouse.click(float(result["x"]), float(result["y"]))
            except Exception:
                pass
            await asyncio.sleep(1.2)
            if await _ttd_date_selection_registered(page, day, month, year):
                run._log(f"→ Date {target_date} selected (SPAT calendar)")
                return True
            run._log(f"→ SPAT date click retry {attempt + 2}/5…")
        else:
            count = (result or {}).get("count", 0)
            run._log(f"→ No SPAT cell for day {day} (attempt {attempt + 1}/5, found={count})")
            try:
                await page.evaluate("window.scrollBy(0, 300)")
            except Exception:
                pass
            await asyncio.sleep(0.7)

    try:
        cells = page.locator(
            f"button.mat-calendar-body-cell-content:text-is('{day}'), "
            f"td:text-is('{day}'), "
            f"[role='gridcell']:text-is('{day}')"
        )
        for i in range(await cells.count()):
            cell = cells.nth(i)
            if not await cell.is_visible():
                continue
            cls = await cell.get_attribute("class") or ""
            if re.search(r"disabled|unavailable|full|past", cls, re.I):
                continue
            await cell.scroll_into_view_if_needed()
            await cell.click()
            await asyncio.sleep(1.2)
            if await _ttd_date_selection_registered(page, day, month, year):
                run._log(f"→ Date {target_date} selected (Playwright mat-calendar)")
                return True
    except Exception as exc:
        run._log(f"⚠ Playwright date fallback: {exc}")

    run._log(f"→ Legacy calendar fallback for {target_date}")
    for attempt in range(4):
        click_info = await page.evaluate(
            f"""
            (args) => {{
                {_calendar_js()}
                const {{ day, monthName, year }} = args;
                const dayStr = String(day);
                const block = findMonthBlock(monthName, year);
                if (!block) return {{ error: 'no-month-block' }};
                block.scrollIntoView({{ block: 'center', inline: 'nearest' }});
                const targets = dayClickTargets(block, dayStr);
                for (const el of targets) {{
                    if (!isGreenAvailable(el)) continue;
                    const pos = clickTargetAt(el);
                    return {{ method: 'green', x: pos.x, y: pos.y }};
                }}
                if (targets.length) {{
                    const pos = clickTargetAt(targets[0]);
                    return {{ method: 'day-fallback', x: pos.x, y: pos.y }};
                }}
                for (const el of block.querySelectorAll('td, button, div, span, a, [role="gridcell"]')) {{
                    const t = (el.innerText || '').trim();
                    if (t !== dayStr) continue;
                    if (!el.offsetParent) continue;
                    const pos = clickTargetAt(el);
                    return {{ method: 'block-scan', x: pos.x, y: pos.y }};
                }}
                return {{ error: 'no-green-cell', found: targets.length }};
            }}
            """,
            {"day": day, "monthName": month_name, "year": year},
        )
        if click_info and not click_info.get("error"):
            try:
                await page.mouse.click(float(click_info["x"]), float(click_info["y"]))
            except Exception:
                pass
            await asyncio.sleep(1.2)
            if await _ttd_date_selection_registered(page, day, month, year):
                run._log(
                    f"→ Date selected: {target_date} ({click_info.get('method', 'legacy')})"
                )
                return True
        await asyncio.sleep(0.7)

    run._log(
        f"⚠ Could not auto-select {target_date} — click day **{day}** in **{month_name} {year}** manually"
    )
    return False


async def set_ticket_count(page, run: "TravelRun", count: int) -> bool:
    count = max(1, min(8, int(count or 1)))
    try:
        set_ok = await page.evaluate(
            """
            (count) => {
                const selects = [...document.querySelectorAll('select')].filter((s) => s.offsetParent !== null);
                for (const sel of selects) {
                    const ctx = (sel.closest('div, section')?.innerText || '').toLowerCase();
                    if (!/ticket|pilgrim|person/i.test(ctx) && selects.length > 1) continue;
                    const val = String(count);
                    for (const opt of sel.options) {
                        if (opt.value === val || (opt.textContent || '').trim() === val) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                    if (sel.options.length >= count) {
                        sel.selectedIndex = count - 1;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }
            """,
            count,
        )
        if set_ok:
            run._log(f"→ Number of tickets set to {count}")
            await asyncio.sleep(0.4)
            return True
    except Exception:
        pass
    return False


async def _ttd_slot_selected(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                f"""
                () => {{
                    {_slot_page_js()}
                    return slotSelected();
                }}
                """
            )
        )
    except Exception:
        return False


async def _ttd_slot_selection_ok(page) -> bool:
    """Slot picked if radio checked, Continue enabled, or Darshan Details shows a time."""
    if await _ttd_slot_selected(page):
        return True
    if await _ttd_pilgrim_screen_visible(page):
        return True
    if await _continue_button_state(page) == "ready":
        return True
    return False


async def _continue_button_state(page) -> str:
    try:
        return str(
            await page.evaluate(
                f"""
                () => {{
                    {_slot_page_js()}
                    return continueReady();
                }}
                """
            )
        )
    except Exception:
        return "missing"


async def select_time_slot(page, run: "TravelRun", *, ticket_count: int = 1) -> bool:
    """Select first available darshan time slot on the date/slot page."""
    if not await _ttd_slot_screen_visible(page):
        return False

    await set_ticket_count(page, run, ticket_count)

    for attempt in range(4):
        selected = await page.evaluate(
            f"""
            () => {{
                {_slot_page_js()}
                return clickFirstSlot();
            }}
            """
        )
        if selected:
            label = ""
            if isinstance(selected, dict):
                label = str(selected.get("label") or "")
                try:
                    await page.mouse.click(float(selected["x"]), float(selected["y"]))
                except Exception:
                    pass
            else:
                label = str(selected)
            run._log(f"→ Time slot clicked: {label.strip()[:60]}")
            await asyncio.sleep(1.0)
            if await _ttd_slot_selection_ok(page):
                run._log("→ Time slot confirmed in Chrome")
                return True
            run._log("→ Slot click retry — waiting for selection…")
        try:
            cards = page.locator("div, section").filter(
                has_text=re.compile(r"\d+\s*Available.*Slot Time", re.I)
            )
            if await cards.count():
                card = cards.first
                box = await card.bounding_box()
                if box:
                    await page.mouse.click(box["x"] + box["width"] - 20, box["y"] + box["height"] / 2)
                    await asyncio.sleep(1.0)
                    if await _ttd_slot_selection_ok(page):
                        run._log("→ Time slot selected (card radio area)")
                        return True
            radio = page.locator("mat-radio-button:visible, [role='radio']:visible").first
            if await radio.count():
                await radio.scroll_into_view_if_needed()
                box = await radio.bounding_box()
                if box:
                    await page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                else:
                    await radio.click(force=True)
                await asyncio.sleep(0.8)
                if await _ttd_slot_selection_ok(page):
                    run._log("→ Time slot selected (Playwright radio)")
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.5)

    if await _ttd_slot_selection_ok(page):
        run._log("→ Time slot already selected in Chrome")
        return True

    run._log("⚠ Could not select a time slot — pick one in Chrome")
    return False


async def click_slot_continue(page, run: "TravelRun", *, ticket_count: int = 1) -> bool:
    """Advance after slot selection.

    On Sri PAT the pilgrim form is on the **same page**. Clicking **Continue** here
    validates empty Gender / Photo ID fields and shows *validation failures*.
    We only scroll to pilgrim — real Continue runs after ``fill_pilgrims``.
    """
    import time

    if not await _ttd_slot_selection_ok(page):
        run._log("⚠ Select a time slot first")
        await select_time_slot(page, run, ticket_count=ticket_count)
        if not await _ttd_slot_selection_ok(page):
            return False

    await clear_pilgrim_page_alerts(page, run)
    await _scroll_pilgrim_form_into_view(page, 0)

    if await _ttd_pilgrim_screen_visible(page):
        run._log(
            "→ Slot selected — **Pilgrim Details** below. "
            "Agent will click **Continue** only after pilgrim fields are filled."
        )
        return True

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        state = await _continue_button_state(page)
        if state == "ready":
            break
        if state == "disabled":
            run._log("→ Continue disabled — re-selecting slot…")
            await select_time_slot(page, run, ticket_count=ticket_count)
        await asyncio.sleep(0.45)
    else:
        run._log("⚠ Continue button not enabled — pick a slot in Chrome")
        return False

    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.4)
    except Exception:
        pass

    clicked = False
    for attempt in range(5):
        click_info = await page.evaluate(
            f"""
            () => {{
                {_slot_page_js()}
                return clickContinueButton();
            }}
            """
        )
        if click_info:
            try:
                await page.mouse.click(float(click_info["x"]), float(click_info["y"]))
            except Exception:
                pass
            clicked = True
            run._log("→ Clicked **Continue** on date/slot page")
            await asyncio.sleep(1.5)
            await clear_pilgrim_page_alerts(page, run)
            await _scroll_pilgrim_form_into_view(page, 0)
            if await _ttd_pilgrim_screen_visible(page):
                run._log("→ **Pilgrim Details** section open")
                return True
            if await _ttd_slot_selection_ok(page):
                run._log("→ Continue clicked — slot registered")
                return True
            run._log("→ Continue retry…")
        try:
            btn = page.get_by_role("button", name=re.compile(r"^Continue$", re.I)).last
            if await btn.count() and await btn.is_visible():
                await btn.scroll_into_view_if_needed()
                box = await btn.bounding_box()
                if box and not await btn.is_disabled():
                    await page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    clicked = True
                    run._log("→ Clicked **Continue** (Playwright)")
                    await asyncio.sleep(1.5)
                    await clear_pilgrim_page_alerts(page, run)
                    await _scroll_pilgrim_form_into_view(page, 0)
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.5)

    if clicked:
        await clear_pilgrim_page_alerts(page, run)
        return True

    run._log("⚠ Could not click **Continue** — click it at the bottom in Chrome")
    return False


async def complete_date_and_slot_booking(
    page,
    run: "TravelRun",
    target_date: str,
    ticket_count: int = 1,
) -> bool:
    """Calendar date → slot list → pick time → **Continue** → pilgrim section."""
    if target_date:
        if not await select_available_date(page, run, target_date):
            return False

    if not await _wait_until(page, _ttd_slot_screen_visible, timeout=15.0):
        run._log("⚠ Slot list did not appear after date click")
        return False

    run._log("→ Slot selection — picking a time")
    if not await select_time_slot(page, run, ticket_count=ticket_count):
        if not await _ttd_slot_selection_ok(page):
            return False
        run._log("→ Slot appears selected — continuing")

    await asyncio.sleep(0.5)
    run._log("→ Opening **Pilgrim Details** (Continue waits until pilgrim fill)")
    if not await click_slot_continue(page, run, ticket_count=ticket_count):
        await select_time_slot(page, run, ticket_count=ticket_count)
        await click_slot_continue(page, run, ticket_count=ticket_count)

    await clear_pilgrim_page_alerts(page, run)
    await _scroll_pilgrim_form_into_view(page, 0)

    if await _ttd_pilgrim_screen_visible(page):
        run._log("→ Date + slot done — fill **Pilgrim Details** in Streamlit next")
        return True

    for _ in range(3):
        await _scroll_pilgrim_form_into_view(page, 0)
        await asyncio.sleep(0.5)
        if await _ttd_pilgrim_screen_visible(page):
            return True

    return await _ttd_slot_selection_ok(page)


async def click_continue(page, run: "TravelRun") -> bool:
    """Generic Continue — prefer slot-page handler when on slot screen."""
    if await _ttd_slot_screen_visible(page):
        return await click_slot_continue(page, run)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.3)
    except Exception:
        pass
    if await click_first(
        page,
        (
            'button:has-text("Continue")',
            'a:has-text("Continue")',
            'input[value="Continue"]',
            '[type="submit"]:has-text("Continue")',
        ),
    ):
        run._log("→ Clicked Continue")
        await asyncio.sleep(1.0)
        return True
    if await click_text(page, r"^continue$", timeout=4000):
        run._log("→ Clicked Continue")
        await asyncio.sleep(1.0)
        return True
    return False


async def _fill_angular_input(loc, value: str) -> bool:
    """IRCTC-style: click field, clear, type slowly so Angular registers input."""
    try:
        await loc.scroll_into_view_if_needed()
        await loc.click(click_count=3)
        await loc.press("Backspace")
        await loc.press_sequentially(value, delay=45)
        await asyncio.sleep(0.2)
        try:
            typed = (await loc.input_value()).strip()
            if typed == value.strip():
                return True
            if value.strip().lower() in typed.lower():
                return True
        except Exception:
            return True
        return True
    except Exception:
        return False


async def _fill_angular_input_if_needed(loc, value: str) -> bool:
    """Type only when the field is empty or does not already match."""
    try:
        current = (await loc.input_value()).strip()
        target = value.strip()
        if current == target:
            return True
        if target and current.lower() == target.lower():
            return True
        if target and len(current) > 2 and target.lower() in current.lower():
            return True
    except Exception:
        pass
    return await _fill_angular_input(loc, value)


async def _mat_overlay_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    for (const o of document.querySelectorAll(
                        '.cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, ' +
                        '.cdk-overlay-pane mat-option, [role="listbox"] [role="option"]'
                    )) {
                        const r = o.getBoundingClientRect();
                        if (r.width > 8 && r.height > 8 && o.offsetParent !== null) return true;
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _pilgrim_details_scope(page):
    try:
        on_pilgrim_url = bool(
            await page.evaluate(
                "() => /pilgrim[-_]detail/i.test((location.pathname || '').toLowerCase())"
            )
        )
        if on_pilgrim_url:
            for sel in (
                "app-pilgrim-details",
                "app-pilgrim-detail",
                "main",
                "form",
                "section",
            ):
                loc = page.locator(sel)
                if await loc.count():
                    return loc.first
    except Exception:
        pass
    section = page.locator("div, section, form, main").filter(
        has_text=re.compile(r"Pilgrim\s+Details|Devotee\s+Detail|Ticket\s+Holder", re.I)
    )
    if await section.count():
        return section.first
    return page.locator("body")


async def _find_pilgrim_input(
    page,
    label_pattern: str,
    index: int = 0,
    *,
    formcontrol_hint: str = "",
):
    """Locate pilgrim text input by label / mat-form-field / formControlName (IRCTC-style)."""
    label_re = re.compile(label_pattern, re.I)
    scope = await _pilgrim_details_scope(page)

    try:
        loc = scope.get_by_label(label_re)
        if await loc.count() > index:
            candidate = loc.nth(index)
            if await candidate.is_visible():
                return candidate
    except Exception:
        pass

    try:
        loc = page.get_by_label(label_re)
        if await loc.count() > index:
            candidate = loc.nth(index)
            if await candidate.is_visible():
                return candidate
    except Exception:
        pass

    for root in (scope, page.locator("body")):
        try:
            field = root.locator("mat-form-field, .mat-mdc-form-field").filter(
                has=root.locator("mat-label, label, .mat-mdc-floating-label").filter(
                    has_text=label_re
                )
            )
            if await field.count() > index:
                inp = field.nth(index).locator("input:visible").first
                if await inp.count() and await inp.is_visible():
                    return inp
        except Exception:
            pass

    if formcontrol_hint:
        try:
            inp = page.locator(
                f"input[formcontrolname*='{formcontrol_hint}' i]:visible, "
                f"input[ng-reflect-name*='{formcontrol_hint}' i]:visible"
            )
            if await inp.count() > index:
                candidate = inp.nth(index)
                if await candidate.is_visible():
                    return candidate
        except Exception:
            pass

    try:
        inputs = scope.locator("input:visible:not([type=hidden])")
        n_inputs = await inputs.count()
        n_selects = await scope.locator("mat-select:visible, select:visible").count()
        if n_selects == 0 and n_inputs >= (index + 1) * 5:
            # 5 inputs per row (gender / id-proof are input-based dropdowns):
            # name, age, gender, id-proof, id-number
            offset5 = {"name": 0, "age": 1, "id": 4}.get(formcontrol_hint, -1)
            if offset5 >= 0:
                return inputs.nth(index * 5 + offset5)
        offset = {"name": 0, "age": 1, "id": 2}.get(formcontrol_hint, -1)
        if offset >= 0 and n_inputs > index * 3 + offset:
            return inputs.nth(index * 3 + offset)
    except Exception:
        pass

    return None


async def _find_pilgrim_select(page, label_pattern: str, index: int = 0):
    """Locate Gender / Photo ID Proof mat-select."""
    label_re = re.compile(label_pattern, re.I)
    scope = await _pilgrim_details_scope(page)

    for root in (scope, page.locator("body")):
        try:
            field = root.locator("mat-form-field, .mat-mdc-form-field").filter(
                has=root.locator("mat-label, label, .mat-mdc-floating-label").filter(
                    has_text=label_re
                )
            )
            if await field.count() > index:
                combo = field.nth(index).locator(
                    "mat-select, .mat-mdc-select-trigger, .mat-mdc-select, select"
                ).first
                if await combo.count():
                    return combo
        except Exception:
            pass

    try:
        selects = scope.locator("mat-select:visible, .mat-mdc-select:visible")
        offset = 0 if "gender" in label_pattern.lower() else 1
        if await selects.count() > index * 2 + offset:
            return selects.nth(index * 2 + offset)
    except Exception:
        pass

    return None


async def _select_dropdown_irctc(page, combo, variants: tuple[str, ...]) -> bool:
    """Open mat-select and click option — same pattern as IRCTC autocomplete pick."""
    if combo is None:
        return False
    try:
        await combo.scroll_into_view_if_needed()
        try:
            tag = str(await combo.evaluate("el => el.tagName.toLowerCase()"))
        except Exception:
            tag = ""
        if tag == "select":
            for variant in variants:
                try:
                    await combo.select_option(label=variant)
                    return True
                except Exception:
                    continue
            return False
        for _ in range(4):
            await combo.click(force=True)
            await asyncio.sleep(0.75)
            if await _mat_overlay_visible(page) and await _pick_mat_option(page, variants):
                await asyncio.sleep(0.35)
                return True
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
    except Exception:
        pass
    return False


async def _find_pilgrim_select_by_formcontrol(page, hint: str, index: int = 0):
    """Locate mat-select by Angular formControlName (TTD SPAT pilgrim-details)."""
    try:
        scope = await _pilgrim_details_scope(page)
        for root in (scope, page.locator("body")):
            loc = root.locator(
                f'mat-select[formcontrolname*="{hint}" i]:visible, '
                f'select[formcontrolname*="{hint}" i]:visible'
            )
            if await loc.count() > index:
                return loc.nth(index)
    except Exception:
        pass
    return None


async def _open_pilgrim_select_overlay(page, pilgrim_index: int, field_key: str) -> bool:
    """Open Gender / Photo ID Proof mat-select and wait for CDK overlay."""
    try:
        opened = await page.evaluate(
            f"""
            (args) => {{
                {_pilgrim_page_js()}
                const {{ idx, key }} = args;
                let mf = null;
                const row = pilgrimRowByLabels(idx);
                if (row && row[key]) mf = row[key];
                if (!mf) {{
                    const root = pilgrimRoot();
                    const fields = [...root.querySelectorAll('mat-form-field, .mat-mdc-form-field')]
                        .filter((f) => f.getBoundingClientRect().width > 20)
                        .sort((a, b) => {{
                            const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                            const dy = ra.top - rb.top;
                            return Math.abs(dy) > 12 ? dy : ra.left - rb.left;
                        }});
                    const offsets = {{ gender: 2, idproof: 3 }};
                    const off = offsets[key];
                    if (off !== undefined && fields[idx * 5 + off]) mf = fields[idx * 5 + off];
                    if (!mf) {{
                        const selectFields = fields.filter((f) =>
                            f.querySelector('mat-select, .mat-mdc-select, select, [role="combobox"]')
                        );
                        const selOff = key === 'gender' ? 0 : 1;
                        mf = selectFields[idx * 2 + selOff] || null;
                    }}
                }}
                if (!mf) return {{ ok: false }};
                const trigger = selectTriggerEl(mf);
                if (!trigger) return {{ ok: false }};
                trigger.scrollIntoView({{ block: 'center', inline: 'nearest' }});
                const r = trigger.getBoundingClientRect();
                const x = r.x + r.width / 2;
                const y = r.y + r.height / 2;
                const ev = {{ bubbles: true, cancelable: true, clientX: x, clientY: y }};
                trigger.dispatchEvent(new PointerEvent('pointerdown', ev));
                trigger.dispatchEvent(new MouseEvent('mousedown', ev));
                trigger.dispatchEvent(new PointerEvent('pointerup', ev));
                trigger.dispatchEvent(new MouseEvent('mouseup', ev));
                trigger.click();
                trigger.dispatchEvent(new KeyboardEvent('keydown', {{
                    key: 'ArrowDown', code: 'ArrowDown', bubbles: true,
                }}));
                return {{ ok: true, x, y }};
            }}
            """,
            {"idx": pilgrim_index, "key": field_key},
        )
        if opened and opened.get("ok"):
            await asyncio.sleep(0.5)
            if await _mat_overlay_visible(page):
                # JS-dispatched click already opened the panel — a real mouse
                # click now would land on the overlay backdrop and close it.
                return True
            await page.mouse.click(float(opened["x"]), float(opened["y"]))
            await asyncio.sleep(0.9)
            return await _mat_overlay_visible(page)
    except Exception:
        pass
    return False


async def _select_generic_dropdown(
    page,
    pilgrim_index: int,
    field_key: str,
    variants: tuple[str, ...],
    *,
    run: "TravelRun | None" = None,
) -> bool:
    """
    Handle non-Material widgets: radio groups, ng-select, PrimeNG, bootstrap
    and custom div/[role=combobox] dropdowns (pages with zero mat-select/select).
    """
    label_pat = "gender" if field_key == "gender" else "photo\\s*id|id\\s*proof|id\\s*type"

    # 1) Radio-button style pickers (Gender is often radios on gov forms)
    try:
        picked = await page.evaluate(
            f"""
            (args) => {{
                {_pilgrim_page_js()}
                const {{ idx, labelPat, variants }} = args;
                const root = pilgrimRoot();
                const vis = (el) => el && el.offsetParent !== null;
                let radios = [...root.querySelectorAll(
                    'input[type=radio], mat-radio-button, .mat-mdc-radio-button'
                )].filter(vis);
                if (!radios.length) {{
                    radios = [...document.querySelectorAll(
                        'input[type=radio], mat-radio-button, .mat-mdc-radio-button'
                    )].filter(vis);
                }}
                if (!radios.length) return '';
                const groups = [];
                const byName = new Map();
                for (const r of radios) {{
                    const nm = r.getAttribute('name')
                        || r.querySelector?.('input')?.name || `g${{groups.length}}`;
                    if (!byName.has(nm)) {{
                        byName.set(nm, []);
                        groups.push(byName.get(nm));
                    }}
                    byName.get(nm).push(r);
                }}
                const re = new RegExp(labelPat, 'i');
                let group = groups.find((g) => re.test(
                    (g[0].closest('div, fieldset, td, li, tr')?.parentElement?.innerText || '')
                        .slice(0, 160)
                ));
                if (!group) group = groups[idx] || groups[0];
                for (const v of variants) {{
                    const needle = (v || '').toLowerCase();
                    for (const r of group) {{
                        const lab = (
                            r.closest('label')?.innerText
                            || (r.labels && r.labels[0]?.innerText)
                            || r.parentElement?.innerText
                            || r.value || r.getAttribute('aria-label') || ''
                        ).replace(/\\s+/g, ' ').trim().toLowerCase();
                        if (!lab) continue;
                        if (lab === needle || lab.startsWith(needle) || needle.startsWith(lab)) {{
                            const inp = r.matches('input') ? r : (r.querySelector('input') || r);
                            inp.click();
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return lab;
                        }}
                    }}
                }}
                return '';
            }}
            """,
            {"idx": pilgrim_index, "labelPat": label_pat, "variants": list(variants)},
        )
        if picked:
            pl = str(picked).lower()
            if any(v.lower() in pl or pl in v.lower() for v in variants if v):
                if run:
                    run._log(f"→ {field_key}: picked radio '{picked}'")
                return True
    except Exception:
        pass

    # 2) Custom combobox / framework dropdowns — click trigger, click option
    find_trigger_js = f"""
        (args) => {{
            {_pilgrim_page_js()}
            const {{ idx, key, labelPat }} = args;
            const root = pilgrimRoot();
            const vis = (el) => {{
                if (!el || el.offsetParent === null) return false;
                const r = el.getBoundingClientRect();
                return r.width > 10 && r.height > 8;
            }};
            const candSel = 'select, mat-select, .mat-mdc-select, .ng-select, .p-dropdown, ' +
                '[role="combobox"], [aria-haspopup="listbox"], .dropdown-toggle, ' +
                '.select2-selection, .choices__inner';
            let raw = [...root.querySelectorAll(candSel)];
            let cands = raw.filter(
                (el) => vis(el) && !raw.some((o) => o !== el && o.contains(el))
            );
            let fromDoc = false;
            if (!cands.length) {{
                raw = [...document.querySelectorAll(candSel)];
                cands = raw.filter(
                    (el) => vis(el) && !raw.some((o) => o !== el && o.contains(el))
                );
                fromDoc = true;
            }}
            if (!cands.length) return null;
            const re = new RegExp(labelPat, 'i');
            const labelled = cands.filter((el) => {{
                const cont = el.closest(
                    'mat-form-field, .mat-mdc-form-field, .form-group, .form-field, div, td, li'
                );
                return cont && re.test((cont.innerText || '').slice(0, 160));
            }});
            let el = labelled[idx] || labelled[0] || null;
            if (!el && !fromDoc) {{
                const off = key === 'gender' ? 0 : 1;
                el = cands[idx * 2 + off] || null;
            }}
            if (!el) return null;
            el.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            const r = el.getBoundingClientRect();
            return {{
                x: r.x + r.width / 2,
                y: r.y + r.height / 2,
                display: (el.value || el.innerText || '')
                    .replace(/\\s+/g, ' ').trim().slice(0, 60),
            }};
        }}
    """
    pick_option_js = """
        (variants) => {
            const opts = [...document.querySelectorAll(
                '.cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, ' +
                '[role="option"], .ng-dropdown-panel .ng-option, ' +
                '.p-dropdown-items .p-dropdown-item, .dropdown-menu .dropdown-item, ' +
                '.dropdown-menu li, .select2-results__option, [role="listbox"] li'
            )].filter((o) => o.offsetParent !== null);
            for (const v of variants) {
                const needle = (v || '').toLowerCase();
                for (const el of opts) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (!t || t === 'select' || t === 'choose') continue;
                    if (t === needle || t.startsWith(needle) || needle.startsWith(t)) {
                        el.scrollIntoView({ block: 'nearest' });
                        const r = el.getBoundingClientRect();
                        const ev = { bubbles: true, cancelable: true,
                                     clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
                        el.dispatchEvent(new PointerEvent('pointerdown', ev));
                        el.dispatchEvent(new MouseEvent('mousedown', ev));
                        el.dispatchEvent(new PointerEvent('pointerup', ev));
                        el.dispatchEvent(new MouseEvent('mouseup', ev));
                        el.click();
                        return t;
                    }
                }
            }
            return '';
        }
    """
    args = {"idx": pilgrim_index, "key": field_key, "labelPat": label_pat}

    for _ in range(3):
        try:
            info = await page.evaluate(find_trigger_js, args)
        except Exception:
            info = None
        if not info:
            break
        if _mat_option_selected(info.get("display") or "", variants):
            return True

        await page.mouse.click(float(info["x"]), float(info["y"]))
        await asyncio.sleep(0.7)
        try:
            picked = str(await page.evaluate(pick_option_js, list(variants)) or "")
        except Exception:
            picked = ""
        await asyncio.sleep(0.45)

        try:
            info2 = await page.evaluate(find_trigger_js, args)
        except Exception:
            info2 = None
        if info2 and _mat_option_selected(info2.get("display") or "", variants):
            if run:
                run._log(f"→ {field_key}: generic dropdown set to '{info2.get('display')}'")
            return True
        if picked:
            pl = picked.lower()
            if any(v.lower() in pl or pl in v.lower() for v in variants if v):
                if run:
                    run._log(f"→ {field_key}: generic option clicked '{picked}'")
                return True
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)

    # 3) Dropdowns built on bare text inputs (e.g. TTD '.floating-input' rows:
    #    name, age, gender, id-proof, id-number — gender/id-proof have no type attr)
    return await _select_input_dropdown(page, pilgrim_index, field_key, variants, run=run)


def _find_dropdown_input_js() -> str:
    """JS: locate the gender / id-proof input in an input-only pilgrim row."""
    return """
        const ddInput = (root, idx, key) => {
            const vis = (el) => el && el.offsetParent !== null;
            const inputs = [...root.querySelectorAll('input:not([type=hidden])')]
                .filter((i) => vis(i)
                    && !['radio', 'checkbox', 'submit', 'button'].includes(i.type || ''));
            if (!inputs.length) return null;
            const dd = inputs.filter((i) => i.readOnly || !i.getAttribute('type'));
            let el = null;
            if (dd.length >= 2) {
                el = dd[idx * 2 + (key === 'gender' ? 0 : 1)] || null;
            }
            if (!el && inputs.length >= (idx + 1) * 5) {
                el = inputs[idx * 5 + (key === 'gender' ? 2 : 3)] || null;
            }
            return el;
        };
    """


async def _select_input_dropdown(
    page,
    pilgrim_index: int,
    field_key: str,
    variants: tuple[str, ...],
    *,
    run: "TravelRun | None" = None,
) -> bool:
    """Click an input-based custom dropdown and pick the option that pops under it."""
    find_input_js = f"""
        (args) => {{
            {_pilgrim_page_js()}
            {_find_dropdown_input_js()}
            const {{ idx, key }} = args;
            const el = ddInput(pilgrimRoot(), idx, key);
            if (!el) return null;
            el.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            const r = el.getBoundingClientRect();
            return {{
                x: r.x + r.width / 2,
                y: r.y + r.height / 2,
                bottom: r.bottom,
                value: (el.value || '').trim(),
            }};
        }}
    """
    # Click whichever visible option-like element matching the variant sits
    # closest below the input (custom panels rarely use role="option").
    pick_near_js = """
        (args) => {
            const { variants, x, bottom } = args;
            const cands = [...document.querySelectorAll(
                'li, [role="option"], .dropdown-item, .option, .item, button, a, span, div, label'
            )].filter((el) => {
                if (el.offsetParent === null) return false;
                if (el.children.length > 1) return false;
                const r = el.getBoundingClientRect();
                if (r.height < 8 || r.height > 80 || r.width < 20) return false;
                if (r.top < bottom - 60 || r.top > bottom + 400) return false;
                return true;
            });
            for (const v of variants) {
                const needle = (v || '').toLowerCase();
                let best = null;
                let bestScore = 1e9;
                for (const el of cands) {
                    const t = (el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (!t || t === 'select' || t === 'choose') continue;
                    if (t === needle || t.startsWith(needle) || needle.startsWith(t)) {
                        const r = el.getBoundingClientRect();
                        const score = Math.abs(r.top - bottom)
                            + Math.abs((r.x + r.width / 2) - x) / 4;
                        if (score < bestScore) { bestScore = score; best = el; }
                    }
                }
                if (best) {
                    best.scrollIntoView({ block: 'nearest' });
                    const r = best.getBoundingClientRect();
                    const ev = { bubbles: true, cancelable: true,
                                 clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
                    best.dispatchEvent(new PointerEvent('pointerdown', ev));
                    best.dispatchEvent(new MouseEvent('mousedown', ev));
                    best.dispatchEvent(new PointerEvent('pointerup', ev));
                    best.dispatchEvent(new MouseEvent('mouseup', ev));
                    best.click();
                    return (best.innerText || best.textContent || '').trim();
                }
            }
            return '';
        }
    """
    args = {"idx": pilgrim_index, "key": field_key}

    async def _current_value() -> str:
        try:
            cur = await page.evaluate(find_input_js, args)
            return (cur or {}).get("value") or ""
        except Exception:
            return ""

    for attempt in range(3):
        try:
            info = await page.evaluate(find_input_js, args)
        except Exception:
            info = None
        if not info:
            return False
        if _mat_option_selected(info.get("value") or "", variants):
            return True

        await page.mouse.click(float(info["x"]), float(info["y"]))
        await asyncio.sleep(0.6)
        try:
            picked = str(
                await page.evaluate(
                    pick_near_js,
                    {"variants": list(variants), "x": info["x"], "bottom": info["bottom"]},
                )
                or ""
            )
        except Exception:
            picked = ""
        await asyncio.sleep(0.4)
        if _mat_option_selected(await _current_value(), variants):
            if run:
                run._log(f"→ {field_key}: input-dropdown set via option click")
            return True

        # Typing fallback: many custom dropdowns filter/accept typed text
        if attempt == 1:
            try:
                await page.mouse.click(float(info["x"]), float(info["y"]))
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+A")
                await page.keyboard.type(variants[0], delay=45)
                await asyncio.sleep(0.5)
                await page.evaluate(
                    pick_near_js,
                    {"variants": list(variants), "x": info["x"], "bottom": info["bottom"]},
                )
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.35)
                if _mat_option_selected(await _current_value(), variants):
                    if run:
                        run._log(f"→ {field_key}: input-dropdown set via typing")
                    return True
            except Exception:
                pass
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)

    # Last resort: set the input value directly with framework-friendly events
    try:
        ok = await page.evaluate(
            f"""
            (args) => {{
                {_pilgrim_page_js()}
                {_find_dropdown_input_js()}
                const {{ idx, key, value }} = args;
                const el = ddInput(pilgrimRoot(), idx, key);
                if (!el) return false;
                const ro = el.readOnly;
                if (ro) el.readOnly = false;
                const done = setInputValue(el, value);
                if (ro) el.readOnly = true;
                return done;
            }}
            """,
            {"idx": pilgrim_index, "key": field_key, "value": variants[0]},
        )
        if ok and _mat_option_selected(await _current_value(), variants):
            if run:
                run._log(f"→ {field_key}: input-dropdown value set directly")
            return True
    except Exception:
        pass
    return False


async def _select_pilgrim_dropdown_robust(
    page,
    pilgrim_index: int,
    field_key: str,
    variants: tuple[str, ...],
    *,
    run: "TravelRun | None" = None,
) -> bool:
    """
    Select Gender or Photo ID Proof — never type option text into a text input.
    field_key: 'gender' | 'idproof'
    """
    state_key = "gender" if field_key == "gender" else "id_proof"
    state = await _read_pilgrim_state_combined(page, pilgrim_index)
    if _mat_option_selected(state.get(state_key, ""), variants):
        return True

    fc_hints = ("gender",) if field_key == "gender" else ("proof", "idproof", "photoid", "idtype")
    label_pat = r"^gender" if field_key == "gender" else r"photo\s*id\s*proof|id\s*proof"

    # Native <select> path — no CDK overlay involved, set value directly.
    try:
        picked_native = await page.evaluate(
            f"""
            (args) => {{
                {_pilgrim_page_js()}
                const {{ idx, key, variants }} = args;
                let mf = null;
                const row = pilgrimRowByLabels(idx);
                if (row && row[key]) mf = row[key];
                let sel = mf ? mf.querySelector('select') : null;
                if (!sel) {{
                    const root = pilgrimRoot();
                    const sels = [...root.querySelectorAll('select')]
                        .filter((s) => s.offsetParent !== null);
                    if (sels.length) {{
                        const off = key === 'gender' ? 0 : 1;
                        sel = sels[idx * 2 + off] || null;
                    }}
                }}
                if (!sel) return '';
                for (const v of variants) {{
                    const needle = (v || '').toLowerCase();
                    for (const o of [...sel.options]) {{
                        const t = (o.textContent || '').trim().toLowerCase();
                        if (!t || t === 'select' || t === 'choose') continue;
                        if (t === needle || t.startsWith(needle) || needle.startsWith(t)) {{
                            sel.value = o.value;
                            sel.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            sel.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                            return t;
                        }}
                    }}
                }}
                return '';
            }}
            """,
            {"idx": pilgrim_index, "key": field_key, "variants": list(variants)},
        )
        if picked_native:
            await asyncio.sleep(0.3)
            picked_l = str(picked_native).lower()
            if any(v.lower() in picked_l or picked_l in v.lower() for v in variants if v):
                return True
    except Exception:
        pass

    # Non-Material widgets (radio groups, ng-select, custom comboboxes)
    if await _select_generic_dropdown(page, pilgrim_index, field_key, variants, run=run):
        return True

    async def _try_open_and_pick(combo) -> bool:
        if not combo:
            return False
        if await _select_dropdown_irctc(page, combo, variants):
            state2 = await _read_pilgrim_state_combined(page, pilgrim_index)
            return _mat_option_selected(state2.get(state_key, ""), variants)
        return False

    for _ in range(5):
        if run:
            await clear_pilgrim_page_alerts(page, run)
        if await _open_pilgrim_select_overlay(page, pilgrim_index, field_key):
            if await _pick_mat_option(page, variants):
                await asyncio.sleep(0.4)
                state2 = await _read_pilgrim_state_combined(page, pilgrim_index)
                if _mat_option_selected(state2.get(state_key, ""), variants):
                    return True
            if await _mat_overlay_visible(page):
                # mat-select typeahead: typing the option prefix highlights it.
                prefix = re.sub(r"[^a-zA-Z]", "", variants[0] if variants else "")[:4]
                if prefix:
                    await page.keyboard.type(prefix.lower(), delay=60)
                    await asyncio.sleep(0.25)
                else:
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.1)
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.4)
                state2 = await _read_pilgrim_state_combined(page, pilgrim_index)
                if _mat_option_selected(state2.get(state_key, ""), variants):
                    return True
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.25)

        for hint in fc_hints:
            combo = await _find_pilgrim_select_by_formcontrol(page, hint, pilgrim_index)
            if await _try_open_and_pick(combo):
                return True
        combo = await _find_pilgrim_select(page, label_pat, pilgrim_index)
        if await _try_open_and_pick(combo):
            return True

        if await _ttd_select_by_field_label(page, label_pat, variants, pilgrim_index):
            state2 = await _read_pilgrim_state_combined(page, pilgrim_index)
            if _mat_option_selected(state2.get(state_key, ""), variants):
                return True

        try:
            picked = await page.evaluate(
                f"""
                (args) => {{
                    {_pilgrim_page_js()}
                    const {{ idx, key, variants }} = args;
                    let row = pilgrimRowByLabels(idx);
                    let mf = row ? row[key] : null;
                    if (!mf) {{
                        const fields = pilgrimRow(idx);
                        const map = ['name', 'age', 'gender', 'idproof', 'idnum'];
                        const i = map.indexOf(key);
                        if (fields && i >= 0) mf = fields[i];
                    }}
                    if (!mf) return '';
                    const cur = selectDisplay(mf);
                    for (const v of variants) {{
                        if (cur.toLowerCase().includes((v || '').toLowerCase())) return cur;
                    }}
                    return openSelectAndPick(mf, variants);
                }}
                """,
                {"idx": pilgrim_index, "key": field_key, "variants": list(variants)},
            )
            if picked and not re.search(r"^select$|^choose$", str(picked).strip(), re.I):
                await asyncio.sleep(0.45)
                state2 = await _read_pilgrim_state_combined(page, pilgrim_index)
                if _mat_option_selected(state2.get(state_key, ""), variants):
                    return True
        except Exception:
            pass

        await page.keyboard.press("Escape")
        await asyncio.sleep(0.35)

    final_ok = _mat_option_selected(
        (await _read_pilgrim_state_combined(page, pilgrim_index)).get(state_key, ""),
        variants,
    )
    if not final_ok and run:
        try:
            diag = await page.evaluate(
                f"""
                () => {{
                    {_pilgrim_page_js()}
                    const vis = (el) => el && el.offsetParent !== null;
                    const root = pilgrimRoot();
                    const mats = [...document.querySelectorAll('mat-select, .mat-mdc-select')]
                        .filter(vis).length;
                    const nats = [...document.querySelectorAll('select')].filter(vis).length;
                    const opts = [...document.querySelectorAll(
                        '.cdk-overlay-container mat-option, ' +
                        '.cdk-overlay-container .mat-mdc-option, [role="option"]'
                    )].filter(vis).map((o) => (o.innerText || '').trim()).slice(0, 8);
                    const ctrls = [...root.querySelectorAll(
                        'input, select, textarea, mat-select, button, ' +
                        '[role="combobox"], [aria-haspopup], .ng-select, .p-dropdown'
                    )].filter(vis).slice(0, 18).map((el) => ({{
                        t: el.tagName.toLowerCase(),
                        ty: el.getAttribute('type') || '',
                        cls: String(
                            el.className && el.className.baseVal !== undefined
                                ? el.className.baseVal
                                : el.className || ''
                        ).slice(0, 36),
                        lab: (el.getAttribute('placeholder')
                            || el.getAttribute('aria-label')
                            || el.getAttribute('formcontrolname') || '').slice(0, 24),
                        tx: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 20),
                    }}));
                    return {{ mats, nats, opts, ctrls }};
                }}
                """
            )
            run._log(
                f"… {field_key} debug (pilgrim {pilgrim_index + 1}): "
                f"mat-selects={diag.get('mats')} native-selects={diag.get('nats')} "
                f"overlay-options={diag.get('opts')}"
            )
            run._log(
                f"… {field_key} controls (pilgrim {pilgrim_index + 1}): {diag.get('ctrls')}"
            )
        except Exception:
            pass
    return final_ok


async def _read_pilgrim_locator_state(page, index: int = 0) -> dict:
    """Read filled values from live Playwright locators."""
    state = {"name": "", "age": "", "gender": "", "id_proof": "", "id_num": ""}
    try:
        name_loc = await _find_pilgrim_input(page, r"^name", index, formcontrol_hint="name")
        if name_loc:
            state["name"] = (await name_loc.input_value()).strip()
        age_loc = await _find_pilgrim_input(page, r"^age", index, formcontrol_hint="age")
        if age_loc:
            state["age"] = (await age_loc.input_value()).strip()
        id_loc = await _find_pilgrim_input(
            page,
            r"photo\s*id\s*(card\s*)?(no\.?|number)|id\s*number|aadhaar|aadhar",
            index,
            formcontrol_hint="id",
        )
        if id_loc:
            state["id_num"] = (await id_loc.input_value()).strip()

        gender_field = await _find_pilgrim_select(page, r"^gender", index)
        if gender_field:
            state["gender"] = str(
                await gender_field.evaluate(
                    """el => {
                        const root = el.closest('mat-form-field, .mat-mdc-form-field') || el.parentElement;
                        const t = root?.querySelector(
                            '.mat-mdc-select-value-text, .mat-select-value-text, .mat-mdc-select-min-line'
                        );
                        return (t?.innerText || t?.textContent || el.innerText || '').trim();
                    }"""
                )
            ).strip()

        proof_field = await _find_pilgrim_select(page, r"photo\s*id\s*proof|id\s*proof", index)
        if proof_field:
            state["id_proof"] = str(
                await proof_field.evaluate(
                    """el => {
                        const root = el.closest('mat-form-field, .mat-mdc-form-field') || el.parentElement;
                        const t = root?.querySelector(
                            '.mat-mdc-select-value-text, .mat-select-value-text, .mat-mdc-select-min-line'
                        );
                        return (t?.innerText || t?.textContent || el.innerText || '').trim();
                    }"""
                )
            ).strip()
    except Exception:
        pass
    return state


async def _fill_pilgrim_row_irctc(
    page,
    run: "TravelRun",
    index: int,
    name: str,
    age: str,
    gender: str,
    id_proof: str,
    id_num: str,
    gender_vars: tuple[str, ...],
    proof_vars: tuple[str, ...],
) -> bool:
    """Fill one pilgrim row using IRCTC-style Playwright typing (click → press_sequentially)."""
    await _scroll_pilgrim_form_into_view(page, index)

    name_loc = await _find_pilgrim_input(page, r"^name", index, formcontrol_hint="name")
    if not name_loc:
        run._log(f"⚠ Pilgrim {index + 1}: Name field not found in Chrome")
        return False
    if not await _fill_angular_input_if_needed(name_loc, name):
        run._log(f"⚠ Pilgrim {index + 1}: could not type Name")
    await asyncio.sleep(0.12)

    age_loc = await _find_pilgrim_input(page, r"^age", index, formcontrol_hint="age")
    if age_loc:
        await _fill_angular_input_if_needed(age_loc, age)
    await asyncio.sleep(0.12)

    await _select_pilgrim_dropdown_robust(
        page, index, "gender", gender_vars, run=run
    )
    await asyncio.sleep(0.25)

    await _select_pilgrim_dropdown_robust(
        page, index, "idproof", proof_vars, run=run
    )
    await asyncio.sleep(0.25)

    id_loc = await _find_pilgrim_input(
        page,
        r"photo\s*id\s*(card\s*)?(no\.?|number)|id\s*number|aadhaar|aadhar",
        index,
        formcontrol_hint="id",
    )
    if id_loc:
        await _fill_angular_input_if_needed(id_loc, id_num)
    else:
        run._log(f"⚠ Pilgrim {index + 1}: Photo ID Number field not found")

    await asyncio.sleep(0.4)
    state = await _read_pilgrim_locator_state(page, index)
    ttd_state = await _read_pilgrim_ttd_state(page, index)
    for key in state:
        if not state[key] and (ttd_state.get(key) or "").strip():
            state[key] = ttd_state[key]

    checks = _row_values_match(state, name, age, gender, id_proof, id_num)
    if all(checks.values()):
        run._log(f"→ Pilgrim {index + 1} verified in Chrome")
        return True

    run._log(
        f"⚠ Pilgrim {index + 1} partial — "
        f"name={checks['name']} age={checks['age']} gender={checks['gender']} "
        f"id_type={checks['id_proof']} id#={checks['id_num']} "
        f"(dom: name={state.get('name', '')!r} gender={state.get('gender', '')!r} "
        f"proof={state.get('id_proof', '')!r} id={state.get('id_num', '')!r})"
    )
    return False


def _pilgrim_page_js() -> str:
    return """
    const normLabel = (s) => (s || '').replace(/\\*/g, '').trim().toLowerCase();

    const pilgrimRoot = () => {
        const path = (location.pathname || '').toLowerCase();
        if (/pilgrim[-_]detail/.test(path)) {
            for (const sel of [
                'app-pilgrim-details', 'app-pilgrim-detail', 'app-root main', 'main', 'form', 'section'
            ]) {
                const el = document.querySelector(sel);
                if (!el) continue;
                const fields = el.querySelectorAll('mat-form-field, .mat-mdc-form-field');
                if (fields.length >= 3) return el;
            }
            const all = [...document.querySelectorAll('mat-form-field, .mat-mdc-form-field')]
                .filter((f) => f.getBoundingClientRect().width > 20);
            if (all.length >= 3 && all.length <= 24) {
                return all[0].closest('form, main, section, mat-card, .mat-card, .container')
                    || document.body;
            }
        }
        for (const el of document.querySelectorAll('div, section, form, main, mat-card, .mat-card')) {
            const t = (el.innerText || '');
            if (!/pilgrim detail|devotee detail|ticket holder/i.test(t)) continue;
            if (/select any\\s*1\\s*slot|please select a darshan ticket/i.test(t)) continue;
            if (t.length > 50000) continue;
            const fields = el.querySelectorAll('mat-form-field, .mat-mdc-form-field');
            if (fields.length >= 3) return el;
        }
        const labelEls = [...document.querySelectorAll(
            'mat-label, label, .mdc-floating-label, .mat-mdc-floating-label'
        )];
        const pilgrimLabels = labelEls.filter((lab) => {
            const txt = normLabel(lab.innerText || lab.textContent);
            return /^(name|age|gender|photo id)/.test(txt);
        });
        if (pilgrimLabels.length >= 3) {
            let node = pilgrimLabels[0];
            for (let depth = 0; depth < 12 && node; depth++) {
                node = node.parentElement;
                if (!node) break;
                const count = node.querySelectorAll('mat-form-field, .mat-mdc-form-field').length;
                if (count >= 3 && count <= 40) return node;
            }
        }
        return document.body;
    };

    const pilgrimPositionState = (idx) => {
        const root = pilgrimRoot();
        const visible = (el) => el && el.offsetParent !== null;
        const byPos = (nodes) => nodes.filter(visible).sort((a, b) => {
            const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
            const dy = ra.top - rb.top;
            return Math.abs(dy) > 12 ? dy : ra.left - rb.left;
        });
        const inputs = byPos([...root.querySelectorAll('input:not([type=hidden])')]
            .filter((i) => !['radio', 'checkbox'].includes(i.type)
                && !i.closest('.ng-select, .p-dropdown, mat-select, [role="listbox"]')));
        const rawSelects = [...root.querySelectorAll(
            'mat-select, select, .ng-select, .p-dropdown, ' +
            '[role="combobox"], [aria-haspopup="listbox"]'
        )];
        const selects = byPos(rawSelects.filter(
            (el) => !rawSelects.some((o) => o !== el && o.contains(el))
        ));
        if (!selects.length && inputs.length >= (idx + 1) * 5) {
            // Input-only layout: name, age, gender, id-proof, id-number
            const b = idx * 5;
            return {
                name: (inputs[b]?.value || '').trim(),
                age: (inputs[b + 1]?.value || '').trim(),
                gender: (inputs[b + 2]?.value || '').trim(),
                id_proof: (inputs[b + 3]?.value || '').trim(),
                id_num: (inputs[b + 4]?.value || '').trim(),
            };
        }
        const bi = idx * 3;
        const bs = idx * 2;
        const mf = (el) => el?.closest('mat-form-field, .mat-mdc-form-field') || el?.parentElement;
        return {
            name: (inputs[bi]?.value || '').trim(),
            age: (inputs[bi + 1]?.value || '').trim(),
            gender: selectDisplay(mf(selects[bs])),
            id_proof: selectDisplay(mf(selects[bs + 1])),
            id_num: (inputs[bi + 2]?.value || '').trim(),
        };
    };

    const fieldContainer = (el) => {
        if (!el) return null;
        if (el.matches?.('mat-form-field, .mat-mdc-form-field')) return el;
        return el.closest('mat-form-field, .mat-mdc-form-field, .form-group, .form-field')
            || el.parentElement;
    };

    const fieldLabel = (mf) => {
        if (!mf) return '';
        const lab = mf.querySelector('mat-label, label, .mdc-floating-label, .mat-mdc-floating-label');
        let txt = normLabel(lab?.innerText || lab?.textContent);
        if (txt) return txt;
        const forId = lab?.getAttribute('for');
        if (forId) {
            const inp = document.getElementById(forId);
            txt = normLabel(inp?.placeholder || inp?.getAttribute('aria-label') || '');
            if (txt) return txt;
        }
        const inp = mf.querySelector('input:not([type=hidden])');
        return normLabel(inp?.placeholder || inp?.getAttribute('aria-label') || '');
    };

    const labeledMatFields = (root, pattern) => {
        const re = new RegExp(pattern, 'i');
        const out = [];
        const seen = new Set();
        const push = (mf) => {
            if (!mf || seen.has(mf)) return;
            const rect = mf.getBoundingClientRect();
            if (rect.width < 5 || rect.height < 5) return;
            seen.add(mf);
            out.push(mf);
        };
        for (const mf of root.querySelectorAll('mat-form-field, .mat-mdc-form-field')) {
            const txt = fieldLabel(mf);
            if (!txt || !re.test(txt)) continue;
            push(mf);
        }
        for (const lab of root.querySelectorAll(
            'mat-label, label, .mdc-floating-label, .mat-mdc-floating-label'
        )) {
            const txt = normLabel(lab.innerText || lab.textContent);
            if (!txt || !re.test(txt)) continue;
            push(fieldContainer(lab));
        }
        out.sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            const dy = ra.top - rb.top;
            if (Math.abs(dy) > 15) return dy;
            return ra.left - rb.left;
        });
        return out;
    };

    const pilgrimFieldSpecs = () => ([
        ['name', '^name\\s*\\*?$|^name|devotee|pilgrim\\s*name'],
        ['age', '^age\\s*\\*?$|^age'],
        ['gender', 'gender'],
        ['idproof', 'photo\\s*id\\s*proof|id\\s*proof|photo\\s*id\\s*type|id\\s*type'],
        ['idnum', 'photo\\s*id\\s*(card\\s*)?\\s*(no\\.?|number)|photo\\s*id\\s*no|id\\s*number|aadhaar|id\\s*card\\s*no'],
    ]);

    const pilgrimRowByLabels = (idx) => {
        const root = pilgrimRoot();
        const row = {};
        for (const [key, pattern] of pilgrimFieldSpecs()) {
            const fields = labeledMatFields(root, pattern);
            row[key] = fields[idx] || null;
        }
        const found = Object.values(row).filter(Boolean).length;
        return found >= 3 ? row : null;
    };

    const markPilgrimByLabels = (idx) => {
        document.querySelectorAll('[data-ttd-agent]').forEach(
            (el) => el.removeAttribute('data-ttd-agent')
        );
        const root = pilgrimRoot();
        root.scrollIntoView({ block: 'start', inline: 'nearest' });
        let row = pilgrimRowByLabels(idx);
        if (!row) {
            const fields = pilgrimRow(idx);
            if (fields && fields.length >= 3) {
                row = {
                    name: fields[0] || null,
                    age: fields[1] || null,
                    gender: fields[2] || null,
                    idproof: fields[3] || null,
                    idnum: fields[4] || null,
                };
            }
        }
        if (!row) return 0;
        let marked = 0;
        for (const [key, mf] of Object.entries(row)) {
            if (!mf) continue;
            mf.setAttribute('data-ttd-agent', `p-${idx}-${key}`);
            marked++;
        }
        return marked;
    };

    const selectTriggerEl = (mf) => {
        if (!mf) return null;
        return mf.querySelector(
            '.mat-mdc-select-trigger, mat-select, .mat-select-trigger, ' +
            '[role="combobox"], .mat-mdc-select, select'
        ) || mf.querySelector('mat-select') || mf;
    };

    const clickSelectOption = (variants) => {
        const opts = [...document.querySelectorAll(
            '.cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, ' +
            '.cdk-overlay-pane mat-option, mat-option[role="option"], [role="option"]'
        )].filter((o) => {
            const r = o.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && o.offsetParent !== null;
        });
        for (const v of variants) {
            const needle = (v || '').toLowerCase();
            for (const el of opts) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!t || t === 'select' || t === 'choose') continue;
                if (t === needle || t.startsWith(needle) || needle.startsWith(t)) {
                    el.scrollIntoView({ block: 'nearest' });
                    const r = el.getBoundingClientRect();
                    const ev = { bubbles: true, cancelable: true,
                                 clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
                    el.dispatchEvent(new PointerEvent('pointerdown', ev));
                    el.dispatchEvent(new MouseEvent('mousedown', ev));
                    el.dispatchEvent(new PointerEvent('pointerup', ev));
                    el.dispatchEvent(new MouseEvent('mouseup', ev));
                    el.click();
                    return t;
                }
            }
        }
        if (opts.length) {
            opts[0].click();
            return (opts[0].innerText || opts[0].textContent || '').trim();
        }
        return '';
    };

    const openSelectAndPick = (mf, variants) => {
        const trigger = selectTriggerEl(mf);
        if (!trigger) return '';
        trigger.scrollIntoView({ block: 'center', inline: 'nearest' });
        const r = trigger.getBoundingClientRect();
        const ev = { bubbles: true, cancelable: true,
                     clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
        trigger.dispatchEvent(new PointerEvent('pointerdown', ev));
        trigger.dispatchEvent(new MouseEvent('mousedown', ev));
        trigger.dispatchEvent(new PointerEvent('pointerup', ev));
        trigger.dispatchEvent(new MouseEvent('mouseup', ev));
        trigger.click();
        return clickSelectOption(variants);
    };

    const selectDisplay = (mf) => {
        if (!mf) return '';
        const nat = mf.querySelector('select');
        if (nat) {
            const o = nat.options[nat.selectedIndex];
            return (o?.textContent || '').replace(/\\s+/g, ' ').trim();
        }
        const el = mf.querySelector(
            '.mat-mdc-select-value-text, .mat-select-value-text, .mat-mdc-select-min-line, mat-select'
        );
        if (el) return (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        const ng = mf.querySelector('.ng-select .ng-value, .ng-value, .p-dropdown-label');
        if (ng) return (ng.innerText || ng.textContent || '').replace(/\\s+/g, ' ').trim();
        const combo = mf.matches?.('[role="combobox"], [aria-haspopup="listbox"]')
            ? mf
            : mf.querySelector('[role="combobox"], [aria-haspopup="listbox"]');
        if (combo) {
            return (combo.value || combo.innerText || combo.textContent || '')
                .replace(/\\s+/g, ' ').trim();
        }
        return '';
    };

    const inputValue = (mf) => {
        if (!mf) return '';
        const inp = mf.querySelector('input:not([type=hidden])');
        return (inp?.value || '').trim();
    };

    const groupMatRows = (root) => {
        const headers = [...root.querySelectorAll(
            'div, h3, h4, h5, span, p, mat-card-title, .card-title'
        )].filter((h) => /pilgrim\\s*\\d+/i.test((h.innerText || '').trim()));
        if (headers.length) {
            headers.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const rows = [];
            for (let i = 0; i < headers.length; i++) {
                const top = headers[i].getBoundingClientRect().top;
                const bottom = i + 1 < headers.length
                    ? headers[i + 1].getBoundingClientRect().top
                    : top + 520;
                const fields = [...root.querySelectorAll('mat-form-field, .mat-mdc-form-field')]
                    .filter((f) => {
                        const r = f.getBoundingClientRect();
                        return r.width > 20 && r.top >= top - 8 && r.top < bottom;
                    });
                fields.sort((a, b) => {
                    const ra = a.getBoundingClientRect();
                    const rb = b.getBoundingClientRect();
                    const dy = ra.top - rb.top;
                    if (Math.abs(dy) > 15) return dy;
                    return ra.left - rb.left;
                });
                if (fields.length >= 3) rows.push(fields);
            }
            if (rows.length) return rows;
        }

        const all = [...root.querySelectorAll('mat-form-field, .mat-mdc-form-field')]
            .filter((f) => {
                const r = f.getBoundingClientRect();
                return r.width > 20 && r.height > 8;
            });
        all.sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            const dy = ra.top - rb.top;
            if (Math.abs(dy) > 18) return dy;
            return ra.left - rb.left;
        });
        const rows = [];
        let current = [];
        let lastTop = -99999;
        for (const f of all) {
            const top = Math.round(f.getBoundingClientRect().top / 12) * 12;
            if (current.length && Math.abs(top - lastTop) > 18) {
                rows.push(current);
                current = [];
            }
            current.push(f);
            lastTop = top;
        }
        if (current.length) rows.push(current);
        return rows.filter((r) => {
            if (r.length < 3) return false;
            let inputs = 0;
            let selects = 0;
            for (const mf of r) {
                if (mf.querySelector('input:not([type=hidden])')) inputs += 1;
                if (mf.querySelector('mat-select, select')) selects += 1;
            }
            return inputs >= 1 && selects >= 1;
        });
    };

    const pilgrimRow = (idx) => {
        const byLabels = pilgrimRowByLabels(idx);
        if (byLabels) {
            return [
                byLabels.name,
                byLabels.age,
                byLabels.gender,
                byLabels.idproof,
                byLabels.idnum,
            ].filter(Boolean);
        }
        const root = pilgrimRoot();
        const rows = groupMatRows(root);
        return rows[idx] || null;
    };

    const setInputValue = (input, value) => {
        if (!input) return false;
        input.focus();
        input.click();
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
        return (input.value || '').trim() === String(value).trim();
    };
    """


async def _scroll_pilgrim_form_into_view(page, pilgrim_index: int = 0) -> None:
    """Scroll pilgrim section and the target row into the Chrome viewport."""
    try:
        heading = page.get_by_text(re.compile(r"Pilgrim\s+Details|Pilgrim\s+\d+", re.I))
        if await heading.count():
            await heading.first.scroll_into_view_if_needed()
        await page.evaluate(
            f"""
            (idx) => {{
                {_pilgrim_page_js()}
                const row = pilgrimRowByLabels(idx);
                const mf = row ? (row.name || row.age || row.gender) : null;
                if (mf) mf.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            }}
            """,
            pilgrim_index,
        )
        await asyncio.sleep(0.25)
    except Exception:
        pass


async def _mark_pilgrim_row(page, pilgrim_index: int) -> int:
    """Tag pilgrim fields by label (primary) or horizontal row position (fallback)."""
    marks = ("name", "age", "gender", "idproof", "idnum")
    try:
        marked = await page.evaluate(
            f"""
            (idx) => {{
                {_pilgrim_page_js()}
                const byLabel = markPilgrimByLabels(idx);
                if (byLabel >= 3) return byLabel;

                document.querySelectorAll('[data-ttd-agent]').forEach(
                    (el) => el.removeAttribute('data-ttd-agent')
                );
                const root = pilgrimRoot();
                root.scrollIntoView({{ block: 'start', inline: 'nearest' }});
                const row = pilgrimRow(idx);
                if (!row || !row.length) return byLabel;
                const labels = {list(marks)};
                row.slice(0, 5).forEach((mf, i) => {{
                    mf.setAttribute('data-ttd-agent', `p-${{idx}}-${{labels[i]}}`);
                }});
                return Math.max(byLabel, Math.min(row.length, 5));
            }}
            """,
            pilgrim_index,
        )
        return int(marked or 0)
    except Exception:
        return 0


async def _read_pilgrim_row_state(page, pilgrim_index: int) -> dict:
    try:
        return await page.evaluate(
            f"""
            (idx) => {{
                {_pilgrim_page_js()}
                const root = pilgrimRoot();
                const byLabels = pilgrimRowByLabels(idx);
                const row = pilgrimRow(idx);
                if (!row || !row.length) {{
                    const matCount = root.querySelectorAll('mat-form-field, .mat-mdc-form-field').length;
                    return {{
                        fields_found: 0,
                        rows: groupMatRows(root).length,
                        mat_fields: matCount,
                        label_fields: byLabels ? Object.values(byLabels).filter(Boolean).length : 0,
                        name: '', age: '', gender: '', id_proof: '', id_num: '',
                    }};
                }}

                let nameF, ageF, genderF, proofF, idF;
                if (byLabels) {{
                    nameF = byLabels.name;
                    ageF = byLabels.age;
                    genderF = byLabels.gender;
                    proofF = byLabels.idproof;
                    idF = byLabels.idnum;
                }} else {{
                    nameF = row[0];
                    ageF = row[1];
                    genderF = row[2];
                    proofF = row[3];
                    idF = row[4];
                }}
                return {{
                    name: inputValue(nameF),
                    age: inputValue(ageF),
                    gender: selectDisplay(genderF),
                    id_proof: selectDisplay(proofF),
                    id_num: inputValue(idF),
                    fields_found: row.length,
                    rows: groupMatRows(root).length,
                    mat_fields: root.querySelectorAll('mat-form-field, .mat-mdc-form-field').length,
                    label_fields: byLabels ? Object.values(byLabels).filter(Boolean).length : 0,
                }};
            }}
            """,
            pilgrim_index,
        )
    except Exception:
        return {}


def _row_values_match(state: dict, name: str, age: str, gender: str, id_proof: str, id_num: str) -> dict:
    """Return per-field ok flags after comparing DOM state to expected values."""
    g = (state.get("gender") or "").lower()
    p = (state.get("id_proof") or "").lower()
    gender_ok = any(v.lower() in g for v in _option_variants("gender", gender) if v)
    proof_ok = any(v.lower() in p for v in _option_variants("id_proof", id_proof) if v)
    if not g or re.search(r"select|choose|^gender$", g):
        gender_ok = False
    if not p or re.search(r"select|choose|^photo id", p):
        proof_ok = False
    return {
        "name": (state.get("name") or "").strip().lower() == name.strip().lower(),
        "age": (state.get("age") or "").strip() == age.strip(),
        "gender": gender_ok,
        "id_proof": proof_ok,
        "id_num": re.sub(r"\D", "", state.get("id_num") or "") == id_num,
    }


def _mat_option_selected(display: str, variants: tuple[str, ...]) -> bool:
    d = (display or "").lower()
    if not d or re.search(r"select|choose|^gender$|^photo", d):
        return False
    return any(v.lower() in d or d in v.lower() for v in variants if v)


def _option_variants(kind: str, value: str) -> tuple[str, ...]:
    v = (value or "").strip()
    if kind == "gender":
        if v.lower().startswith("f"):
            return ("Female", "FEMALE")
        if v.lower().startswith("m"):
            return ("Male", "MALE")
        if v.lower().startswith("t") or "other" in v.lower():
            return ("Transgender", "Other", "Others", "OTHER")
        return (v, "Female", "Male")
    if kind == "id_proof":
        if "aadhaar" in v.lower() or "aadhar" in v.lower():
            return (
                "Aadhaar Card",
                "AADHAAR CARD",
                "Aadhaar",
                "AADHAAR",
                "Aadhar Card",
                "Aadhar",
                "AADHAR",
            )
        return (v, "Aadhaar Card", "Aadhaar")
    return (v,)


async def _open_mat_select(select_loc) -> bool:
    try:
        await select_loc.scroll_into_view_if_needed()
        await select_loc.click()
        await asyncio.sleep(0.5)
        return True
    except Exception:
        return False


async def _pilgrim_mat_fields(page, index: int):
    """Five mat-form-fields per pilgrim row: Name, Age, Gender, Photo ID Proof, Photo ID Number."""
    section = page.locator("div, section, form").filter(
        has_text=re.compile(r"Pilgrim Details", re.I)
    )
    if await section.count():
        fields = section.first.locator("mat-form-field:visible")
    else:
        fields = page.locator("mat-form-field:visible")
    base = index * 5
    return fields, base


async def _pilgrim_section_locator(page):
    section = page.locator("div, section, form").filter(
        has_text=re.compile(r"Pilgrim\s+Details", re.I)
    )
    if await section.count():
        return section.first
    return page.locator("body")


async def _select_mat_dropdown(
    page,
    label_pattern: str,
    option_text: str,
    *,
    index: int = 0,
    kind: str = "gender",
) -> bool:
    variants = _option_variants(kind, option_text)
    try:
        label_re = re.compile(label_pattern, re.I)
        scope = await _pilgrim_section_locator(page)
        field = scope.locator("mat-form-field, .mat-mdc-form-field").filter(
            has=scope.locator("mat-label, label").filter(has_text=label_re)
        )
        if await field.count() > index:
            combo = field.nth(index).locator("mat-select, .mat-mdc-select, select").first
        else:
            field = page.locator("mat-form-field, .mat-mdc-form-field").filter(
                has=page.locator("mat-label, label").filter(has_text=label_re)
            )
            if await field.count() > index:
                combo = field.nth(index).locator("mat-select, .mat-mdc-select, select").first
            else:
                fields, base = await _pilgrim_mat_fields(page, index)
                offset = 2 if "gender" in label_pattern.lower() else 3
                combo = fields.nth(base + offset).locator("mat-select, select").first
        if not await combo.count():
            return False
        if await combo.evaluate("el => el.tagName.toLowerCase()") == "select":
            for v in variants:
                try:
                    await combo.select_option(label=v)
                    return True
                except Exception:
                    continue
            return False
        if not await _open_mat_select(combo):
            return False
        return await _pick_mat_option(page, variants)
    except Exception:
        return False


async def _fill_labeled_input(
    page,
    label_pattern: str,
    value: str,
    *,
    index: int = 0,
    field_offset: int | None = None,
) -> bool:
    try:
        label_re = re.compile(label_pattern, re.I)
        scope = await _pilgrim_section_locator(page)
        field = scope.locator("mat-form-field, .mat-mdc-form-field").filter(
            has=scope.locator("mat-label, label").filter(has_text=label_re)
        )
        if await field.count() > index:
            inp = field.nth(index).locator("input:visible").first
            if await inp.count() and await inp.is_visible():
                return await _fill_angular_input(inp, value)
        field = page.locator("mat-form-field, .mat-mdc-form-field").filter(
            has=page.locator("mat-label, label").filter(has_text=label_re)
        )
        if await field.count() > index:
            inp = field.nth(index).locator("input:visible").first
            if await inp.count() and await inp.is_visible():
                return await _fill_angular_input(inp, value)
    except Exception:
        pass
    if field_offset is not None:
        try:
            fields, base = await _pilgrim_mat_fields(page, index)
            inp = fields.nth(base + field_offset).locator("input:visible").first
            if await inp.count() and await inp.is_visible():
                return await _fill_angular_input(inp, value)
        except Exception:
            pass
    try:
        loc = page.get_by_label(re.compile(label_pattern, re.I))
        if await loc.count() > index:
            return await _fill_angular_input(loc.nth(index), value)
    except Exception:
        pass
    return False


async def _ttd_validation_alert_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    for (const pane of document.querySelectorAll(
                        'mat-dialog-container, .mat-mdc-dialog-container, .cdk-overlay-pane'
                    )) {
                        if (!pane.offsetParent) continue;
                        const t = (pane.innerText || pane.textContent || '').toLowerCase();
                        if (/validation fail|validation failure/i.test(t)) return true;
                        if (t.includes('alert') && t.includes('validation')) return true;
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _pilgrim_has_validation_errors(page) -> bool:
    """Inline mat-error under fields — not the modal Alert dialog."""
    if await _ttd_validation_alert_visible(page):
        return True
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    for (const e of document.querySelectorAll(
                        'mat-error, .mat-mdc-form-field-error, .mat-error'
                    )) {
                        if (!e.offsetParent) continue;
                        const t = (e.innerText || e.textContent || '').trim();
                        if (t.length > 2) return true;
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def clear_pilgrim_page_alerts(page, run: "TravelRun | None" = None) -> None:
    """Close any TTD validation Alert dialog on the pilgrim page."""
    class _SilentRun:
        def _log(self, _msg: str) -> None:
            return

    log_run = run or _SilentRun()
    for _ in range(3):
        if not await _ttd_validation_alert_visible(page):
            return
        if await _dismiss_ttd_validation_alert(page, log_run):  # type: ignore[arg-type]
            await asyncio.sleep(0.35)
            continue
        break


async def _fill_pilgrim_text_js(
    page,
    index: int,
    name: str,
    age: str,
    id_num: str = "",
    *,
    include_id: bool = False,
) -> bool:
    """Set Name/Age (and optionally ID#) via Angular input events."""
    try:
        return bool(
            await page.evaluate(
                f"""
                (args) => {{
                    const [idx, name, age, idNum, includeId] = args;
                    {_pilgrim_page_js()}
                    const row = pilgrimRowByLabels(idx);
                    if (!row) return false;
                    let ok = true;
                    if (row.name) {{
                        ok = setInputValue(row.name.querySelector('input:not([type=hidden])'), name) && ok;
                    }}
                    if (row.age) {{
                        ok = setInputValue(row.age.querySelector('input:not([type=hidden])'), age) && ok;
                    }}
                    if (includeId && row.idnum && idNum) {{
                        ok = setInputValue(row.idnum.querySelector('input:not([type=hidden])'), idNum) && ok;
                    }}
                    return ok;
                }}
                """,
                [index, name, age, id_num, include_id],
            )
        )
    except Exception:
        return False


def _ttd_field_finder_js() -> str:
    return """
    const normLabel = (s) => (s || '').replace(/\\*/g, '').trim().toLowerCase();

    const ttdPilgrimRoot = () => {
        const path = (location.pathname || '').toLowerCase();
        if (/pilgrim[-_]detail/.test(path)) {
            const all = [...document.querySelectorAll('mat-form-field, .mat-mdc-form-field')]
                .filter((f) => f.getBoundingClientRect().width > 20);
            if (all.length >= 3) {
                return all[0].closest('form, main, section, app-root') || document.body;
            }
        }
        for (const el of document.querySelectorAll('main, form, section, div')) {
            const t = (el.innerText || '');
            if (!/pilgrim detail|devotee detail/i.test(t)) continue;
            const fields = el.querySelectorAll('mat-form-field, .mat-mdc-form-field');
            if (fields.length >= 3) return el;
        }
        return document.body;
    };

    const labeledFields = (pattern, idx) => {
        const re = new RegExp(pattern, 'i');
        const root = ttdPilgrimRoot();
        const out = [];
        const seen = new Set();
        const push = (mf) => {
            if (!mf || seen.has(mf)) return;
            seen.add(mf);
            out.push(mf);
        };
        for (const mf of root.querySelectorAll('mat-form-field, .mat-mdc-form-field')) {
            const r = mf.getBoundingClientRect();
            if (r.width < 10 || r.height < 10) continue;
            const lab = mf.querySelector(
                'mat-label, label, .mat-mdc-floating-label, .mdc-floating-label'
            );
            const txt = normLabel(lab?.innerText || lab?.textContent);
            if (!txt || !re.test(txt)) continue;
            push(mf);
        }
        for (const lab of root.querySelectorAll(
            'mat-label, label, .mat-mdc-floating-label, .mdc-floating-label'
        )) {
            const txt = normLabel(lab.innerText || lab.textContent);
            if (!txt || !re.test(txt)) continue;
            let mf = lab.closest('mat-form-field, .mat-mdc-form-field');
            if (!mf) {
                const inp = lab.parentElement?.querySelector('input:not([type=hidden])');
                if (inp) mf = inp.closest('mat-form-field, .mat-mdc-form-field') || inp.parentElement;
            }
            push(mf);
        }
        for (const mf of root.querySelectorAll('mat-form-field, .mat-mdc-form-field')) {
            const inp = mf.querySelector('input:not([type=hidden])');
            const txt = normLabel(inp?.getAttribute('aria-label') || inp?.placeholder || '');
            if (txt && re.test(txt)) push(mf);
        }
        out.sort((a, b) => {
            const ra = (a.getBoundingClientRect ? a : a.parentElement).getBoundingClientRect();
            const rb = (b.getBoundingClientRect ? b : b.parentElement).getBoundingClientRect();
            const dy = ra.top - rb.top;
            if (Math.abs(dy) > 12) return dy;
            return ra.left - rb.left;
        });
        return out[idx] || null;
    };

    const selectDisplay = (mf) => {
        if (!mf) return '';
        const el = mf.querySelector(
            '.mat-mdc-select-value-text, .mat-select-value-text, .mat-mdc-select-min-line'
        );
        return (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
    };

    const inputInField = (mf) => mf?.querySelector('input:not([type=hidden])') || null;

    const selectTrigger = (mf) => mf?.querySelector(
        '.mat-mdc-select-trigger, .mat-mdc-select, mat-select, .mat-select-trigger'
    ) || null;
    """


async def _read_pilgrim_ttd_state(page, index: int = 0) -> dict:
    """Read pilgrim field values directly from TTD mat-label fields."""
    try:
        return await page.evaluate(
            f"""
            (idx) => {{
                {_ttd_field_finder_js()}
                const nameInp = inputInField(labeledFields('^name', idx));
                const ageInp = inputInField(labeledFields('^age', idx));
                const idInp = inputInField(labeledFields(
                    'photo\\\\s*id\\\\s*(card\\\\s*)?(no\\\\.?|number)|id\\\\s*number|aadhaar|aadhar', idx
                ));
                return {{
                    name: (nameInp?.value || '').trim(),
                    age: (ageInp?.value || '').trim(),
                    gender: selectDisplay(labeledFields('^gender', idx)),
                    id_proof: selectDisplay(labeledFields('photo\\\\s*id\\\\s*proof', idx)),
                    id_num: (idInp?.value || '').trim(),
                }};
            }}
            """,
            index,
        )
    except Exception:
        return {}


async def _ttd_field_display(page, label_pattern: str, index: int = 0) -> str:
    try:
        return str(
            await page.evaluate(
                f"""
                (args) => {{
                    const [pattern, idx] = args;
                    {_ttd_field_finder_js()}
                    const mf = labeledFields(pattern, idx);
                    return selectDisplay(mf);
                }}
                """,
                [label_pattern, index],
            )
        ).strip()
    except Exception:
        return ""


async def _ttd_fill_input_by_label(
    page, label_pattern: str, value: str, index: int = 0
) -> bool:
    """Fill a TTD pilgrim text field found by mat-label (locator + press_sequentially)."""
    hint = ""
    if re.search(r"name", label_pattern, re.I):
        hint = "name"
    elif re.search(r"^age", label_pattern, re.I):
        hint = "age"
    elif re.search(r"id\s*number|aadhaar", label_pattern, re.I):
        hint = "id"

    loc = await _find_pilgrim_input(page, label_pattern, index, formcontrol_hint=hint)
    if loc:
        if await _fill_angular_input(loc, value):
            return True

    try:
        coords = await page.evaluate(
            f"""
            (args) => {{
                const [pattern, idx] = args;
                {_ttd_field_finder_js()}
                const mf = labeledFields(pattern, idx);
                const inp = inputInField(mf);
                if (!inp) return null;
                inp.scrollIntoView({{ block: 'center', inline: 'nearest' }});
                const r = inp.getBoundingClientRect();
                return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
            }}
            """,
            [label_pattern, index],
        )
        if not coords:
            return False
        await page.mouse.click(float(coords["x"]), float(coords["y"]))
        await asyncio.sleep(0.1)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(value, delay=40)
        await asyncio.sleep(0.15)
        typed = await page.evaluate(
            f"""
            (args) => {{
                const [pattern, idx] = args;
                {_ttd_field_finder_js()}
                const inp = inputInField(labeledFields(pattern, idx));
                return (inp?.value || '').trim();
            }}
            """,
            [label_pattern, index],
        )
        return str(typed).strip() == value.strip()
    except Exception:
        return False


async def _ttd_select_by_field_label(
    page,
    label_pattern: str,
    variants: tuple[str, ...],
    index: int = 0,
) -> bool:
    """Open TTD mat-select by label and pick an option with mouse clicks."""
    display = await _ttd_field_display(page, label_pattern, index)
    if _mat_option_selected(display, variants):
        return True
    try:
        trigger = await page.evaluate(
            f"""
            (args) => {{
                const [pattern, idx] = args;
                {_ttd_field_finder_js()}
                const mf = labeledFields(pattern, idx);
                const el = selectTrigger(mf);
                if (!el) return null;
                el.scrollIntoView({{ block: 'center', inline: 'nearest' }});
                const r = el.getBoundingClientRect();
                return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
            }}
            """,
            [label_pattern, index],
        )
        if not trigger:
            return False

        for _ in range(4):
            await page.mouse.click(float(trigger["x"]), float(trigger["y"]))
            await asyncio.sleep(0.9)
            picked = await page.evaluate(
                """
                (variants) => {
                    const opts = [...document.querySelectorAll(
                        '.cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, ' +
                        '.cdk-overlay-pane mat-option, mat-option.mat-mdc-option, [role="option"]'
                    )].filter((o) => {
                        const r = o.getBoundingClientRect();
                        return r.width > 8 && r.height > 8;
                    });
                    for (const v of variants) {
                        const needle = (v || '').toLowerCase();
                        for (const o of opts) {
                            const t = (o.innerText || o.textContent || '').trim().toLowerCase();
                            if (!t || t === 'select' || t === 'choose') continue;
                            if (t === needle || t.includes(needle) || needle.includes(t)) {
                                const r = o.getBoundingClientRect();
                                const x = r.x + r.width / 2;
                                const y = r.y + r.height / 2;
                                const pe = { bubbles: true, cancelable: true, clientX: x, clientY: y };
                                o.dispatchEvent(new PointerEvent('pointerdown', pe));
                                o.dispatchEvent(new MouseEvent('mousedown', pe));
                                o.dispatchEvent(new PointerEvent('pointerup', pe));
                                o.dispatchEvent(new MouseEvent('mouseup', pe));
                                o.click();
                                return t;
                            }
                        }
                    }
                    return '';
                }
                """,
                list(variants),
            )
            if picked:
                await asyncio.sleep(0.45)
                if _mat_option_selected(
                    await _ttd_field_display(page, label_pattern, index), variants
                ):
                    return True
            if await _pick_mat_option(page, variants):
                await asyncio.sleep(0.4)
                if _mat_option_selected(
                    await _ttd_field_display(page, label_pattern, index), variants
                ):
                    return True
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.25)

        # Keyboard: type option prefix then Enter
        await page.mouse.click(float(trigger["x"]), float(trigger["y"]))
        await asyncio.sleep(0.75)
        for v in variants:
            text = re.sub(r"[^a-zA-Z0-9 ]", "", v).strip()
            if text:
                await page.keyboard.type(text[:6], delay=70)
                await asyncio.sleep(0.2)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.4)
            if _mat_option_selected(
                await _ttd_field_display(page, label_pattern, index), variants
            ):
                return True
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return _mat_option_selected(await _ttd_field_display(page, label_pattern, index), variants)


async def _ensure_pilgrim_dropdowns(
    page,
    prefix: str,
    index: int,
    gender_vars: tuple[str, ...],
    proof_vars: tuple[str, ...],
    run: "TravelRun | None" = None,
) -> None:
    """Gender and Photo ID Proof must be selected before Aadhaar number."""
    await clear_pilgrim_page_alerts(page, run)

    for attempt in range(5):
        if await _ttd_select_by_field_label(page, r"^gender", gender_vars, index):
            break
        await clear_pilgrim_page_alerts(page, run)
        await _select_marked_dropdown(page, index, "gender", gender_vars)
        await _select_mat_dropdown(page, r"gender", gender_vars[0], index=index, kind="gender")
        await asyncio.sleep(0.3)
        if _mat_option_selected(await _ttd_field_display(page, r"^gender", index), gender_vars):
            break

    await clear_pilgrim_page_alerts(page, run)

    for attempt in range(5):
        if await _ttd_select_by_field_label(page, r"photo\s*id\s*proof", proof_vars, index):
            break
        await clear_pilgrim_page_alerts(page, run)
        await _select_marked_dropdown(page, index, "idproof", proof_vars)
        await _select_mat_dropdown(
            page, r"photo\s*id\s*proof|id\s*proof", proof_vars[0], index=index, kind="id_proof"
        )
        await asyncio.sleep(0.3)
        if _mat_option_selected(
            await _ttd_field_display(page, r"photo\s*id\s*proof", index), proof_vars
        ):
            break


async def _fill_pilgrim_id_number(
    page, prefix: str, index: int, id_num: str, run: "TravelRun | None" = None
) -> None:
    """Type Aadhaar slowly after Photo ID Proof is selected."""
    await clear_pilgrim_page_alerts(page, run)
    if await _ttd_fill_input_by_label(page, r"photo\s*id\s*number|id\s*number", id_num, index):
        return
    try:
        id_loc = page.locator(f'[data-ttd-agent="{prefix}-idnum"] input:visible').first
        if await id_loc.count():
            await id_loc.scroll_into_view_if_needed()
            await id_loc.click(click_count=3)
            await id_loc.press("Backspace")
            await id_loc.press_sequentially(id_num, delay=55)
            return
    except Exception:
        pass
    await _fill_marked_input(page, index, "idnum", id_num)
    try:
        id_loc = page.get_by_label(re.compile(r"photo\s*id.*number|id\s*number|aadhaar", re.I))
        if await id_loc.count() > index:
            inp = id_loc.nth(index)
            await inp.scroll_into_view_if_needed()
            await inp.click(click_count=3)
            await inp.press("Backspace")
            await inp.press_sequentially(id_num, delay=55)
    except Exception:
        pass
    await _fill_pilgrim_text_js(page, index, "", "", id_num, include_id=True)


async def _fill_pilgrim_row_indexed(
    page,
    index: int,
    name: str,
    age: str,
    id_num: str,
    gender_vars: tuple[str, ...],
    proof_vars: tuple[str, ...],
) -> bool:
    """Last-resort fill: pilgrim N = Nth group of visible inputs + mat-selects."""
    prefix = f"p-{index}"
    try:
        marked = await page.evaluate(
            f"""
            (args) => {{
                const [idx, name, age] = args;
                {_pilgrim_page_js()}
                const root = pilgrimRoot();
                const inputs = [...root.querySelectorAll('input:not([type=hidden])')]
                    .filter((inp) => inp.offsetParent !== null);
                const selects = [...root.querySelectorAll('mat-select, select')]
                    .filter((sel) => sel.offsetParent !== null);
                const perInputs = 3;
                const perSelects = 2;
                const baseIn = idx * perInputs;
                const baseSel = idx * perSelects;
                if (inputs[baseIn]) setInputValue(inputs[baseIn], name);
                if (inputs[baseIn + 1]) setInputValue(inputs[baseIn + 1], age);
                const specs = [
                    ['gender', selects[baseSel]],
                    ['idproof', selects[baseSel + 1]],
                ];
                let marked = 0;
                for (const [key, el] of specs) {{
                    const mf = el?.closest('mat-form-field, .mat-mdc-form-field') || el?.parentElement;
                    if (mf) {{
                        mf.setAttribute('data-ttd-agent', `p-${{idx}}-${{key}}`);
                        marked++;
                    }}
                }}
                if (inputs[baseIn]) {{
                    const mf = inputs[baseIn].closest('mat-form-field, .mat-mdc-form-field');
                    if (mf) {{ mf.setAttribute('data-ttd-agent', `p-${{idx}}-name`); marked++; }}
                }}
                if (inputs[baseIn + 1]) {{
                    const mf = inputs[baseIn + 1].closest('mat-form-field, .mat-mdc-form-field');
                    if (mf) {{ mf.setAttribute('data-ttd-agent', `p-${{idx}}-age`); marked++; }}
                }}
                if (inputs[baseIn + 2]) {{
                    const mf = inputs[baseIn + 2].closest('mat-form-field, .mat-mdc-form-field');
                    if (mf) {{ mf.setAttribute('data-ttd-agent', `p-${{idx}}-idnum`); marked++; }}
                }}
                return marked;
            }}
            """,
            [index, name, age],
        )
        if int(marked or 0) >= 2:
            await _ensure_pilgrim_dropdowns(page, prefix, index, gender_vars, proof_vars)
            await _fill_pilgrim_id_number(page, prefix, index, id_num)
            await _ttd_select_by_field_label(page, r"^gender", gender_vars, index)
            await _ttd_select_by_field_label(page, r"photo\s*id\s*proof", proof_vars, index)
            await _ttd_fill_input_by_label(page, r"photo\s*id\s*number|id\s*number", id_num, index)
            return True
    except Exception:
        pass
    return False


async def _fill_pilgrim_row_playwright(
    page,
    index: int,
    name: str,
    age: str,
    gender: str,
    id_proof: str,
    id_num: str,
    gender_vars: tuple[str, ...],
    proof_vars: tuple[str, ...],
) -> None:
    """Playwright label/index fallback when DOM marking finds no pilgrim row."""
    prefix = f"p-{index}"
    await clear_pilgrim_page_alerts(page)
    await _fill_pilgrim_text_js(page, index, name, age)
    await _fill_labeled_input(page, r"^name", name, index=index, field_offset=0)
    await asyncio.sleep(0.12)
    await _fill_labeled_input(page, r"^age", age, index=index, field_offset=1)
    await asyncio.sleep(0.12)
    await _mark_pilgrim_row(page, index)
    await _ensure_pilgrim_dropdowns(page, prefix, index, gender_vars, proof_vars)
    await _fill_pilgrim_id_number(page, prefix, index, id_num)
    if await _mark_pilgrim_row(page, index) < 3:
        await _fill_pilgrim_row_indexed(
            page, index, name, age, id_num, gender_vars, proof_vars
        )
    await _ttd_fill_input_by_label(page, r"^name", name, index)
    await _ttd_fill_input_by_label(page, r"^age", age, index)
    await _ttd_select_by_field_label(page, r"^gender", gender_vars, index)
    await _ttd_select_by_field_label(page, r"photo\s*id\s*proof", proof_vars, index)
    await _ttd_fill_input_by_label(page, r"photo\s*id\s*number|id\s*number", id_num, index)


async def _dismiss_ttd_validation_alert(page, run: "TravelRun") -> bool:
    """Click **Retry** / OK on the TTD 'validation failures' Alert modal."""
    try:
        dismissed = await page.evaluate(
            """
            () => {
                const panes = [...document.querySelectorAll(
                    'mat-dialog-container, .mat-mdc-dialog-container, .cdk-overlay-pane'
                )].filter((p) => p.offsetParent !== null);
                for (const pane of panes) {
                    const text = (pane.innerText || pane.textContent || '').toLowerCase();
                    if (!/validation|alert/i.test(text)) continue;
                    const buttons = [...pane.querySelectorAll('button, a, [role="button"]')];
                    for (const label of ['retry', 'ok', 'close', 'got it']) {
                        const btn = buttons.find((b) => {
                            const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                            return t === label || t.includes(label);
                        });
                        if (btn) {
                            btn.click();
                            return (btn.innerText || btn.textContent || '').trim();
                        }
                    }
                    if (buttons.length) {
                        buttons[buttons.length - 1].click();
                        return (buttons[buttons.length - 1].innerText || '').trim();
                    }
                }
                return '';
            }
            """
        )
        if dismissed:
            run._log(f"→ Closed validation alert ({dismissed})")
            await asyncio.sleep(0.45)
            return True
    except Exception:
        pass
    try:
        dialog = page.locator("mat-dialog-container, .mat-mdc-dialog-container").filter(
            has_text=re.compile(r"validation fail", re.I)
        )
        if await dialog.count():
            run._log("⚠ Validation alert — closing dialog")
            for pattern in (r"^retry$", r"^ok$", r"^close$"):
                btn = dialog.first.get_by_role(
                    "button", name=re.compile(pattern, re.I)
                )
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click()
                    await asyncio.sleep(0.45)
                    return True
        if await click_text(page, r"^retry$", timeout=2000):
            await asyncio.sleep(0.45)
            return True
    except Exception:
        pass
    return False


async def _pick_mat_option(page, variants: tuple[str, ...]) -> bool:
    """Click a mat-option in the open CDK overlay."""
    try:
        panel = page.locator(
            ".cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, "
            "mat-option, .mat-mdc-option"
        )
        await panel.first.wait_for(state="visible", timeout=3500)
    except Exception:
        await asyncio.sleep(0.5)
    for variant in variants:
        for sel in (
            page.locator(
                ".cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option"
            ).filter(has_text=re.compile(re.escape(variant), re.I)),
            page.locator(".cdk-overlay-pane mat-option, .cdk-overlay-pane .mat-mdc-option").filter(
                has_text=re.compile(re.escape(variant), re.I)
            ),
            page.get_by_role("option", name=re.compile(re.escape(variant), re.I)),
        ):
            try:
                if await sel.count():
                    await sel.first.scroll_into_view_if_needed()
                    await sel.first.click(force=True)
                    await asyncio.sleep(0.45)
                    return True
            except Exception:
                continue
    try:
        picked = await page.evaluate(
            """
            (variants) => {
                const opts = [...document.querySelectorAll(
                    '.cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, ' +
                    'mat-option[role="option"], [role="option"]'
                )].filter(o => o.offsetParent !== null);
                for (const v of variants) {
                    const needle = (v || '').toLowerCase();
                    for (const el of opts) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (!t || t === 'select' || t === 'choose') continue;
                        if (t === needle || t.startsWith(needle) || needle.startsWith(t)) {
                            el.click();
                            return t;
                        }
                    }
                }
                if (opts.length) { opts[0].click(); return (opts[0].innerText || '').trim(); }
                return '';
            }
            """,
            list(variants),
        )
        if picked:
            await asyncio.sleep(0.4)
            return True
    except Exception:
        pass
    try:
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.12)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.35)
        return True
    except Exception:
        pass
    return False


async def _open_marked_select(page, pilgrim_index: int, field_key: str):
    """Return Playwright locator for mat-select trigger inside a marked pilgrim field."""
    field = page.locator(f'[data-ttd-agent="p-{pilgrim_index}-{field_key}"]')
    if not await field.count():
        return None
    trigger = field.locator(
        "mat-select, .mat-mdc-select-trigger, .mat-select-trigger, select"
    ).first
    if await trigger.count():
        return trigger
    return field


async def _fill_marked_input(page, pilgrim_index: int, field_key: str, value: str) -> bool:
    """Fill text input inside a data-ttd-agent marked pilgrim field."""
    field = page.locator(f'[data-ttd-agent="p-{pilgrim_index}-{field_key}"]')
    if await field.count():
        inp = field.locator("input:visible:not([type=hidden])").first
        if await inp.count():
            if await _fill_angular_input(inp, value):
                return True

    try:
        ok = await page.evaluate(
            f"""
            (args) => {{
                {_pilgrim_page_js()}
                const {{ idx, key, value }} = args;
                const row = pilgrimRowByLabels(idx) || {{}};
                if (!row[key]) {{
                    const fields = pilgrimRow(idx);
                    const map = ['name','age','gender','idproof','idnum'];
                    const i = map.indexOf(key);
                    if (fields && i >= 0) row[key] = fields[i];
                }}
                const mf = row[key];
                if (!mf) return false;
                const inp = mf.querySelector('input:not([type=hidden])');
                return setInputValue(inp, value);
            }}
            """,
            {"idx": pilgrim_index, "key": field_key, "value": value},
        )
        return bool(ok)
    except Exception:
        return False


async def _select_marked_dropdown(
    page,
    pilgrim_index: int,
    field_key: str,
    variants: tuple[str, ...],
) -> bool:
    """Open marked mat-select and pick an option."""
    trigger = await _open_marked_select(page, pilgrim_index, field_key)
    if trigger:
        try:
            await trigger.scroll_into_view_if_needed()
            for _ in range(4):
                await trigger.click(force=True)
                await asyncio.sleep(0.6)
                if await _pick_mat_option(page, variants):
                    await asyncio.sleep(0.35)
                    return True
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            if await _mat_overlay_visible(page):
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.12)
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.35)
                return True
            await page.keyboard.press("Escape")
        except Exception:
            pass

    try:
        picked = await page.evaluate(
            f"""
            (args) => {{
                {_pilgrim_page_js()}
                const {{ idx, key, variants }} = args;
                let row = pilgrimRowByLabels(idx);
                if (!row) {{
                    const fields = pilgrimRow(idx);
                    if (fields && fields.length >= 3) {{
                        row = {{
                            name: fields[0], age: fields[1], gender: fields[2],
                            idproof: fields[3], idnum: fields[4],
                        }};
                    }}
                }}
                const mf = row ? row[key] : null;
                if (!mf) return '';
                return openSelectAndPick(mf, variants);
            }}
            """,
            {"idx": pilgrim_index, "key": field_key, "variants": list(variants)},
        )
        if picked and not re.search(r"^select$|^choose$", str(picked).strip(), re.I):
            await asyncio.sleep(0.4)
            return True
    except Exception:
        pass
    return False


def _spat_dom_js() -> str:
    """Resilient SPAT pilgrim form DOM helpers (pilgrim-section mat-form-fields)."""
    return r"""
    const normLabel = (s) => (s || '').replace(/\*/g, '').trim().toLowerCase();

    const pilgrimRoot = () => {
        const path = (location.pathname || '').toLowerCase();
        if (/pilgrim[-_]detail/.test(path)) {
            for (const sel of [
                'app-pilgrim-details', 'app-pilgrim-detail', 'app-root main', 'main', 'form', 'section'
            ]) {
                const el = document.querySelector(sel);
                if (!el) continue;
                const fields = el.querySelectorAll('mat-form-field, .mat-mdc-form-field');
                if (fields.length >= 3) return el;
            }
            const all = [...document.querySelectorAll('mat-form-field, .mat-mdc-form-field')]
                .filter((f) => f.getBoundingClientRect().width > 20);
            if (all.length >= 3 && all.length <= 24) {
                return all[0].closest('form, main, section, mat-card, .mat-card, .container')
                    || document.body;
            }
        }
        for (const el of document.querySelectorAll('div, section, form, main, mat-card, .mat-card')) {
            const t = (el.innerText || '');
            if (!/pilgrim detail|devotee detail|ticket holder/i.test(t)) continue;
            if (/select any\s*1\s*slot|please select a darshan ticket/i.test(t)) continue;
            if (t.length > 50000) continue;
            const fields = el.querySelectorAll('mat-form-field, .mat-mdc-form-field');
            if (fields.length >= 3) return el;
        }
        const labelEls = [...document.querySelectorAll(
            'mat-label, label, .mdc-floating-label, .mat-mdc-floating-label'
        )];
        const pilgrimLabels = labelEls.filter((lab) => {
            const txt = normLabel(lab.innerText || lab.textContent);
            return /^(name|age|gender|photo id)/.test(txt);
        });
        if (pilgrimLabels.length >= 3) {
            let node = pilgrimLabels[0];
            for (let depth = 0; depth < 12 && node; depth++) {
                node = node.parentElement;
                if (!node) break;
                const count = node.querySelectorAll('mat-form-field, .mat-mdc-form-field').length;
                if (count >= 3 && count <= 40) return node;
            }
        }
        return document.body;
    };

    const allFields = () => {
        const root = pilgrimRoot();
        return [...root.querySelectorAll(
            'mat-form-field, .mat-mdc-form-field, .mat-form-field'
        )].filter(mf => {
            const r = mf.getBoundingClientRect();
            return mf.offsetParent !== null && r.width > 20 && r.height > 10;
        }).sort((a, b) => {
            const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
            const dy = ra.top - rb.top;
            return Math.abs(dy) > 12 ? dy : ra.left - rb.left;
        });
    };

    const tagFields = () => {
        document.querySelectorAll('[data-ttd-spat-idx]').forEach(
            (el) => el.removeAttribute('data-ttd-spat-idx')
        );
        allFields().forEach((mf, i) => mf.setAttribute('data-ttd-spat-idx', String(i)));
    };

    const fieldLabel = mf => {
        const el = mf.querySelector(
            'mat-label, label, .mat-mdc-floating-label, .mdc-floating-label, ' +
            '.mat-label, [class*="label"]'
        );
        let txt = normLabel(el?.innerText || el?.textContent);
        if (txt) return txt;
        const inp = mf.querySelector('input:not([type=hidden])');
        txt = normLabel(inp?.getAttribute('aria-label') || inp?.placeholder || '');
        if (txt) return txt;
        const sel = mf.querySelector('mat-select, .mat-mdc-select');
        return normLabel(sel?.getAttribute('aria-label') || sel?.getAttribute('placeholder') || '');
    };

    const fieldType = mf => {
        if (mf.querySelector('mat-select, .mat-mdc-select, select')) return 'select';
        if (mf.querySelector('input:not([type=hidden])')) return 'input';
        return 'unknown';
    };

    const selectDisplay = mf => {
        if (!mf) return '';
        const nat = mf.querySelector('select');
        if (nat) {
            const o = nat.options[nat.selectedIndex];
            return (o?.textContent || '').replace(/\s+/g, ' ').trim();
        }
        const el = mf.querySelector(
            '.mat-mdc-select-value-text, .mat-select-value-text, ' +
            '.mat-mdc-select-min-line, [class*="select-value"]'
        );
        return (el?.innerText || el?.textContent || '').replace(/\s+/g,' ').trim();
    };

    const snapshot = () => {
        tagFields();
        return allFields().map((mf, i) => ({
            index: i,
            label: fieldLabel(mf),
            type: fieldType(mf),
            value: fieldType(mf) === 'input'
                ? (mf.querySelector('input')?.value || '').trim()
                : selectDisplay(mf),
        }));
    };

    const fieldCoords = (mf, isSelect) => {
        const el = isSelect
            ? (mf.querySelector('.mat-mdc-select-trigger, mat-select, .mat-select-trigger') || mf)
            : mf.querySelector('input:not([type=hidden])');
        if (!el) return null;
        el.scrollIntoView({ block: 'center' });
        const r = el.getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    };

    const clickOption = (variants) => {
        const opts = [...document.querySelectorAll(
            '.cdk-overlay-container mat-option, .cdk-overlay-container .mat-mdc-option, ' +
            'mat-option[role="option"], [role="option"]'
        )].filter(o => {
            const r = o.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        });
        for (const v of variants) {
            const needle = v.toLowerCase();
            for (const el of opts) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!t || t === 'select' || t === 'choose') continue;
                if (t === needle || t.startsWith(needle) || needle.startsWith(t)) {
                    const r = el.getBoundingClientRect();
                    const ev = { bubbles:true, cancelable:true,
                                 clientX: r.x+r.width/2, clientY: r.y+r.height/2 };
                    el.dispatchEvent(new MouseEvent('mousedown', ev));
                    el.dispatchEvent(new MouseEvent('mouseup', ev));
                    el.click();
                    return t;
                }
            }
        }
        if (opts.length) { opts[0].click(); return (opts[0].innerText||'').trim(); }
        return '';
    };

    const setInput = (inp, value) => {
        if (!inp) return false;
        inp.focus(); inp.click();
        const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        if (desc?.set) desc.set.call(inp, value);
        ['input','change','blur'].forEach(ev =>
            inp.dispatchEvent(new Event(ev, { bubbles: true }))
        );
        return (inp.value||'').trim() === String(value).trim();
    };
    """


def _spat_identify_fields(fields: list[dict], pilgrim_index: int) -> dict:
    """Map label patterns → field indices for pilgrim N (label + position fallback)."""
    patterns = {
        "name": re.compile(r"^name|devotee|pilgrim\s*name", re.I),
        "age": re.compile(r"^age", re.I),
        "gender": re.compile(r"gender", re.I),
        "id_proof": re.compile(r"photo\s*id\s*(proof|type)|id\s*(proof|type)", re.I),
        "id_num": re.compile(
            r"photo\s*id\s*(card\s*)?(no\.?|number)|id\s*(card\s*)?(no\.?|number)|aadhaar|aadhar",
            re.I,
        ),
    }
    result: dict[str, int | None] = {}
    seen: set[int] = set()

    for key, pat in patterns.items():
        matches = [f for f in fields if pat.search(f.get("label", ""))]
        target = (
            matches[pilgrim_index]
            if len(matches) > pilgrim_index
            else matches[0] if matches else None
        )
        if target and target["index"] not in seen:
            result[key] = target["index"]
            seen.add(target["index"])
        else:
            result[key] = None

    inputs = [f for f in fields if f.get("type") == "input"]
    selects = [f for f in fields if f.get("type") == "select"]
    base_in = pilgrim_index * 3
    base_sel = pilgrim_index * 2

    if result.get("name") is None and len(inputs) > base_in:
        result["name"] = inputs[base_in]["index"]
    if result.get("age") is None and len(inputs) > base_in + 1:
        result["age"] = inputs[base_in + 1]["index"]
    if result.get("gender") is None and len(selects) > base_sel:
        result["gender"] = selects[base_sel]["index"]
    if result.get("id_proof") is None and len(selects) > base_sel + 1:
        result["id_proof"] = selects[base_sel + 1]["index"]
    if result.get("id_num") is None and len(inputs) > base_in + 2:
        result["id_num"] = inputs[base_in + 2]["index"]

    return result


async def _spat_get_field_snapshot(page) -> list[dict]:
    try:
        return await page.evaluate(f"() => {{ {_spat_dom_js()} return snapshot(); }}")
    except Exception:
        return []


async def _spat_field_locator(page, field_index: int):
    """Playwright locator for a tagged pilgrim mat-form-field."""
    loc = page.locator(f'[data-ttd-spat-idx="{field_index}"]')
    if await loc.count():
        return loc.first
    await _spat_get_field_snapshot(page)
    loc = page.locator(f'[data-ttd-spat-idx="{field_index}"]')
    if await loc.count():
        return loc.first
    return None


async def _spat_select_display(page, field_index: int) -> str:
    try:
        return str(
            await page.evaluate(
                f"""
                (idx) => {{
                    {_spat_dom_js()}
                    return selectDisplay(allFields()[idx]);
                }}
                """,
                field_index,
            )
        ).strip()
    except Exception:
        return ""


async def _spat_fill_text_field_by_index(
    page, field_index: int, value: str, *, skip_if_set: bool = False
) -> bool:
    await _spat_get_field_snapshot(page)
    field = await _spat_field_locator(page, field_index)
    if field:
        inp = field.locator("input:visible:not([type=hidden])").first
        if await inp.count():
            try:
                if skip_if_set:
                    current = (await inp.input_value()).strip()
                    if current == str(value).strip() or (
                        value.strip() and current.lower() == value.strip().lower()
                    ):
                        return True
                fill_fn = _fill_angular_input_if_needed if skip_if_set else _fill_angular_input
                if await fill_fn(inp, value):
                    typed = (await inp.input_value()).strip()
                    if typed == str(value).strip():
                        return True
            except Exception:
                pass

    coords = await page.evaluate(
        f"""
        (idx) => {{
            {_spat_dom_js()}
            const mf = allFields()[idx];
            return mf ? fieldCoords(mf, false) : null;
        }}
        """,
        field_index,
    )
    if coords:
        await page.mouse.click(float(coords["x"]), float(coords["y"]))
        await asyncio.sleep(0.15)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(str(value), delay=45)
        await asyncio.sleep(0.2)

    ok = bool(
        await page.evaluate(
            f"""
            (args) => {{
                {_spat_dom_js()}
                const mf = allFields()[args.idx];
                const inp = mf?.querySelector('input:not([type=hidden])');
                return setInput(inp, args.value);
            }}
            """,
            {"idx": field_index, "value": value},
        )
    )
    if ok:
        return True

    if field:
        inp = field.locator("input:visible:not([type=hidden])").first
        if await inp.count():
            try:
                typed = (await inp.input_value()).strip()
                return typed == str(value).strip()
            except Exception:
                pass
    return False


async def _spat_open_and_pick_dropdown(
    page, field_index: int, variants: tuple[str, ...]
) -> bool:
    if _mat_option_selected(await _spat_select_display(page, field_index), variants):
        return True

    await _spat_get_field_snapshot(page)
    field = await _spat_field_locator(page, field_index)

    if field:
        trigger = field.locator(
            "mat-select, .mat-mdc-select-trigger, .mat-select-trigger, select"
        ).first
        if await trigger.count():
            try:
                tag = await trigger.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    for variant in variants:
                        try:
                            await trigger.select_option(label=variant)
                            if _mat_option_selected(
                                await _spat_select_display(page, field_index), variants
                            ):
                                return True
                        except Exception:
                            continue
                else:
                    for _ in range(4):
                        await trigger.scroll_into_view_if_needed()
                        await trigger.click(force=True)
                        await asyncio.sleep(0.65)
                        if await _pick_mat_option(page, variants):
                            await asyncio.sleep(0.35)
                            if _mat_option_selected(
                                await _spat_select_display(page, field_index), variants
                            ):
                                return True
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.25)

                    if await _mat_overlay_visible(page):
                        await page.keyboard.press("ArrowDown")
                        await asyncio.sleep(0.12)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(0.35)
                        if _mat_option_selected(
                            await _spat_select_display(page, field_index), variants
                        ):
                            return True
                    await page.keyboard.press("Escape")
            except Exception:
                pass

    coords = await page.evaluate(
        f"""
        (idx) => {{
            {_spat_dom_js()}
            const mf = allFields()[idx];
            return mf ? fieldCoords(mf, true) : null;
        }}
        """,
        field_index,
    )
    if not coords:
        return _mat_option_selected(await _spat_select_display(page, field_index), variants)

    for _ in range(4):
        await page.mouse.click(float(coords["x"]), float(coords["y"]))
        await asyncio.sleep(0.9)
        if await _mat_overlay_visible(page) and await _pick_mat_option(page, variants):
            await asyncio.sleep(0.35)
            if _mat_option_selected(await _spat_select_display(page, field_index), variants):
                return True
        picked = await page.evaluate(
            f"""
            (variants) => {{
                {_spat_dom_js()}
                return clickOption(variants);
            }}
            """,
            list(variants),
        )
        await asyncio.sleep(0.4)
        display = await _spat_select_display(page, field_index)
        if display and not re.search(r"^select$|^choose$", display.strip(), re.I):
            return True
        if picked and not re.search(r"^select$|^choose$", str(picked).strip(), re.I):
            return True
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.35)

    return _mat_option_selected(await _spat_select_display(page, field_index), variants)


async def _spat_snapshot_row_state(page, pilgrim_index: int) -> dict:
    fields = await _spat_get_field_snapshot(page)
    fmap = _spat_identify_fields(fields, pilgrim_index)

    def _val(key: str) -> str:
        idx = fmap.get(key)
        if idx is None or idx >= len(fields):
            return ""
        return fields[idx].get("value", "")

    return {
        "name": _val("name"),
        "age": _val("age"),
        "gender": _val("gender"),
        "id_proof": _val("id_proof"),
        "id_num": _val("id_num"),
    }


async def _spat_fill_pilgrim_row_snapshot(
    page, run: "TravelRun", index: int, pilgrim: PilgrimDetail
) -> bool:
    """Fill pilgrim row via all visible mat-form-fields (resilient SPAT approach)."""
    name = pilgrim.name.strip().title()
    age = str(pilgrim.age or "30").strip()
    gender = (pilgrim.gender or "Female").strip()
    id_proof = (pilgrim.id_proof or "Aadhaar Card").strip()
    if "aadhaar" in id_proof.lower() or "aadhar" in id_proof.lower():
        id_proof = "Aadhaar Card"
    id_num = re.sub(r"\D", "", pilgrim.aadhaar or "")

    if len(id_num) != 12:
        run._log(
            f"⚠ Pilgrim {index + 1}: Aadhaar must be **12 digits** (got {len(id_num)})"
        )
        return False

    gender_vars = _option_variants("gender", gender)
    proof_vars = _option_variants("id_proof", id_proof)

    await clear_pilgrim_page_alerts(page, run)
    await _mark_pilgrim_row(page, index)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.35)")
    except Exception:
        pass
    await asyncio.sleep(0.3)

    fields = await _spat_get_field_snapshot(page)
    if not fields:
        return False

    run._log(
        f"→ Pilgrim {index + 1}: {len(fields)} fields — "
        f"{[f.get('label', '')[:14] for f in fields[:8]]}"
    )
    fmap = _spat_identify_fields(fields, index)
    run._log(f"→ Pilgrim {index + 1} field map: {fmap}")

    if fmap.get("name") is not None:
        await _spat_fill_text_field_by_index(page, fmap["name"], name, skip_if_set=True)
    await asyncio.sleep(0.15)

    if fmap.get("age") is not None:
        age_ok = await _spat_fill_text_field_by_index(page, fmap["age"], age, skip_if_set=True)
        if not age_ok:
            age_loc = await _find_pilgrim_input(page, r"^age", index, formcontrol_hint="age")
            if age_loc:
                age_ok = await _fill_angular_input_if_needed(age_loc, age)
    await asyncio.sleep(0.15)
    await clear_pilgrim_page_alerts(page, run)

    gender_ok = await _select_pilgrim_dropdown_robust(
        page, index, "gender", gender_vars, run=run
    )
    if not gender_ok and fmap.get("gender") is not None:
        gender_ok = await _spat_open_and_pick_dropdown(page, fmap["gender"], gender_vars)
    if not gender_ok:
        run._log(f"⚠ Pilgrim {index + 1}: Gender not selected")
    await asyncio.sleep(0.25)
    await clear_pilgrim_page_alerts(page, run)

    proof_ok = await _select_pilgrim_dropdown_robust(
        page, index, "idproof", proof_vars, run=run
    )
    if not proof_ok and fmap.get("id_proof") is not None:
        proof_ok = await _spat_open_and_pick_dropdown(page, fmap["id_proof"], proof_vars)
    if not proof_ok:
        run._log(f"⚠ Pilgrim {index + 1}: Photo ID Proof not selected")
    await asyncio.sleep(0.4)
    await clear_pilgrim_page_alerts(page, run)

    await asyncio.sleep(0.3)
    fields2 = await _spat_get_field_snapshot(page)
    fmap2 = _spat_identify_fields(fields2, index)
    id_idx = fmap2.get("id_num") if fmap2.get("id_num") is not None else fmap.get("id_num")

    id_ok = False
    if not gender_ok or not proof_ok:
        run._log(
            f"⚠ Pilgrim {index + 1}: skipping Aadhaar until Gender and Photo ID Proof are set"
        )
    else:
        if id_idx is not None:
            field = await _spat_field_locator(page, id_idx)
            if field:
                inp = field.locator("input:visible:not([type=hidden])").first
                if await inp.count():
                    try:
                        current = (await inp.input_value()).strip()
                        digits = re.sub(r"\D", "", current)
                        if current and digits != id_num and len(digits) != 12:
                            await inp.click(click_count=3)
                            await inp.press("Backspace")
                    except Exception:
                        pass
            for _ in range(4):
                id_ok = await _spat_fill_text_field_by_index(
                    page, id_idx, id_num, skip_if_set=True
                )
                if id_ok:
                    break
                field = await _spat_field_locator(page, id_idx)
                if field:
                    inp = field.locator("input:visible:not([type=hidden])").first
                    if await inp.count():
                        try:
                            await inp.scroll_into_view_if_needed()
                            await inp.click(click_count=3)
                            await inp.press("Backspace")
                            await inp.press_sequentially(id_num, delay=55)
                            typed = re.sub(r"\D", "", await inp.input_value())
                            if typed == id_num:
                                id_ok = True
                                break
                        except Exception:
                            pass
                await clear_pilgrim_page_alerts(page, run)
                await asyncio.sleep(0.4)
        if not id_ok:
            await _fill_pilgrim_id_number(page, f"p-{index}", index, id_num, run)
            state_id = await _spat_snapshot_row_state(page, index)
            id_ok = re.sub(r"\D", "", state_id.get("id_num") or "") == id_num
        if not id_ok:
            run._log(f"⚠ Pilgrim {index + 1}: Photo ID Number not filled")

    await asyncio.sleep(0.3)
    state = await _spat_snapshot_row_state(page, index)
    checks = _row_values_match(
        {
            "name": state.get("name", ""),
            "age": state.get("age", ""),
            "gender": state.get("gender", ""),
            "id_proof": state.get("id_proof", ""),
            "id_num": state.get("id_num", ""),
        },
        name,
        age,
        gender,
        id_proof,
        id_num,
    )
    run._log(
        f"→ Pilgrim {index + 1} state: "
        f"name={'✓' if checks['name'] else '✗'} "
        f"age={'✓' if checks['age'] else '✗'} "
        f"gender={'✓' if checks['gender'] else '✗'}({state.get('gender', '')[:12]}) "
        f"proof={'✓' if checks['id_proof'] else '✗'}({state.get('id_proof', '')[:12]}) "
        f"id#={'✓' if checks['id_num'] else '✗'}"
    )
    return all(checks.values())


async def _spat_fill_pilgrim_row_legacy(
    page, run: "TravelRun", index: int, pilgrim: PilgrimDetail
) -> bool:
    """Legacy mark/label fallback when snapshot field discovery fails."""
    name = pilgrim.name.strip().title()
    age = str(pilgrim.age or "30").strip()
    gender = (pilgrim.gender or "Female").strip()
    id_proof = (pilgrim.id_proof or "Aadhaar Card").strip()
    if "aadhaar" in id_proof.lower() or "aadhar" in id_proof.lower():
        id_proof = "Aadhaar Card"
    id_num = re.sub(r"\D", "", pilgrim.aadhaar or "")

    gender_vars = _option_variants("gender", gender)
    proof_vars = _option_variants("id_proof", id_proof)

    marked = await _mark_pilgrim_row(page, index)
    row_state = await _read_pilgrim_row_state(page, index)
    if marked < 3:
        run._log(
            f"⚠ Pilgrim {index + 1}: only {marked} fields marked "
            f"(mat_fields={row_state.get('mat_fields', '?')}, "
            f"label_fields={row_state.get('label_fields', '?')}) — using position fallback"
        )

    name_loc = await _find_pilgrim_input(page, r"^name", index, formcontrol_hint="name")
    if name_loc:
        await _fill_angular_input_if_needed(name_loc, name)
    elif not await _fill_marked_input(page, index, "name", name):
        await _ttd_fill_input_by_label(page, r"^name", name, index)
    await asyncio.sleep(0.12)

    age_loc = await _find_pilgrim_input(page, r"^age", index, formcontrol_hint="age")
    if age_loc:
        await _fill_angular_input_if_needed(age_loc, age)
    elif not await _fill_marked_input(page, index, "age", age):
        await _ttd_fill_input_by_label(page, r"^age", age, index)
    await asyncio.sleep(0.12)
    await clear_pilgrim_page_alerts(page, run)

    gender_ok = await _select_pilgrim_dropdown_robust(
        page, index, "gender", gender_vars, run=run
    )
    proof_ok = await _select_pilgrim_dropdown_robust(
        page, index, "idproof", proof_vars, run=run
    )

    await _mark_pilgrim_row(page, index)
    id_ok = False
    for _ in range(4):
        id_ok = await _fill_marked_input(page, index, "idnum", id_num)
        if id_ok:
            break
        id_ok = await _ttd_fill_input_by_label(
            page, r"photo\s*id\s*number|id\s*number|aadhaar", id_num, index
        )
        if id_ok:
            break
        await clear_pilgrim_page_alerts(page, run)
        await asyncio.sleep(0.35)

    state = await _read_pilgrim_row_state(page, index)
    if not state.get("name"):
        loc_state = await _read_pilgrim_locator_state(page, index)
        ttd_state = await _read_pilgrim_ttd_state(page, index)
        for key in ("name", "age", "gender", "id_proof", "id_num"):
            if not (state.get(key) or "").strip():
                state[key] = loc_state.get(key) or ttd_state.get(key) or ""

    checks = _row_values_match(
        {
            "name": state.get("name", ""),
            "age": state.get("age", ""),
            "gender": state.get("gender", ""),
            "id_proof": state.get("id_proof", ""),
            "id_num": state.get("id_num", ""),
        },
        name,
        age,
        gender,
        id_proof,
        id_num,
    )
    return all(checks.values())


async def _spat_fill_pilgrim_row(
    page, run: "TravelRun", index: int, pilgrim: PilgrimDetail
) -> bool:
    """
    Fill one pilgrim row in order:
    Name → Age → Gender → Photo ID Proof → Photo ID Number.
    """
    name = pilgrim.name.strip().title()
    age = str(pilgrim.age or "30").strip()
    gender = (pilgrim.gender or "Female").strip()

    run._log(f"→ Filling pilgrim {index + 1}: {name[:20]}, {age}y, {gender}")
    await _scroll_pilgrim_form_into_view(page, index)
    await clear_pilgrim_page_alerts(page, run)

    ok = await _spat_fill_pilgrim_row_snapshot(page, run, index, pilgrim)
    if ok:
        return True

    run._log(f"→ Pilgrim {index + 1}: snapshot fill incomplete — trying IRCTC-style fill")
    id_proof = (pilgrim.id_proof or "Aadhaar Card").strip()
    if "aadhaar" in id_proof.lower() or "aadhar" in id_proof.lower():
        id_proof = "Aadhaar Card"
    id_num = re.sub(r"\D", "", pilgrim.aadhaar or "")
    gender_vars = _option_variants("gender", gender)
    proof_vars = _option_variants("id_proof", id_proof)
    if await _fill_pilgrim_row_irctc(
        page,
        run,
        index,
        name,
        age,
        gender,
        id_proof,
        id_num,
        gender_vars,
        proof_vars,
    ):
        return True

    run._log(f"→ Pilgrim {index + 1}: IRCTC fill incomplete — trying label fallback")
    return await _spat_fill_pilgrim_row_legacy(page, run, index, pilgrim)


async def _fill_pilgrim_row(page, run: "TravelRun", index: int, pilgrim: PilgrimDetail) -> bool:
    name = pilgrim.name.strip().title()
    id_num = re.sub(r"\D", "", pilgrim.aadhaar or "")

    if len(id_num) != 12:
        run._log(
            f"⚠ Pilgrim {index + 1}: Aadhaar must be **12 digits** (got {len(id_num)}). "
            "Fix in Streamlit and resubmit."
        )
        return False

    for attempt in range(2):
        ok = await _spat_fill_pilgrim_row(page, run, index, pilgrim)
        if ok:
            return True
        if attempt < 1:
            run._log(f"→ Pilgrim {index + 1} retry {attempt + 2}/2")
            await clear_pilgrim_page_alerts(page, run)
        await asyncio.sleep(0.5)

    return False


async def fill_pilgrims(
    page,
    run: "TravelRun",
    pilgrims: list[PilgrimDetail],
) -> int:
    """Fill TTD SPAT Pilgrim Details: Name, Age, Gender, Photo ID Proof, Photo ID Number."""
    if not pilgrims:
        return 0

    try:
        if page.is_closed():
            run._log("❌ Chrome tab closed — cannot fill pilgrim details")
            return 0
    except Exception:
        pass

    await clear_pilgrim_page_alerts(page, run)
    await _wait_until(page, _ttd_pilgrim_screen_visible, timeout=12.0)
    try:
        await page.evaluate(
            f"""
            () => {{
                {_pilgrim_page_js()}
                pilgrimRoot().scrollIntoView({{ block: 'start', inline: 'nearest' }});
            }}
            """
        )
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.25)")
    except Exception as exc:
        run._log(f"⚠ Pilgrim page scroll failed: {exc}")
        return 0
    await _scroll_pilgrim_form_into_view(page, 0)
    await asyncio.sleep(0.6)

    filled = 0
    for i, pilgrim in enumerate(pilgrims):
        try:
            if page.is_closed():
                run._log("❌ Chrome tab closed during pilgrim fill")
                break

            await _scroll_pilgrim_form_into_view(page, i)
            await clear_pilgrim_page_alerts(page, run)

            if await _fill_pilgrim_row(page, run, i, pilgrim):
                filled += 1
            else:
                run._log(
                    f"⚠ Pilgrim {i + 1}: partial fill — complete Name/Age/Gender/"
                    "Photo ID Proof/ID Number manually in Chrome"
                )
        except Exception as exc:
            run._log(f"⚠ Pilgrim {i + 1} fill error: {exc}")
        await asyncio.sleep(0.5)

    if filled:
        run._log(
            f"→ {filled}/{len(pilgrims)} pilgrim(s) filled — "
            "verify in Chrome then click **Continue**"
        )
    else:
        run._log(
            "⚠ Auto-fill failed — fill Name/Age/Gender/Photo ID Proof/ID Number "
            "in Chrome before clicking Continue"
        )
    return filled


async def _ttd_payment_screen_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const t = (document.body.innerText || '').slice(0, 16000).toLowerCase();
                    if (/pay now|make payment|payment summary|booking amount|total payable|amount payable/i.test(t))
                        return true;
                    for (const btn of document.querySelectorAll('button, a, [role="button"]')) {
                        if (btn.offsetParent === null) continue;
                        const label = (btn.innerText || btn.textContent || '').trim();
                        if (/^pay now$/i.test(label)) return true;
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _pilgrim_continue_reached_next(page) -> bool:
    if await _ttd_payment_screen_visible(page):
        return True
    if await _ttd_slot_screen_visible(page):
        return False
    return not await _ttd_pilgrim_screen_visible(page)


async def _read_pilgrim_state_combined(page, index: int = 0) -> dict:
    """Merge pilgrim field values from every DOM read strategy."""
    state: dict[str, str] = {
        "name": "",
        "age": "",
        "gender": "",
        "id_proof": "",
        "id_num": "",
    }
    sources: list[dict] = []

    for coro in (
        _read_pilgrim_ttd_state(page, index),
        _read_pilgrim_row_state(page, index),
        _spat_snapshot_row_state(page, index),
        _read_pilgrim_locator_state(page, index),
    ):
        try:
            src = await coro
            if src:
                sources.append(src)
        except Exception:
            pass

    try:
        pos = await page.evaluate(
            f"""
            (idx) => {{
                {_pilgrim_page_js()}
                return pilgrimPositionState(idx);
            }}
            """,
            index,
        )
        if pos:
            sources.append(pos)
    except Exception:
        pass

    for key in state:
        for src in sources:
            val = (src.get(key) or "").strip()
            if val:
                state[key] = val
                break
    return state


def _pilgrim_state_ready(state: dict) -> tuple[bool, list[str]]:
    """Return (ready, list of missing field keys)."""
    missing: list[str] = []
    if not (state.get("name") or "").strip():
        missing.append("name")
    if not (state.get("age") or "").strip():
        missing.append("age")
    if not (state.get("id_num") or "").strip():
        missing.append("id_num")
    g = (state.get("gender") or "").lower()
    if not g or re.search(r"select|choose|^gender$", g):
        missing.append("gender")
    p = (state.get("id_proof") or "").lower()
    if not p or re.search(r"select|choose|^photo", p):
        missing.append("id_proof")
    return (not missing, missing)


async def click_pilgrim_continue(
    page, run: "TravelRun", *, trust_human: bool = False
) -> bool:
    """Click **Continue** only after pilgrim Name/Age/Gender/ID are filled (→ payment)."""
    if await _ttd_slot_screen_visible(page):
        run._log("⚠ Still on slot page — cannot use pilgrim Continue")
        return False
    if not await _ttd_pilgrim_screen_visible(page):
        if await _ttd_payment_screen_visible(page):
            run._log("→ Already on payment page (**Pay Now**)")
            return True
        run._log("⚠ Pilgrim page not detected — cannot click Continue")
        return False

    await clear_pilgrim_page_alerts(page, run)
    if await _pilgrim_has_validation_errors(page):
        run._log("⚠ Pilgrim validation errors — fix fields before Continue")
        return False

    state = await _read_pilgrim_state_combined(page, 0)
    ready, missing = _pilgrim_state_ready(state)
    if not ready and not trust_human:
        run._log(
            f"⚠ Pilgrim row incomplete — cannot Continue yet "
            f"(missing: {', '.join(missing)}; "
            f"name={state.get('name', '')[:12]!r} age={state.get('age', '')!r} "
            f"gender={state.get('gender', '')[:14]!r} proof={state.get('id_proof', '')[:14]!r} "
            f"id#={'***' if state.get('id_num') else ''})"
        )
        return False
    if not ready and trust_human:
        run._log(
            "→ Human confirmed pilgrim details — clicking **Continue** "
            f"(agent read: missing {', '.join(missing) or 'none'})"
        )
    elif ready:
        run._log(
            f"→ Pilgrim row verified: name={state.get('name', '')[:16]!r} "
            f"age={state.get('age', '')!r} gender={state.get('gender', '')[:12]!r}"
        )

    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.45)
    except Exception:
        pass

    click_info = await page.evaluate(
        f"""
        () => {{
            {_pilgrim_page_js()}
            const isEnabled = (btn) => {{
                if (!btn || btn.offsetParent === null) return false;
                if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
                return true;
            }};
            const label = (btn) => (btn.innerText || btn.textContent || '').trim();
            const root = pilgrimRoot();
            const buttons = [...document.querySelectorAll('button, a[role="button"], [role="button"]')];
            const continues = buttons.filter((btn) => isEnabled(btn) && /^continue$/i.test(label(btn)));
            let best = null;
            let bestScore = -1;
            const rootBottom = root.getBoundingClientRect().bottom;
            for (const btn of continues) {{
                let score = btn.getBoundingClientRect().top;
                if (root.contains(btn)) score += 10000;
                if (btn.getBoundingClientRect().top >= rootBottom - 40) score += 5000;
                if (score > bestScore) {{
                    bestScore = score;
                    best = btn;
                }}
            }}
            if (!best && continues.length) best = continues[continues.length - 1];
            if (!best) return null;
            best.scrollIntoView({{ block: 'center', inline: 'nearest' }});
            const r = best.getBoundingClientRect();
            return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
        }}
        """
    )

    if click_info:
        try:
            await page.mouse.click(float(click_info["x"]), float(click_info["y"]))
            run._log("→ Clicked **Continue** on pilgrim page")
            await asyncio.sleep(1.2)
            if await _dismiss_ttd_validation_alert(page, run):
                run._log("⚠ Continue blocked — complete pilgrim fields first")
                return False
            if await _wait_until(page, _pilgrim_continue_reached_next, timeout=12.0):
                if await _ttd_payment_screen_visible(page):
                    run._log("→ Reached payment page — **Pay Now** in Chrome")
                return True
            run._log("→ Continue clicked — waiting for payment page…")
        except Exception:
            pass

    try:
        btn = page.get_by_role("button", name=re.compile(r"^Continue$", re.I)).last
        if await btn.is_visible() and not await btn.is_disabled():
            await btn.scroll_into_view_if_needed()
            await btn.click()
            run._log("→ Clicked Continue on pilgrim page (Playwright)")
            await asyncio.sleep(1.2)
            if await _dismiss_ttd_validation_alert(page, run):
                return False
            return await _wait_until(page, _pilgrim_continue_reached_next, timeout=12.0)
    except Exception:
        pass

    run._log("⚠ Could not click **Continue** on pilgrim page — click it manually in Chrome")
    return False


async def continue_after_pilgrims(
    page, run: "TravelRun", *, trust_human: bool = False
) -> bool:
    """Click **Continue** on pilgrim details → payment page with **Pay Now**."""
    if await _ttd_payment_screen_visible(page):
        run._log("→ Already on payment page (**Pay Now**)")
        return True

    if await _ttd_slot_screen_visible(page):
        run._log("⚠ Still on slot page — select a time slot before continuing")
        return False

    if await click_pilgrim_continue(page, run, trust_human=trust_human):
        return True

    await _dismiss_ttd_validation_alert(page, run)
    if await _pilgrim_has_validation_errors(page):
        run._log("⚠ Pilgrim form has validation errors — fix Gender / Photo ID / ID number first")
        return False

    return False


async def _ttd_payment_gateway_visible(page) -> bool:
    """True when Pay Now opened UPI/card/netbanking gateway."""
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const t = (document.body.innerText || '').slice(0, 20000).toLowerCase();
                    if (/upi id|net\s*banking|debit card|credit card|payment gateway|pay via|qr code/i.test(t))
                        return true;
                    const url = (location.href || '').toLowerCase();
                    return /payment|checkout|gateway|razorpay|payu|billdesk|ccavenue/i.test(url);
                }
                """
            )
        )
    except Exception:
        return False


async def click_pay_now(page, run: "TravelRun") -> bool:
    """Click **Pay Now** on the TTD payment summary page."""
    if await _ttd_payment_gateway_visible(page):
        run._log("→ Payment gateway already open in Chrome")
        return True

    if not await _ttd_payment_screen_visible(page):
        run._log("⚠ Payment summary not detected — cannot click **Pay Now**")
        return False

    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.45)
    except Exception:
        pass

    click_info = await page.evaluate(
        """
        () => {
            const isEnabled = (btn) => {
                if (!btn || btn.offsetParent === null) return false;
                if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
                const cls = (btn.className || '').toString();
                if (/disabled|mat-button-disabled/i.test(cls)) return false;
                return true;
            };
            const label = (btn) => (btn.innerText || btn.textContent || '').replace(/\\s+/g, ' ').trim();
            const buttons = [...document.querySelectorAll('button, a, [role="button"]')];
            const score = (btn) => {
                const t = label(btn).toLowerCase();
                if (/^pay\\s*now$/i.test(t)) return 100;
                if (/^pay$/i.test(t)) return 80;
                if (/make payment|proceed to pay/i.test(t)) return 70;
                return 0;
            };
            let best = null;
            let bestScore = 0;
            for (const btn of buttons) {
                if (!isEnabled(btn)) continue;
                const s = score(btn);
                if (s <= 0) continue;
                const top = btn.getBoundingClientRect().top;
                const combined = s * 1000 + top;
                if (combined > bestScore) {
                    bestScore = combined;
                    best = btn;
                }
            }
            if (!best) return null;
            best.scrollIntoView({ block: 'center', inline: 'nearest' });
            const r = best.getBoundingClientRect();
            return {
                x: r.x + r.width / 2,
                y: r.y + r.height / 2,
                text: label(best),
            };
        }
        """
    )

    if click_info:
        try:
            await page.mouse.click(float(click_info["x"]), float(click_info["y"]))
            run._log(f"→ Clicked **{click_info.get('text', 'Pay Now')}** in Chrome")
            await asyncio.sleep(1.5)
            if await _ttd_payment_gateway_visible(page):
                run._log("→ Payment gateway opened — complete UPI/card in Chrome")
            return True
        except Exception:
            pass

    for btn_label in ("Pay Now", "Pay", "Make Payment"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(btn_label)}$", re.I)).last
            if await btn.is_visible() and not await btn.is_disabled():
                await btn.scroll_into_view_if_needed()
                await btn.click()
                run._log(f"→ Clicked **{btn_label}** in Chrome (Playwright)")
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    if await click_first(
        page,
        (
            'button:has-text("Pay Now")',
            'a:has-text("Pay Now")',
            'button:has-text("Make Payment")',
            'button:has-text("Pay")',
        ),
    ):
        run._log("→ Clicked **Pay Now** in Chrome")
        await asyncio.sleep(1.5)
        return True

    run._log("⚠ Could not click **Pay Now** — click it manually in Chrome")
    return False


async def proceed_to_payment(page, run: "TravelRun") -> bool:
    """Ensure we are on the TTD payment summary page (**Pay Now** — user pays in Chrome)."""
    if await _ttd_payment_screen_visible(page):
        run._log("→ Payment page ready — confirm amount then **Pay Now**")
        return True

    if await _ttd_slot_screen_visible(page):
        run._log("⚠ On slot page — cannot proceed to payment without selecting a slot")
        return False

    if await _ttd_pilgrim_screen_visible(page):
        run._log("→ Pilgrim page — clicking **Continue** to open payment")
        if await click_pilgrim_continue(page, run):
            await asyncio.sleep(0.8)

    if await _ttd_payment_screen_visible(page):
        run._log("→ Payment page ready — confirm amount then **Pay Now**")
        return True

    for label in ("Pay Now", "Pay", "Make Payment", "Proceed"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
            if await btn.is_visible() and not await btn.is_disabled():
                run._log(f"→ Found **{label}** on page (complete payment in Chrome)")
                return True
        except Exception:
            continue

    if await click_first(
        page,
        (
            'button:has-text("Pay Now")',
            'button:has-text("Pay")',
            'button:has-text("Make Payment")',
        ),
    ):
        run._log("→ Payment button visible in Chrome")
        await asyncio.sleep(0.5)
        return True

    run._log("⚠ Payment page not detected — look for **Pay Now** in Chrome")
    return False


async def dump_spat_dom(page) -> dict:
    """Dump SPAT calendar cells and pilgrim form fields (for selector tuning)."""
    result = await page.evaluate(
        """
        () => {
            const out = {
                url: location.href,
                calendar_cells: [],
                mat_labels: [],
                visible_inputs: [],
                mat_selects: [],
                body_snippet: (document.body.innerText || '').slice(0, 800),
            };

            for (const el of document.querySelectorAll(
                'button, td, [role="gridcell"], [role="button"], .mat-calendar-body-cell-content, div, span'
            )) {
                const t = (el.innerText || el.textContent || '').trim();
                if (!/^\\d{1,2}$/.test(t) || parseInt(t) < 1 || parseInt(t) > 31) continue;
                if (!el.offsetParent) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.width > 90) continue;
                out.calendar_cells.push({
                    tag: el.tagName,
                    text: t,
                    class: (el.className || '').slice(0, 120),
                    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                    role: el.getAttribute('role') || '',
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                    top: Math.round(r.top),
                });
            }

            for (const lab of document.querySelectorAll(
                'mat-label, label, .mat-mdc-floating-label, .mdc-floating-label'
            )) {
                const t = (lab.innerText || lab.textContent || '').replace(/\\*/g,'').trim();
                if (!t || t.length > 60) continue;
                const mf = lab.closest('mat-form-field, .mat-mdc-form-field');
                const hasSelect = !!(mf && mf.querySelector('mat-select, select'));
                const hasInput = !!(mf && mf.querySelector('input:not([type=hidden])'));
                out.mat_labels.push({ label: t, hasSelect, hasInput });
            }

            for (const inp of document.querySelectorAll('input')) {
                if (inp.type === 'hidden' || inp.disabled || !inp.offsetParent) continue;
                const r = inp.getBoundingClientRect();
                if (r.width < 10) continue;
                const lab = inp.closest('mat-form-field, .mat-mdc-form-field')
                    ?.querySelector('mat-label, label, .mat-mdc-floating-label');
                out.visible_inputs.push({
                    type: inp.type || 'text',
                    placeholder: inp.placeholder || '',
                    name: inp.name || inp.getAttribute('formcontrolname') || '',
                    label: (lab?.innerText || '').replace(/\\*/g,'').trim(),
                    value: inp.value || '',
                });
            }

            for (const sel of document.querySelectorAll('mat-select, select')) {
                if (!sel.offsetParent) continue;
                const lab = sel.closest('mat-form-field, .mat-mdc-form-field')
                    ?.querySelector('mat-label, label, .mat-mdc-floating-label');
                const display = sel.querySelector(
                    '.mat-mdc-select-value-text, .mat-select-value-text'
                );
                out.mat_selects.push({
                    tag: sel.tagName,
                    label: (lab?.innerText || '').replace(/\\*/g,'').trim(),
                    display: (display?.innerText || '').trim(),
                    name: sel.name || sel.getAttribute('formcontrolname') || '',
                });
            }

            return out;
        }
        """
    )

    print("\n" + "=" * 60)
    print("TTD SPAT DOM Diagnostic")
    print("=" * 60)
    print(f"URL: {result['url']}")
    print(f"\nBody snippet:\n{result['body_snippet'][:400]}")
    print(f"\n── Calendar cells ({len(result['calendar_cells'])}) ──")
    for cell in result["calendar_cells"][:20]:
        print(
            f"  day={cell['text']:>2}  tag={cell['tag']:<8}  "
            f"disabled={cell['disabled']}  w={cell['w']}  "
            f"class={cell['class'][:60]}"
        )
    print(f"\n── mat-form-field labels ({len(result['mat_labels'])}) ──")
    for lab in result["mat_labels"]:
        print(f"  {lab['label']:<30}  select={lab['hasSelect']}  input={lab['hasInput']}")
    print(f"\n── Visible inputs ({len(result['visible_inputs'])}) ──")
    for inp in result["visible_inputs"]:
        print(
            f"  label={inp['label']:<20}  name={inp['name']:<20}  "
            f"placeholder={inp['placeholder']:<20}  value={inp['value'][:20]!r}"
        )
    print(f"\n── mat-selects ({len(result['mat_selects'])}) ──")
    for sel in result["mat_selects"]:
        print(
            f"  label={sel['label']:<20}  display={sel['display']:<20}  "
            f"name={sel['name']:<20}"
        )
    print("=" * 60 + "\n")
    return result
