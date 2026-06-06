"""Shared Playwright helpers for scripted (no-LLM) flows."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from agent.human_prompts import (
    TAG_CAPTCHA,
    TAG_CONFIRM_DONE,
    TAG_LOGIN_FORM,
    TAG_OTP,
    TAG_PAYMENT_CONFIRM,
    TAG_TEXT,
)

if TYPE_CHECKING:
    from agent.playwright_engine import PlaywrightEngine
    from agent.travel_runner import TravelRun

MATH_CAPTCHA_RE = re.compile(r"(\d+)\s*([+\-*/x×])\s*(\d+)", re.I)
_REDBUS_STEP_PARAM = re.compile(r"[?&]step=([A-Za-z]+)", re.I)

# RedBus SPA checkout uses ?step= on bus-tickets URLs (e.g. step=BPDP for board/drop).
_REDBUS_URL_STEP_MAP = {
    "BPDP": "board_drop",
    "BP": "board_drop",
    "BOARD": "board_drop",
    "SEAT": "seats",
    "SEATS": "seats",
    "SL": "seats",
    "CUST": "passenger",
    "CUSTINFO": "passenger",
    "PI": "passenger",
    "PASSENGER": "passenger",
    "PASSENGERINFO": "passenger",
}


def _redbus_url_step(url: str) -> str:
    """Booking step from RedBus ?step= query param (empty if absent)."""
    m = _REDBUS_STEP_PARAM.search(url or "")
    if not m:
        return ""
    key = m.group(1).upper()
    return _REDBUS_URL_STEP_MAP.get(key, key.lower())


_REDBUS_LIVE_BPDP_JS = """
() => {
    const url = location.href.toLowerCase();
    if (/[?&]step=bpdp/i.test(url)) return true;
    let left = 0, right = 0;
    const mid = window.innerWidth * 0.42;
    const vh = window.innerHeight;
    for (const r of document.querySelectorAll('input[type="radio"], [role="radio"]')) {
        const rect = r.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1 || rect.y < 100 || rect.y > vh * 0.9) continue;
        const t = (r.closest('label, li, div')?.innerText || r.getAttribute('aria-label') || '')
            .replace(/\\s+/g, ' ').trim();
        if (t.length < 8 || t.length > 160) continue;
        const tl = t.toLowerCase();
        if (/primo bus|bus duration|bus service|avg\\.|view price|coimbatore to bangalore bus/i.test(tl)) continue;
        // Times are optional on some RedBus renders (name-only boarding points).
        // Treat "two columns of radio-ish rows" as BPDP even without time tokens.
        if (rect.x < mid) left++;
        else right++;
    }
    return left >= 1 && right >= 1;
}
"""


async def _redbus_has_live_board_drop(page) -> bool:
    try:
        return bool(await page.evaluate(_REDBUS_LIVE_BPDP_JS))
    except Exception:
        return False


def solve_math(a: int, op: str, b: int) -> Optional[int]:
    op = op.lower().replace("×", "x")
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op in ("x", "*"):
        return a * b
    if op == "/" and b != 0:
        return a // b
    return None


async def screenshot(engine: "PlaywrightEngine", run: "TravelRun") -> None:
    """Capture live view without scanning DOM (avoids site-specific JS errors)."""
    try:
        run.screenshot = await engine.get_screenshot_b64() or run.screenshot
    except Exception:
        try:
            obs = await engine.observe()
            run.screenshot = obs.get("screenshot_b64", "") or run.screenshot
        except Exception:
            pass


def _cert_hint(error: str) -> str:
    if "ERR_CERT" not in error:
        return ""
    return (
        " Tip: verify Windows date/time is set correctly; "
        "keep PLAYWRIGHT_IGNORE_HTTPS_ERRORS=true in .env and restart Streamlit."
    )


async def open_url(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    url: str,
    *,
    label: str = "",
) -> bool:
    run._log(f"🌐 Opening {label or url[:70]}")
    nav = await engine.navigate(url)
    if nav.get("success"):
        run._log("✅ Page loaded")
        run._phase("open_portal")
        if "redbus" in url.lower():
            try:
                await engine.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(1.5)
        else:
            await asyncio.sleep(2)
        await screenshot(engine, run)
        return True
    err = nav.get("error", "")[:160]
    run._log(f"❌ Navigate failed: {err}{_cert_hint(err)}")
    return False


async def open_url_with_fallbacks(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    urls: list[str],
    *,
    label: str = "",
) -> bool:
    """Try each URL until one loads (helps with www / cert quirks)."""
    seen: set[str] = set()
    for url in urls:
        u = (url or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        if await open_url(engine, run, u, label=label or u[:50]):
            return True
    run.error = run.error or "Could not open portal — try opening the site manually in Chrome."
    return False


async def click_text(page, pattern: str, timeout: float = 8000) -> bool:
    try:
        await page.get_by_text(re.compile(pattern, re.I)).first.click(timeout=timeout)
        return True
    except Exception:
        return False


async def fill_first(page, selectors: tuple[str, ...], value: str) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                await loc.fill(value)
                return True
        except Exception:
            continue
    return False


async def click_first(page, selectors: tuple[str, ...]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                return True
        except Exception:
            continue
    return False


async def ask_image_captcha(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    wait_human: Callable[[str], Awaitable[str]],
    *,
    message: str = "Enter the **CAPTCHA** exactly as shown in the browser (image next to the field):",
    img_selectors: tuple[str, ...] = (
        "#CaptchaImgID",
        'img[src*="simpleCaptcha" i]',
        'img[src*="captcha" i]',
    ),
) -> str:
    """Screenshot page (and captcha image when possible), then ask user via [CAPTCHA] tag."""
    page = engine.page
    for sel in img_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                png = await loc.screenshot(type="png")
                if png:
                    import base64

                    run.screenshot = base64.b64encode(png).decode("ascii")
                    run._log("→ CAPTCHA image captured — enter it in the form below")
                    break
        except Exception:
            continue
    if not run.screenshot:
        await screenshot(engine, run)
    return await ask_tagged(wait_human, TAG_CAPTCHA, message)


async def read_math_captcha(page) -> Optional[str]:
    text = await page.evaluate(
        """() => {
        for (const el of document.querySelectorAll('label, span, div, .captcha-question')) {
            const t = (el.innerText || '').trim();
            if (/\\d\\s*[+\\-*/x×]\\s*\\d/.test(t)) return t;
        }
        return document.body.innerText.slice(0, 6000);
    }"""
    )
    m = MATH_CAPTCHA_RE.search(text or "")
    if not m:
        return None
    ans = solve_math(int(m.group(1)), m.group(2), int(m.group(3)))
    return str(ans) if ans is not None else None


async def ask(
    wait_human: Callable[[str], Awaitable[str]],
    prompt: str,
    prefilled: str = "",
) -> str:
    if prefilled and not str(prefilled).upper().startswith("ASK_USER"):
        return str(prefilled).strip()
    return (await wait_human(prompt)).strip()


async def ask_tagged(
    wait_human: Callable[[str], Awaitable[str]],
    tag: str,
    message: str,
    param: str = "",
    prefilled: str = "",
) -> str:
    from agent.human_prompts import format_prompt

    if prefilled and not str(prefilled).upper().startswith("ASK_USER"):
        return str(prefilled).strip()
    return await ask(wait_human, format_prompt(tag, message, param))


async def dismiss_cookie_banner(page) -> None:
    for sel in (
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        'button:has-text("Agree")',
        'button:has-text("Got it")',
        "#onetrust-accept-btn-handler",
    ):
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    for pattern in (r"accept|agree|got it|allow all|i understand",):
        if await click_text(page, pattern, timeout=1500):
            await asyncio.sleep(0.4)
            return


def _is_redbus(portal_url: str) -> bool:
    return "redbus" in (portal_url or "").lower()


def _is_tnstc(portal_url: str) -> bool:
    return "tnstc" in (portal_url or "").lower()


async def dismiss_tnstc_modals(page) -> None:
    """Close SETC promo / login overlays on TNSTC OTRS."""
    await dismiss_cookie_banner(page)
    for sel in (
        'button:has-text("Close")',
        ".modal .close",
        '[data-dismiss="modal"]',
        "button.btn-close",
    ):
        try:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible(timeout=1200):
                await btn.click(timeout=2000)
                await asyncio.sleep(0.4)
        except Exception:
            continue


def _tnstc_score_place(needle: str, place_name: str) -> int:
    """Rank TNSTC place API results against user input from Streamlit."""
    n = (needle or "").strip().lower()
    p = (place_name or "").strip().lower()
    if not n or not p:
        return 0
    if p == n:
        score = 100
    elif p.startswith(n):
        score = 90
    elif re.search(rf"\b{re.escape(n)}\b", p):
        score = 82
    elif n in p:
        score = 72
    elif p in n:
        score = 55
    elif n[:4] and n[:4] in p:
        score = 35
    else:
        score = 0
    if "chennai" in n:
        if "kilambakkam" in p or "kcbt" in p:
            score = max(score, 88)
        elif p == "chennai":
            score = min(score, 70)
    return score


async def _tnstc_fetch_places(page, city: str, *, is_from: bool) -> list[tuple[str, str, str]]:
    """Call TNSTC LoadFromPlaceList / LoadTOPlaceList (same as site autocomplete)."""
    city = (city or "").strip()
    if not city:
        return []
    rows = await page.evaluate(
        """
        async ([city, isFrom]) => {
            const action = isFrom ? 'LoadFromPlaceList' : 'LoadTOPlaceList';
            const param = isFrom ? 'matchStartPlace' : 'matchEndPlace';
            const body = `hiddenAction=${action}&${param}=${encodeURIComponent(city)}`;
            const res = await fetch('jqreq.do', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body,
                credentials: 'same-origin',
            });
            const raw = await res.text();
            const out = [];
            for (const chunk of raw.split('^')) {
                if (!chunk.trim()) continue;
                const parts = chunk.split(':');
                if (parts.length < 3) continue;
                out.push([parts[0], parts[1], parts.slice(2).join(':')]);
            }
            return out;
        }
        """,
        [city, is_from],
    )
    return [(str(a), str(b), str(c)) for a, b, c in (rows or [])]


async def _tnstc_apply_place(
    page,
    *,
    is_from: bool,
    place_id: str,
    place_code: str,
    place_name: str,
) -> None:
    """
    Set TNSTC place IDs + visible Source/Destination fields.

    Must not use Playwright .fill() — jQuery autocomplete 'change' clears IDs when
    the value was not picked from the dropdown (ui.item is null).
    """
    await page.evaluate(
        """
        ([isFrom, pid, pcode, pname]) => {
            const setNative = (el, v) => {
                if (!el) return;
                const desc = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                );
                if (desc && desc.set) desc.set.call(el, v);
                else el.value = v;
            };
            const vis = document.querySelector(
                isFrom ? 'input#matchStartPlace[type="text"]' : 'input#matchEndPlace[type="text"]'
            );
            setNative(vis, pname);
            const form = document.forms['advanceBookingActionForm'] || document.forms[0];
            if (!form) return;
            if (isFrom) {
                window.fromPlaceID = pid;
                window.fromPlaceCode = pcode;
                setNative(form.matchStartPlace, pname);
                if (form.selectStartPlace) form.selectStartPlace.value = pcode;
                if (form.hiddenStartPlaceID) form.hiddenStartPlaceID.value = pid;
                if (form.hiddenStartPlaceName) form.hiddenStartPlaceName.value = pname;
                if (form.txtStartPlaceCode) form.txtStartPlaceCode.value = pcode;
            } else {
                window.toPlaceID = pid;
                window.toPlaceCode = pcode;
                setNative(form.matchEndPlace, pname);
                if (form.selectEndPlace) form.selectEndPlace.value = pcode;
                if (form.hiddenEndPlaceID) form.hiddenEndPlaceID.value = pid;
                if (form.hiddenEndPlaceName) form.hiddenEndPlaceName.value = pname;
                if (form.txtEndPlaceCode) form.txtEndPlaceCode.value = pcode;
            }
        }
        """,
        [is_from, place_id, place_code, place_name],
    )


async def _tnstc_places_ready(page) -> dict:
    """Check visible + hidden TNSTC route fields (setSearchAction requires these)."""
    return await page.evaluate(
        """
        () => {
            const visFrom = document.querySelector('input#matchStartPlace[type="text"]');
            const visTo = document.querySelector('input#matchEndPlace[type="text"]');
            const form = document.forms['advanceBookingActionForm'] || document.forms[0];
            return {
                fromVisible: (visFrom && visFrom.value) || '',
                toVisible: (visTo && visTo.value) || '',
                fromCode: (form && form.selectStartPlace && form.selectStartPlace.value) || '',
                toCode: (form && form.selectEndPlace && form.selectEndPlace.value) || '',
                fromId: String(window.fromPlaceID || (form && form.hiddenStartPlaceID && form.hiddenStartPlaceID.value) || ''),
                toId: String(window.toPlaceID || (form && form.hiddenEndPlaceID && form.hiddenEndPlaceID.value) || ''),
            };
        }
        """
    )


async def _tnstc_ensure_places_before_search(
    page,
    run: "TravelRun",
    *,
    from_place: tuple[str, str, str] | None,
    to_place: tuple[str, str, str] | None,
) -> bool:
    """Re-apply route if autocomplete cleared Source/Destination before search."""
    ok = True
    check = await _tnstc_places_ready(page)
    if from_place:
        pid, pcode, pname = from_place
        need = (
            not (check.get("fromVisible") or "").strip()
            or not (check.get("fromCode") or "").strip()
            or not (check.get("fromId") or "").strip()
        )
        if need:
            await _tnstc_apply_place(
                page, is_from=True, place_id=pid, place_code=pcode, place_name=pname
            )
            run._log(f"→ Re-applied From: {pname}")
    if to_place:
        pid, pcode, pname = to_place
        need = (
            not (check.get("toVisible") or "").strip()
            or not (check.get("toCode") or "").strip()
            or not (check.get("toId") or "").strip()
        )
        if need:
            await _tnstc_apply_place(
                page, is_from=False, place_id=pid, place_code=pcode, place_name=pname
            )
            run._log(f"→ Re-applied To: {pname}")
    check = await _tnstc_places_ready(page)
    if not (check.get("fromVisible") or "").strip():
        run._log("⚠ TNSTC Source still empty — click Source and pick city in Chrome")
        ok = False
    if not (check.get("toVisible") or "").strip():
        run._log("⚠ TNSTC Destination still empty")
        ok = False
    return ok


async def fill_tnstc_place(
    page,
    run: "TravelRun",
    *,
    field_id: str,
    city: str,
    label: str,
) -> tuple[str, str, str] | None:
    """Resolve city via TNSTC place API — returns (id, code, name) for re-apply before search."""
    city = (city or "").strip()
    if not city:
        return None
    is_from = field_id == "matchStartPlace"
    best: tuple[str, str, str] | None = None
    best_score = 0

    for alias in _city_aliases(city):
        needle = alias.split(",")[0].strip()
        places = await _tnstc_fetch_places(page, needle, is_from=is_from)
        for pid, pcode, pname in places:
            score = _tnstc_score_place(needle, pname)
            if score > best_score:
                best_score = score
                best = (pid, pcode, pname)
        if best_score >= 90:
            break

    if not best or best_score < 35:
        run._log(f"⚠ TNSTC: no place match for **{city}** ({label})")
        return None

    pid, pcode, pname = best
    await _tnstc_apply_place(
        page, is_from=is_from, place_id=pid, place_code=pcode, place_name=pname
    )
    run._log(f"→ {label}: {pname}")
    await asyncio.sleep(0.3)
    return best


async def select_tnstc_date(page, run: "TravelRun", date_str: str) -> bool:
    """TNSTC onward date — readonly field + jQuery #ui-datepicker-div."""
    date_str = (date_str or "").strip()
    if not date_str:
        return False
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        run._log(f"⚠ Invalid date: {date_str} (use DD/MM/YYYY)")
        return False

    day = dt.day
    month_abbr = dt.strftime("%b")
    month_full = dt.strftime("%B")
    year = dt.year

    try:
        await page.locator("#txtdeptDateOtrip").click(timeout=5000)
    except Exception:
        run._log("⚠ TNSTC date field not found")
        return False
    await asyncio.sleep(0.7)

    for _ in range(16):
        title = ""
        try:
            title = await page.evaluate(
                """() => {
                const el = document.querySelector('#ui-datepicker-div .ui-datepicker-title');
                return el ? (el.innerText || '').trim() : '';
            }"""
            )
        except Exception:
            pass
        if str(year) in title and (
            month_abbr in title or month_full in title or str(dt.month) in title
        ):
            break
        try:
            nxt = page.locator(
                "#ui-datepicker-div a.ui-datepicker-next:not(.ui-state-disabled)"
            ).first
            if await nxt.count() and await nxt.is_visible():
                await nxt.click()
                await asyncio.sleep(0.35)
            else:
                break
        except Exception:
            break

    clicked = await page.evaluate(
        """
        ([day]) => {
            const dayStr = String(day);
            for (const el of document.querySelectorAll('#ui-datepicker-div td a')) {
                const t = (el.innerText || el.textContent || '').trim();
                if (t === dayStr || t === ('0' + dayStr).slice(-2)) {
                    el.click();
                    return t;
                }
            }
            return '';
        }
        """,
        [day],
    )
    if clicked:
        run._log(f"→ Date: {date_str}")
        await page.evaluate(
            """
            ([d]) => {
                const form = document.forms['advanceBookingActionForm'] || document.forms[0];
                const vis = document.getElementById('txtdeptDateOtrip');
                if (vis) vis.value = d;
                if (!form) return;
                if (form.txtJourneyDate) form.txtJourneyDate.value = d;
                if (form.hiddenOnwardJourneyDate) form.hiddenOnwardJourneyDate.value = d;
                if (form.txtdeptDateOtrip) form.txtdeptDateOtrip.value = d;
            }
            """,
            [date_str],
        )
        await asyncio.sleep(0.4)
        return True

    run._log(f"⚠ Could not pick date on TNSTC calendar — set **{date_str}** in Chrome")
    return False


async def _tnstc_submit_search(page, run: "TravelRun") -> bool:
    """Use site's setSearchAction() so place IDs/codes are posted correctly."""
    submitted = await page.evaluate(
        """
        () => {
            if (typeof setSearchAction === 'function') {
                setSearchAction('SearchService');
                return 'setSearchAction';
            }
            const btn = document.getElementById('searchButton');
            if (btn) { btn.click(); return 'button'; }
            return '';
        }
        """
    )
    if submitted:
        run._log("→ Submitted TNSTC search")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=45000)
        except Exception:
            pass
        await asyncio.sleep(2)
        return True
    return False


async def _tnstc_page_kind(page) -> str:
    return await page.evaluate(
        """
        () => {
            const text = (document.body.innerText || '').slice(0, 12000).toLowerCase();
            const url = (location.href || '').toLowerCase();
            const searchHome = document.querySelector('input#matchStartPlace[type="text"]')
                && document.getElementById('searchButton')
                && document.getElementById('searchButton').offsetParent !== null;
            if (searchHome && !/searchservice/i.test(url)) return 'search_home';
            if (/booking is enabled between/.test(text)) return 'hours_error';
            if (/booking-error page/.test(text)) return 'error';
            const loginEl = document.getElementById('txtUserLoginID');
            if (loginEl && loginEl.offsetParent !== null) return 'login';
            if (/seat\\s*layout|select\\s*your\\s*seat|coach\\s*layout|choose\\s*seat/i.test(text)) return 'seats';
            if (document.querySelector(
                '.seat-available, .seatavailable, td[onclick*="seat" i], '
                + 'img[usemap][name*="seat" i]'
            )) return 'seats';
            const visPassenger = [...document.querySelectorAll(
                'input:not([type="hidden"]), select:not([type="hidden"]), textarea'
            )].some(el => {
                if (!el.offsetParent) return false;
                const blob = ((el.name || '') + (el.id || '') + (el.placeholder || '')).toLowerCase();
                return /aadhaar|passenger\\s*name|paxname|txtpassenger/i.test(blob);
            });
            if (/passenger\\s*detail|passenger\\s*information/i.test(text) && visPassenger) {
                return 'passenger';
            }
            if (visPassenger) return 'passenger';
            if (/payment|pay\\s*now|billdesk|proceed\\s*to\\s*pay/i.test(text)) return 'payment';
            if (/available\\s*service|service\\s*details|search\\s*result|departure\\s*time/i.test(text)) {
                return 'services';
            }
            const serviceClick = [...document.querySelectorAll('[onclick], a, button, img, input')].some(el => {
                const blob = ((el.getAttribute('onclick') || '') + (el.innerText || '') +
                    (el.alt || '') + (el.value || '') + (el.src || '')).toLowerCase();
                return /selectservice|showcoach|seatlayout|bookservice|select\\s*service|view\\s*seat/.test(blob)
                    || /^select$/i.test((el.innerText || el.alt || el.value || '').trim());
            });
            if (serviceClick && !searchHome) return 'services';
            return 'unknown';
        }
        """
    )


async def _tnstc_booking_hours_blocked(page) -> bool:
    try:
        text = await page.evaluate("() => (document.body.innerText || '').slice(0, 4000)")
    except Exception:
        return False
    return bool(text and re.search(r"booking is enabled between", text, re.I))


async def _tnstc_try_select_first_service(page, run: "TravelRun") -> bool:
    picked = await page.evaluate(
        """
        () => {
            const score = (el) => {
                const blob = ((el.getAttribute('onclick') || '') + (el.innerText || '') +
                    (el.alt || '') + (el.value || '') + (el.src || '')).toLowerCase();
                if (/selectservice|showcoach|seatlayout|bookservice|funselect/i.test(blob)) return 100;
                if (/^select$/i.test((el.innerText || el.alt || el.value || '').trim())) return 90;
                if (/view\\s*seat|book\\s*now|proceed/i.test(blob)) return 80;
                if (el.tagName === 'IMG' && /select|book/i.test((el.alt || el.src || '').toLowerCase())) return 75;
                return 0;
            };
            let best = null, bestS = 0;
            for (const el of document.querySelectorAll(
                'a, button, input, img, tr[onclick], td[onclick], span[onclick]'
            )) {
                const r = el.getBoundingClientRect();
                if (r.width < 12 || r.height < 8) continue;
                const s = score(el);
                if (s > bestS) { bestS = s; best = el; }
            }
            if (!best) {
                const radio = document.querySelector(
                    'input[type="radio"][name*="service" i], input[type="radio"][name*="bus" i]'
                );
                if (radio) { radio.click(); return 'radio'; }
                const row = document.querySelector('table tbody tr');
                if (row) {
                    const link = row.querySelector('a, img, input, button');
                    if (link) { link.click(); return (row.innerText || '').slice(0, 40); }
                }
                return '';
            }
            best.click();
            return ((best.innerText || best.alt || best.value || '') + '').trim().slice(0, 60);
        }
        """
    )
    if picked:
        run._log(f"→ Selected bus service: {picked}")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(2)
        return True
    return False


async def _tnstc_pick_seats(page, run: "TravelRun", passenger_count: int) -> int:
    picked = await page.evaluate(
        """
        ([need]) => {
            let count = 0;
            const isAvail = (el) => {
                const cls = (el.className || '').toString().toLowerCase();
                const onclick = (el.getAttribute('onclick') || '').toLowerCase();
                const title = (el.title || '').toLowerCase();
                if (/booked|reserved|sold|blocked|ladies|disabled|unavailable/.test(cls + onclick + title)) return false;
                if (/available|vacant|empty|seatavail|seat-available|green/.test(cls + onclick + title)) return true;
                if (el.tagName === 'TD' && onclick.includes('seat')) return true;
                return false;
            };
            const nodes = [
                ...document.querySelectorAll(
                    'td[onclick*="seat" i], td[onclick*="Seat" i], '
                    + '.seat-available, .seatavailable, .availableSeat, '
                    + 'img[onclick*="seat" i], area[onclick*="seat" i]'
                ),
            ];
            for (const el of nodes) {
                if (count >= need) break;
                if (!isAvail(el)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 6 || r.height < 6) continue;
                try { el.click(); count++; } catch (e) {}
            }
            return count;
        }
        """,
        [max(1, passenger_count)],
    )
    if picked:
        run._log(f"→ Selected {picked} seat(s) on layout")
    return int(picked or 0)


async def _tnstc_click_continue(page, run: "TravelRun") -> bool:
    clicked = await page.evaluate(
        """
        () => {
            const want = /^(continue|proceed|next|confirm|submit|book\\s*now|done)$/i;
            for (const el of document.querySelectorAll('a, button, input[type="button"], input[type="submit"]')) {
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 10) continue;
                const t = ((el.innerText || el.value || '') + '').trim();
                if (!want.test(t)) continue;
                if (/cancel|back|home|search\\s*again/i.test(t)) continue;
                el.click();
                return t;
            }
            return '';
        }
        """
    )
    if clicked:
        run._log(f"→ Clicked {clicked}")
        await asyncio.sleep(2)
        return True
    return False


async def _tnstc_handle_login(page, run: "TravelRun", wait_human: Callable[[str], Awaitable[str]]) -> None:
    run._log("🔐 TNSTC login step")
    creds = await ask_tagged(
        wait_human,
        TAG_LOGIN_FORM,
        "TNSTC **login** — enter username/password + CAPTCHA in Chrome, "
        "or reply `user|password` for auto-fill then solve CAPTCHA in browser.",
    )
    if creds.upper() == "CANCEL":
        return
    if "|" in creds:
        user, pwd = creds.split("|", 1)
        await fill_first(
            page,
            ("#txtUserLoginID", 'input[placeholder*="User Name" i]'),
            user.strip(),
        )
        await fill_first(page, ("#txtPassword",), pwd.strip())
        cap = await ask_tagged(
            wait_human,
            TAG_CAPTCHA,
            "Enter the **TNSTC login CAPTCHA** shown in Chrome:",
        )
        if cap and cap.upper() not in ("CANCEL", "SKIP"):
            await fill_first(page, ("#txtCaptchaCode",), cap.strip())
            await click_first(
                page,
                (
                    "#ValidateUser",
                    'button:has-text("Login")',
                    'button[type="submit"]',
                ),
            )
            await asyncio.sleep(2)


async def _tnstc_search_succeeded(page) -> bool:
    kind = await _tnstc_page_kind(page)
    return kind not in ("search_home", "hours_error", "error")


async def _tnstc_advance_after_search(
    page,
    run: "TravelRun",
    wait_human: Callable[[str], Awaitable[str]],
    *,
    passenger_count: int,
) -> None:
    """Walk TNSTC OTRS pages: services → seats → login → passenger (stop before pay)."""
    if not await _tnstc_search_succeeded(page):
        return

    last_kind = ""
    for _ in range(14):
        kind = await _tnstc_page_kind(page)
        if kind == last_kind:
            await asyncio.sleep(1.2)
        last_kind = kind

        if kind == "search_home":
            run._log("⚠ Still on TNSTC search form — search may not have completed")
            return
        if kind == "hours_error":
            run._log("⚠ TNSTC booking only **02:30–23:46 IST**")
            return
        if kind == "error":
            run._log("⚠ TNSTC error page — check route/date in Chrome")
            return
        if kind == "services":
            run._phase("select")
            if await _tnstc_try_select_first_service(page, run):
                continue
            break
        if kind == "seats":
            run._phase("checkout")
            n = await _tnstc_pick_seats(page, run, passenger_count)
            if n < passenger_count:
                run._log(
                    f"⚠ Pick {passenger_count - n} more seat(s) in Chrome (ladies-only berths may block auto-pick)"
                )
            await _tnstc_click_continue(page, run)
            continue
        if kind == "login":
            await _tnstc_handle_login(page, run, wait_human)
            continue
        if kind in ("passenger", "payment"):
            break
        await asyncio.sleep(1.5)


async def state_transport_portal_booking(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    *,
    portal_url: str,
    origin: str,
    destination: str,
    journey_date: str,
    portal_name: str,
    passenger_count: int = 1,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    """TNSTC OTRS — full scripted path: search → service → seats → login → passenger."""
    page = engine.page
    if not _is_tnstc(portal_url):
        await travel_portal_search(
            engine,
            run,
            portal_url=portal_url,
            origin=origin,
            destination=destination,
            journey_date=journey_date,
            portal_name=portal_name,
            wait_human=wait_human,
        )
        return

    run._phase("search")
    await dismiss_tnstc_modals(page)

    origin = (origin or "").strip()
    destination = (destination or "").strip()

    ok_date = await select_tnstc_date(page, run, journey_date)
    if not ok_date:
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"Set TNSTC **Onward** date to **{journey_date}** in Chrome, then confirm.",
            "Date set",
        )

    from_place = await fill_tnstc_place(
        page, run, field_id="matchStartPlace", city=origin, label="From"
    )
    to_place = await fill_tnstc_place(
        page, run, field_id="matchEndPlace", city=destination, label="To"
    )

    if not from_place or not to_place:
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"On TNSTC, set **Source** = {origin} and **Destination** = {destination}, "
            f"date **{journey_date}**, then confirm.",
            "Route filled",
        )

    await _tnstc_ensure_places_before_search(
        page, run, from_place=from_place, to_place=to_place
    )
    check = await _tnstc_places_ready(page)
    run._log(
        f"→ Route check — From: {check.get('fromVisible', '')[:40]} | To: {check.get('toVisible', '')[:40]}"
    )

    await screenshot(engine, run)
    if not await _tnstc_submit_search(page, run):
        if await click_first(page, ("#searchButton", 'button:has-text("Search Bus")')):
            run._log("→ Clicked Search Bus")
            await asyncio.sleep(4)
        else:
            await click_text(page, r"search\s*bus")
            await asyncio.sleep(4)

    if await _tnstc_booking_hours_blocked(page):
        run._log(
            "⚠ TNSTC online booking is only open **02:30–23:46 IST**. "
            "Retry during that window."
        )
        await finish_page_summary(engine, run, page)
        return

    if not await _tnstc_search_succeeded(page):
        kind = await _tnstc_page_kind(page)
        if kind == "search_home":
            await _tnstc_ensure_places_before_search(
                page, run, from_place=from_place, to_place=to_place
            )
            run._log(
                "⚠ Search did not leave the home page — Source/Destination may be empty in Chrome. "
                "Fill **Source** and **Destination**, click **Search Bus**, then confirm below."
            )
            await ask_tagged(
                wait_human,
                TAG_CONFIRM_DONE,
                "Fill **Source** and **Destination** on TNSTC, click **Search Bus**, wait for bus list, then confirm.",
                "Search done",
            )
        else:
            run._log("⚠ TNSTC search failed — see Chrome for errors")
            await finish_page_summary(engine, run, page)
            return

    await _tnstc_advance_after_search(
        page, run, wait_human, passenger_count=passenger_count
    )

    if not await _tnstc_search_succeeded(page):
        await finish_page_summary(engine, run, page)
        return

    kind = await _tnstc_page_kind(page)
    if kind == "services":
        run._log("⚠ Could not auto-select a bus — pick a service row in Chrome")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Click **Select** on your preferred TNSTC bus in Chrome, then confirm.",
            "Service selected",
        )
        await _tnstc_advance_after_search(
            page, run, wait_human, passenger_count=passenger_count
        )

    await screenshot(engine, run)
    kind = await _tnstc_page_kind(page)
    if kind == "seats":
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"Select **{passenger_count}** seat(s) on the coach layout in Chrome, click **Continue**, then confirm.",
            "Seats done",
        )
        await _tnstc_advance_after_search(
            page, run, wait_human, passenger_count=passenger_count
        )

    if passenger_count > 1:
        run._log(f"ℹ️ {passenger_count} passengers — verify count on seat/passenger screens")

    kind = await _tnstc_page_kind(page)
    if kind in ("passenger", "payment", "seats", "login"):
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Fill **passenger name, age, gender, mobile, Aadhaar** on TNSTC (stop before payment), then confirm.",
            "Passenger done",
        )
    elif kind == "services":
        run._log("ℹ️ Select bus → seats → passengers in Chrome; confirm when on passenger form")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Complete **bus selection**, **seats**, and **passenger details** in Chrome, then confirm.",
            "Passenger done",
        )
    else:
        run._log(f"ℹ️ TNSTC step: {kind} — complete booking screens in Chrome")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Complete the current TNSTC step in Chrome (bus/seats/passengers), then confirm.",
            "Step done",
        )

    pay = await ask_tagged(
        wait_human,
        TAG_PAYMENT_CONFIRM,
        "Review **fare and journey** on TNSTC. Proceed to payment in Chrome only if you intend to pay.",
    )
    if pay.upper() in ("NO", "CANCEL"):
        run._log("⏹ Stopped before payment (by user)")
        await finish_page_summary(engine, run, page)
        return

    otp = await ask_tagged(
        wait_human,
        TAG_OTP,
        "Enter **payment/booking OTP** from SMS if prompted, or type **SKIP**.",
    )
    if otp and otp.upper() not in ("", "SKIP"):
        run._log("→ OTP received")

    await finish_page_summary(engine, run, page)


def _city_aliases(city: str) -> list[str]:
    base = (city or "").strip()
    if not base:
        return []
    aliases = [base]
    low = base.lower()
    if "bangalore" in low or "bengaluru" in low:
        aliases.extend(["Bengaluru", "Bangalore"])
    if "coimbatore" in low:
        aliases.append("Coimbatore")
    if "chennai" in low or "madras" in low:
        aliases.extend(["Chennai", "Madras"])
    if "mumbai" in low or "bombay" in low:
        aliases.extend(["Mumbai", "Bombay"])
    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for a in aliases:
        key = a.lower()
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


async def prepare_redbus_page(page, run: "TravelRun") -> None:
    """Scroll past fixed header and close RedBus overlays that block clicks."""
    await dismiss_cookie_banner(page)
    for name in ("Close App Install Banner",):
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.is_visible(timeout=1500):
                await btn.click()
                run._log("→ Dismissed app install banner")
                await asyncio.sleep(0.4)
        except Exception:
            pass
    await page.evaluate("window.scrollTo(0, 280)")
    await asyncio.sleep(0.6)


def _normalize_india_mobile(mobile: str) -> str:
    digits = re.sub(r"\D", "", mobile or "")
    return digits[-10:] if len(digits) >= 10 else ""


async def _redbus_login_screen_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const t = (document.body.innerText || '').slice(0, 8000).toLowerCase();
                    if (/login to get exciting offers|what'?s your mobile number/i.test(t))
                        return true;
                    if (/country code/i.test(t) && /mobile number/i.test(t) && /passkey/i.test(t))
                        return true;
                    const mobileInp = document.querySelector(
                        'input[placeholder*="mobile" i], input[name*="mobile" i], input[type="tel"]'
                    );
                    if (mobileInp) {
                        const r = mobileInp.getBoundingClientRect();
                        if (r.width > 40 && r.height > 10 && /continue/i.test(t)) return true;
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _redbus_otp_screen_visible(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                    const t = (document.body.innerText || '').slice(0, 6000).toLowerCase();
                    if (/enter otp|verify otp|one time password|otp sent/i.test(t)) return true;
                    for (const inp of document.querySelectorAll('input')) {
                        const ph = (inp.placeholder || '').toLowerCase();
                        const nm = (inp.name || '').toLowerCase();
                        const al = (inp.getAttribute('aria-label') || '').toLowerCase();
                        if (/otp|verification code|enter code/.test(ph + nm + al)) {
                            const r = inp.getBoundingClientRect();
                            if (r.width > 20 && r.height > 10) return true;
                        }
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


async def _fill_redbus_login_mobile(page, run: "TravelRun", mobile: str) -> bool:
    digits = _normalize_india_mobile(mobile)
    if not digits:
        return False
    filled = await page.evaluate(
        """
        (digits) => {
            const isCountryField = (inp) => {
                const ctx = (inp.closest('div, label, section')?.innerText || '').slice(0, 220).toLowerCase();
                return /country code/i.test(ctx) && !/mobile number/i.test(ctx);
            };
            const candidates = [];
            for (const inp of document.querySelectorAll('input')) {
                if (inp.type === 'hidden' || inp.disabled) continue;
                const ph = (inp.placeholder || '').toLowerCase();
                const nm = (inp.name || '').toLowerCase();
                const al = (inp.getAttribute('aria-label') || '').toLowerCase();
                const id = (inp.id || '').toLowerCase();
                const blob = ph + nm + al + id;
                if (!/mobile|phone|tel/.test(blob) && inp.type !== 'tel') continue;
                if (isCountryField(inp)) continue;
                const r = inp.getBoundingClientRect();
                if (r.width < 50 || r.height < 10) continue;
                candidates.push(inp);
            }
            if (!candidates.length) return false;
            const inp = candidates[0];
            inp.focus();
            inp.value = digits;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        """,
        digits,
    )
    if filled:
        run._log(f"→ RedBus login mobile: {digits[:4]}******")
        return True
    for loc in (
        page.get_by_label(re.compile(r"mobile\s*number", re.I)),
        page.locator('input[placeholder*="Mobile" i]'),
        page.locator('input[type="tel"]').nth(1),
        page.locator('input[type="tel"]').first,
    ):
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                await loc.first.fill(digits)
                run._log(f"→ RedBus login mobile: {digits[:4]}******")
                return True
        except Exception:
            continue
    return False


async def _click_redbus_login_continue(page, run: "TravelRun") -> bool:
    """Click Continue on login — avoid Passkey / Face ID buttons."""
    clicked = await page.evaluate(
        """
        () => {
            const bad = (t) => /passkey|face id|fingerprint|sign in with/i.test((t || '').toLowerCase());
            const ok = (t) => /^continue$/i.test((t || '').replace(/\\s+/g, ' ').trim());
            for (const el of document.querySelectorAll('button, a, [role="button"]')) {
                const raw = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                    .replace(/\\s+/g, ' ').trim();
                if (!ok(raw) || bad(raw)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 12) continue;
                el.click();
                return raw;
            }
            return '';
        }
        """
    )
    if clicked:
        run._log("→ Clicked **Continue** on RedBus login")
        return True
    try:
        btn = page.get_by_role("button", name=re.compile(r"^Continue$", re.I))
        if await btn.count() > 0:
            await btn.first.click(force=True)
            run._log("→ Clicked Continue (Playwright)")
            return True
    except Exception:
        pass
    return False


async def _submit_redbus_otp(page, run: "TravelRun", otp: str) -> bool:
    otp = re.sub(r"\D", "", otp or "")
    if not otp:
        return False
    filled = await page.evaluate(
        """
        (code) => {
            const inputs = [...document.querySelectorAll('input')].filter((inp) => {
                const ph = (inp.placeholder || '').toLowerCase();
                const nm = (inp.name || '').toLowerCase();
                const al = (inp.getAttribute('aria-label') || '').toLowerCase();
                const blob = ph + nm + al;
                if (!/otp|verification|enter code|pin/.test(blob)) return false;
                const r = inp.getBoundingClientRect();
                return r.width > 16 && r.height > 10;
            });
            if (inputs.length === 1) {
                inputs[0].focus();
                inputs[0].value = code;
                inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
                inputs[0].dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
            if (inputs.length >= 4 && inputs.length <= 8 && code.length === inputs.length) {
                for (let i = 0; i < inputs.length; i++) {
                    inputs[i].value = code[i];
                    inputs[i].dispatchEvent(new Event('input', { bubbles: true }));
                }
                return true;
            }
            return false;
        }
        """,
        otp,
    )
    if not filled:
        await fill_first(
            page,
            (
                'input[placeholder*="OTP" i]',
                'input[name*="otp" i]',
                'input[inputmode="numeric"]',
            ),
            otp,
        )
    for loc in (
        page.get_by_role("button", name=re.compile(r"verify|continue|submit|login", re.I)),
        page.locator('button:has-text("Verify")'),
        page.locator('button:has-text("Continue")'),
    ):
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click(force=True)
                run._log("→ Submitted RedBus OTP")
                await asyncio.sleep(2)
                return True
        except Exception:
            continue
    return filled


async def ensure_redbus_logged_in(
    page,
    run: "TravelRun",
    wait_human: Callable[[str], Awaitable[str]],
    mobile: str = "",
) -> bool:
    """Handle RedBus mobile login + OTP (skip Passkey / Face ID)."""
    if not _is_redbus(page.url or ""):
        return True

    digits = _normalize_india_mobile(mobile)
    if not digits:
        digits = _normalize_india_mobile(
            os.getenv("BUS_CONTACT_MOBILE", os.getenv("IRCTC_MOBILE", ""))
        )

    for round_i in range(3):
        if not await _redbus_login_screen_visible(page) and not await _redbus_otp_screen_visible(page):
            return True

        if await _redbus_login_screen_visible(page):
            run._log("📱 RedBus login — mobile number step")
            if digits:
                await _fill_redbus_login_mobile(page, run, digits)
                await asyncio.sleep(0.4)
                await _click_redbus_login_continue(page, run)
                await asyncio.sleep(2.5)
            else:
                run._log("⚠ No mobile in form — complete RedBus login in Chrome")
                await ask_tagged(
                    wait_human,
                    TAG_CONFIRM_DONE,
                    "On RedBus **login**, enter your **mobile number**, click **Continue**, "
                    "complete **OTP** (ignore Passkey / Face ID), then confirm.",
                    "RedBus login done",
                )
                await asyncio.sleep(1)
                if not await _redbus_login_screen_visible(page):
                    return True
                continue

        if await _redbus_otp_screen_visible(page):
            run._log("📱 RedBus OTP step")
            otp = await ask_tagged(
                wait_human,
                TAG_OTP,
                f"RedBus sent an OTP to **{digits[:4]}******. Enter the SMS OTP below (type **SKIP** if already logged in).",
            )
            if otp.upper() not in ("", "SKIP"):
                await _submit_redbus_otp(page, run, otp)
                await asyncio.sleep(2.5)
            else:
                await ask_tagged(
                    wait_human,
                    TAG_CONFIRM_DONE,
                    "Complete RedBus **OTP** in Chrome if needed, then confirm.",
                    "RedBus login done",
                )
            await asyncio.sleep(1)
            if not await _redbus_login_screen_visible(page) and not await _redbus_otp_screen_visible(page):
                run._log("✅ RedBus login complete")
                return True
            continue

        await asyncio.sleep(1)

    if await _redbus_login_screen_visible(page) or await _redbus_otp_screen_visible(page):
        run._log("⚠ RedBus login still visible — finish OTP in Chrome")
        return False
    return True


async def wait_redbus_search_widget(
    page,
    run: "TravelRun",
    timeout: float = 25,
    *,
    wait_human: Optional[Callable[[str], Awaitable[str]]] = None,
    mobile: str = "",
) -> bool:
    await prepare_redbus_page(page, run)
    if wait_human:
        await ensure_redbus_logged_in(page, run, wait_human, mobile=mobile)
    loc = page.locator(
        "#srcinput, #destinput, #src, #dest, input[role='combobox']"
    ).first
    try:
        await loc.wait_for(state="attached", timeout=int(timeout * 1000))
        return True
    except Exception:
        run._log("⚠ RedBus search widget not visible yet — scrolling…")
        await page.evaluate("window.scrollTo(0, 420)")
        await asyncio.sleep(0.8)
        try:
            await loc.wait_for(state="attached", timeout=8000)
            return True
        except Exception:
            run._log("⚠ RedBus search form still not found")
            return False


async def _pick_redbus_option(page, alias: str) -> bool:
    """Select city from new RedBus listbox ([role=option])."""
    short = alias[: min(8, len(alias))]
    pattern = re.compile(re.escape(short), re.I)
    opt = page.locator('[role="option"]').filter(has_text=pattern).first
    if await opt.count() == 0:
        opt = page.locator('[role="option"]').first
    if await opt.count() == 0:
        return False
    try:
        await opt.click(force=True)
        return True
    except Exception:
        return False


async def fill_redbus_city(
    page,
    run: "TravelRun",
    *,
    field: str,
    city: str,
) -> bool:
    """Fill RedBus city — supports new UI (#srcinput) and legacy (#src)."""
    label = "From" if field == "from" else "To"
    input_ids = (
        ("srcinput", "src") if field == "from" else ("destinput", "dest")
    )
    list_idx = 1 if field == "from" else 2

    for input_id, legacy_id in (input_ids,):
        for try_id in (input_id, legacy_id):
            inp = page.locator(f"#{try_id}").first
            if await inp.count() == 0:
                continue
            try:
                await inp.wait_for(state="attached", timeout=6000)
            except Exception:
                continue

            for alias in _city_aliases(city):
                try:
                    await inp.focus()
                    await inp.fill("")
                    await inp.press_sequentially(alias, delay=100)
                    await asyncio.sleep(2.0)

                    if await _pick_redbus_option(page, alias):
                        val = (await inp.input_value()).strip() or alias
                        run._log(f"→ {label}: {val[:70]}")
                        await asyncio.sleep(0.5)
                        return True

                    picked = await page.evaluate(
                        """
                        ([listIdx, alias]) => {
                            const a = alias.toLowerCase();
                            const lists = [
                                document.querySelector(`#search > div > div:nth-child(${listIdx}) ul`),
                                document.querySelector('#search ul'),
                            ].filter(Boolean);
                            for (const ul of lists) {
                                for (const li of ul.querySelectorAll('li')) {
                                    const t = (li.innerText || '').trim();
                                    if (!t.toLowerCase().includes(a.slice(0, 4))) continue;
                                    li.click();
                                    return t.slice(0, 70);
                                }
                            }
                            return '';
                        }
                        """,
                        [list_idx, alias],
                    )
                    if picked:
                        run._log(f"→ {label}: {picked}")
                        return True

                    value = (await inp.input_value()).strip()
                    if value and a_match(value, alias):
                        run._log(f"→ {label}: {value[:70]}")
                        return True
                except Exception:
                    continue

    # Accessibility combobox fallback (new RedBus header form)
    try:
        combo = page.get_by_role("combobox", name=label)
        if await combo.count() > 0:
            for alias in _city_aliases(city):
                await combo.focus()
                await combo.fill("")
                await combo.press_sequentially(alias, delay=100)
                await asyncio.sleep(2.0)
                if await _pick_redbus_option(page, alias):
                    run._log(f"→ {label}: {alias}")
                    return True
    except Exception:
        pass

    run._log(f"⚠ Could not set RedBus {label} — enter **{city}** in Chrome")
    return False


def a_match(value: str, alias: str) -> bool:
    v = value.lower()
    a = alias.lower()
    return a[:4] in v or v[:4] in a


def _date_label_matches(aria_label: str, dt: datetime) -> bool:
    aria = (aria_label or "").lower()
    if not aria:
        return False
    month_names = [
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    ]
    mon = month_names[dt.month - 1]
    day_patterns = (str(dt.day), f"{dt.day:02d}")
    year_ok = str(dt.year) in aria
    for d in day_patterns:
        if d in aria and mon in aria and year_ok:
            return True
        if d in aria and mon in aria and "current date" in aria:
            return True
    return False


async def select_redbus_date(page, run: "TravelRun", date_str: str) -> bool:
    """RedBus date — new combobox UI or legacy #onward_cal calendar."""
    date_str = (date_str or "").strip()
    if not date_str:
        return False
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        run._log(f"⚠ Invalid date: {date_str} (use DD/MM/YYYY)")
        return False

    day = dt.day
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    month_abbr = month_names[dt.month - 1]
    month_full = dt.strftime("%B")
    year = dt.year

    # ── New RedBus UI: div[role=combobox] "Select Date of Journey…" ──
    new_date = page.locator('[role="combobox"][aria-label*="Date of Journey"]').first
    if await new_date.count() > 0:
        aria = (await new_date.get_attribute("aria-label")) or ""
        if _date_label_matches(aria, dt):
            run._log(f"→ Date already set: {date_str}")
            return True
        try:
            await new_date.click(force=True)
            await asyncio.sleep(0.8)
            day_str = str(day)
            day_padded = f"{day:02d}"
            picked = await page.evaluate(
                """
                ([dayStr, dayPad, monthAbbrev, monthFull, year]) => {
                    const monthRe = new RegExp(monthAbbrev + '|' + monthFull, 'i');
                    for (const el of document.querySelectorAll(
                        'button, td, span, div[role="gridcell"], [role="option"]'
                    )) {
                        const cls = String(el.className || '').toLowerCase();
                        if (cls.includes('disabled') || cls.includes('past')) continue;
                        const t = (el.innerText || '').trim();
                        if (t !== dayStr && t !== dayPad) continue;
                        const block = (el.closest('[class*="calendar"], [role="dialog"], table') || el).innerText || '';
                        if (block && !monthRe.test(block) && !String(block).includes(String(year))) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 8 || r.height < 8) continue;
                        el.click();
                        return t;
                    }
                    return '';
                }
                """,
                [day_str, day_padded, month_abbr, month_full, year],
            )
            if picked:
                run._log(f"→ Date: {date_str}")
                return True
        except Exception:
            pass

    # ── Legacy RedBus: input#onward_cal + #rb-calendar_onward_cal ──
    triggers = (
        "#onward_cal",
        "#onward_calender",
        "#onward_calendar",
        'input[id*="onward_cal"]',
        'label[for="onward_cal"]',
        ".onwardCal",
        ".date-text",
    )
    cal_input = None
    for sel in triggers:
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            cal_input = loc
            break

    if cal_input is None:
        if await new_date.count() > 0:
            run._log(f"⚠ Could not set date on RedBus — pick **{date_str}** in Chrome")
        else:
            run._log("⚠ RedBus date field not found")
        return False

    try:
        current = (await cal_input.input_value()).strip()
    except Exception:
        current = (await cal_input.text_content() or "").strip()

    if current and (
        str(day) in current
        and (month_abbr in current or month_full in current or str(dt.month) in current)
    ):
        run._log(f"→ Date already set: {current[:40]}")
        return True

    await cal_input.click()
    await asyncio.sleep(0.9)

    cal_root = page.locator(
        "#rb-calendar_onward_cal, [id^='rb-calendar'], .rb-calendar, [class*='CalendarWrapper']"
    ).first
    try:
        await cal_root.wait_for(state="visible", timeout=5000)
    except Exception:
        await cal_input.click()
        await asyncio.sleep(0.8)

    for _ in range(18):
        title_el = page.locator(
            "#rb-calendar_onward_cal td.monthTitle, "
            "#rb-calendar_onward_cal .monthTitle, "
            "[id^='rb-calendar'] td.monthTitle, "
            "[id^='rb-calendar'] .monthTitle"
        ).first
        title = ""
        try:
            if await title_el.count() > 0:
                title = (await title_el.text_content() or "").strip()
        except Exception:
            pass

        if title and month_abbr.lower() in title.lower() and str(year) in title:
            break
        if title and month_full.lower() in title.lower() and str(year) in title:
            break

        next_btn = page.locator(
            "#rb-calendar_onward_cal button:has-text('>'), "
            "#rb-calendar_onward_cal .next, "
            "[id^='rb-calendar'] button:has-text('>'), "
            "[id^='rb-calendar'] .next, "
            ".icon-next"
        ).first
        if await next_btn.count() == 0 or not await next_btn.is_visible():
            break
        await next_btn.click()
        await asyncio.sleep(0.35)

    day_str = str(day)
    day_padded = f"{day:02d}"
    clicked = await page.evaluate(
        """
        ([dayStr, dayPad, monthAbbrev, monthFull]) => {
            const roots = [
                document.querySelector('#rb-calendar_onward_cal'),
                document.querySelector('[id^="rb-calendar"]'),
            ].filter(Boolean);
            const monthRe = new RegExp(monthAbbrev + '|' + monthFull, 'i');
            for (const root of roots) {
                const title = (root.querySelector('.monthTitle, td.monthTitle') || {}).innerText || '';
                if (title && !monthRe.test(title)) continue;
                for (const td of root.querySelectorAll('td, span, div, button')) {
                    const cls = String(td.className || '').toLowerCase();
                    if (cls.includes('disabled') || cls.includes('old') || cls.includes('past')) continue;
                    const t = (td.innerText || td.textContent || '').trim();
                    if (t !== dayStr && t !== dayPad) continue;
                    const r = td.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) continue;
                    td.click();
                    return t;
                }
            }
            return '';
        }
        """,
        [day_str, day_padded, month_abbr, month_full],
    )

    if not clicked:
        for text in (day_str, day_padded):
            cell = page.locator(
                f"#rb-calendar_onward_cal td:not(.disabled):has-text('{text}'), "
                f"[id^='rb-calendar'] td:not(.disabled):has-text('{text}')"
            ).first
            if await cell.count() > 0 and await cell.is_visible():
                await cell.click()
                clicked = text
                break

    if clicked:
        run._log(f"→ Date: {date_str}")
        await asyncio.sleep(0.5)
        return True

    run._log(f"⚠ Could not auto-pick date — set **{date_str}** on RedBus calendar")
    return False


async def click_redbus_search(page, run: "TravelRun") -> bool:
    btn = page.get_by_role("button", name=re.compile(r"Search buses", re.I))
    try:
        if await btn.count() > 0:
            if await btn.is_enabled():
                await btn.click(force=True)
                run._log("→ Clicked Search buses")
                return True
            run._log("⚠ Search buses button disabled — pick cities in Chrome first")
    except Exception:
        pass
    if await click_first(
        page,
        (
            "#search_button",
            'button:has-text("Search buses")',
            'button:has-text("Search Buses")',
        ),
    ):
        run._log("→ Clicked Search")
        return True
    return False


async def redbus_form_filled(page, origin: str, destination: str, journey_date: str) -> tuple[bool, bool, bool]:
    """Check whether Chrome form already has values (e.g. after manual user fill)."""
    ok_from = False
    ok_to = False
    ok_date = False
    try:
        src_val = (await page.locator("#srcinput, #src").first.input_value()).strip()
        ok_from = bool(src_val) and a_match(src_val, origin)
    except Exception:
        pass
    try:
        dest_val = (await page.locator("#destinput, #dest").first.input_value()).strip()
        ok_to = bool(dest_val) and a_match(dest_val, destination)
    except Exception:
        pass
    try:
        dt = datetime.strptime(journey_date.strip(), "%d/%m/%Y")
        aria = await page.locator('[role="combobox"][aria-label*="Date of Journey"]').first.get_attribute(
            "aria-label"
        )
        if aria and _date_label_matches(aria, dt):
            ok_date = True
        elif journey_date:
            ok_date = True  # legacy pages often default to today
    except Exception:
        ok_date = bool(journey_date)
    return ok_from, ok_to, ok_date


async def fill_travel_autocomplete(
    page,
    run: "TravelRun",
    selectors: tuple[str, ...],
    text: str,
    label: str,
) -> bool:
    """Type city/station and pick the first matching autocomplete suggestion."""
    text = (text or "").strip()
    if not text:
        return False

    needle = text.split(",")[0].strip()
    short = needle[: min(6, len(needle))]

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0 or not await loc.is_visible():
                continue
            await loc.click()
            await loc.fill("")
            await loc.press_sequentially(needle, delay=90)
            await asyncio.sleep(1.4)

            picked = await page.evaluate(
                """
                ([needle, short]) => {
                    const n = needle.toLowerCase();
                    const s = short.toLowerCase();
                    const lists = document.querySelectorAll(
                        '[role="listbox"] [role="option"], [role="listbox"] li, '
                        + 'ul li, .autoComplete li, .autocomplete li, '
                        + '[class*="AutoComplete"] li, [class*="suggestion"] li, '
                        + '[class*="SearchResult"] li, div[data-value]'
                    );
                    for (const el of lists) {
                        const r = el.getBoundingClientRect();
                        if (r.width < 20 || r.height < 8) continue;
                        const t = (el.innerText || el.textContent || '').trim();
                        if (!t || t.length > 120) continue;
                        const tl = t.toLowerCase();
                        if (!tl.includes(s) && !tl.includes(n.slice(0, 4))) continue;
                        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        el.click();
                        return t.slice(0, 60);
                    }
                    return '';
                }
                """,
                [needle, short],
            )
            if picked:
                run._log(f"→ {label}: {picked}")
                await asyncio.sleep(0.6)
                return True

            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.25)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.5)
            value = await loc.input_value()
            if value and len(value) > 2:
                run._log(f"→ {label}: {value[:50]}")
                return True
        except Exception:
            continue
    return False


async def select_travel_date(page, run: "TravelRun", date_str: str, portal_url: str = "") -> bool:
    """Pick journey date on RedBus/AbhiBus-style calendar widgets."""
    if _is_redbus(portal_url):
        return await select_redbus_date(page, run, date_str)

    date_str = (date_str or "").strip()
    if not date_str:
        return False
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        run._log(f"⚠ Invalid date format: {date_str} (use DD/MM/YYYY)")
        return False

    day = dt.day
    month_abbr = dt.strftime("%b")
    month_full = dt.strftime("%B")
    year = dt.year

    date_triggers = (
        "#onward_calender",
        "#onward_calendar",
        ".date-text",
        'input[placeholder*="Date" i]',
        'input[placeholder*="Depart" i]',
        '[data-automation-id="departure"]',
        ".departure-date",
    )
    if not await click_first(page, date_triggers):
        await click_text(page, r"date of journey|select date", timeout=3000)
    await asyncio.sleep(0.8)

    for _ in range(14):
        header = page.locator(
            '.monthTitle, .calendar-month, [class*="monthTitle"], '
            '[class*="MonthTitle"], .DayPicker-Caption, .rdp-caption'
        ).first
        header_text = ""
        try:
            if await header.count() > 0:
                header_text = (await header.text_content() or "").strip()
        except Exception:
            pass
        if (
            str(year) in header_text
            and (month_abbr in header_text or month_full in header_text or str(dt.month) in header_text)
        ):
            break
        if not await click_first(
            page,
            (
                '.next, .icon-next, button[aria-label="Next"]',
                '[class*="next"]:not([class*="disabled"])',
                'button:has-text("›")',
            ),
        ):
            break
        await asyncio.sleep(0.35)

    day_clicked = await page.evaluate(
        """
        ([day, monthAbbrev, monthFull]) => {
            const dayStr = String(day);
            const monthRe = new RegExp(monthAbbrev + '|' + monthFull, 'i');
            const cells = document.querySelectorAll(
                'td:not(.disabled):not(.old):not(.past), '
                + '[data-day], [data-date], .calendar-day, '
                + '.DayPicker-Day:not(.DayPicker-Day--disabled), '
                + '[class*="Calendar"] button, [class*="dateCell"]'
            );
            for (const el of cells) {
                const cls = (el.className || '').toLowerCase();
                if (cls.includes('disabled') || cls.includes('past') || cls.includes('old')) continue;
                const t = (el.innerText || el.textContent || '').trim();
                if (t !== dayStr && t !== ('0' + dayStr).slice(-2)) continue;
                const block = (el.closest('[class*="month"], table, [role="grid"]') || el).innerText || '';
                if (block && !monthRe.test(block)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                el.click();
                return t;
            }
            return '';
        }
        """,
        [day, month_abbr, month_full],
    )
    if day_clicked:
        run._log(f"→ Date: {date_str}")
        await asyncio.sleep(0.5)
        return True

    if await fill_first(page, ('input[type="date"]',), dt.strftime("%Y-%m-%d")):
        run._log(f"→ Date: {date_str}")
        return True

    run._log(f"⚠ Could not auto-pick date — set **{date_str}** manually")
    return False


async def finish_page_summary(engine: "PlaywrightEngine", run: "TravelRun", page) -> None:
    await asyncio.sleep(2)
    await screenshot(engine, run)
    summary = await page.evaluate(
        """() => document.body.innerText.slice(0, 2800)"""
    )
    if summary:
        for line in summary.split("\n"):
            line = line.strip()
            if line and len(line) > 4:
                run._log(f"  {line[:110]}")
                if sum(1 for _ in run.log if _.startswith("  ")) >= 14:
                    break
    run._log("✅ Scripted flow complete — review Chrome for OTP/CAPTCHA if needed")
    run.status = "done"
    run._phase("done")


async def travel_portal_search(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    *,
    portal_url: str,
    origin: str,
    destination: str,
    journey_date: str,
    portal_name: str,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    """Open a travel portal and attempt origin/destination/date search without LLM."""
    page = engine.page
    if not await open_url(engine, run, portal_url, label=portal_name):
        run.status = "failed"
        return

    run._phase("search")
    origin = (origin or "").strip()
    destination = (destination or "").strip()

    await dismiss_cookie_banner(page)

    if _is_redbus(portal_url):
        login_mobile = os.getenv("BUS_CONTACT_MOBILE", os.getenv("IRCTC_MOBILE", ""))
        await wait_redbus_search_widget(
            page, run, wait_human=wait_human, mobile=login_mobile
        )
        ok_from = await fill_redbus_city(page, run, field="from", city=origin)
        ok_to = await fill_redbus_city(page, run, field="to", city=destination)
    else:
        from_selectors = (
            "#src",
            "input#mmtSrc",
            'input[placeholder*="From" i]',
            'input[placeholder*="Leaving" i]',
            'input[id*="source" i]',
            'input[name*="source" i]',
            ".src input",
        )
        to_selectors = (
            "#dest",
            "input#mmtDest",
            'input[placeholder*="To" i]',
            'input[placeholder*="Going" i]',
            'input[id*="dest" i]',
            'input[name*="dest" i]',
            ".dest input",
        )
        ok_from = await fill_travel_autocomplete(page, run, from_selectors, origin, "From")
        ok_to = await fill_travel_autocomplete(page, run, to_selectors, destination, "To")

    if not ok_from or not ok_to:
        run._log("⚠ Could not auto-fill cities — complete From/To in Chrome")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"Fill **From** = {origin} and **To** = {destination}, set date to **{journey_date}**, click Search, then confirm.",
            "Search done",
        )

    await select_travel_date(page, run, journey_date, portal_url)

    await screenshot(engine, run)
    if await click_first(
        page,
        (
            "#search_button",
            "#search_btn",
            'button:has-text("Search buses")',
            'button:has-text("Search Buses")',
            'button:has-text("Search")',
            'a:has-text("Search")',
            'button[type="submit"]',
            ".search-btn",
            "#search",
        ),
    ):
        run._log("→ Clicked Search")
    else:
        await click_text(page, r"search")

    run._phase("select")
    await asyncio.sleep(3)
    await finish_page_summary(engine, run, page)


def _bus_contact_from_env() -> tuple[str, str, str, str]:
    email = os.getenv("BUS_CONTACT_EMAIL", "").strip()
    mobile = os.getenv("BUS_CONTACT_MOBILE", os.getenv("IRCTC_MOBILE", "")).strip()
    name = os.getenv("BUS_CONTACT_NAME", os.getenv("IRCTC_P1_NAME", "")).strip()
    state = os.getenv("BUS_CONTACT_STATE", "Tamil Nadu").strip()
    return name, email, mobile, state


def _bus_passenger_row(index: int, fallback_name: str) -> tuple[str, str, str]:
    i = index + 1
    name = os.getenv(f"BUS_P{i}_NAME", fallback_name if index == 0 else "").strip()
    age = os.getenv(f"BUS_P{i}_AGE", os.getenv("IRCTC_P1_AGE", "30")).strip()
    gender = os.getenv(f"BUS_P{i}_GENDER", os.getenv("IRCTC_P1_GENDER", "Male")).strip()
    return name, age, gender


async def _redbus_page_kind(page) -> str:
    """Classify RedBus page: checkout (seat flow), search (results), error, unknown."""
    try:
        url_step = _redbus_url_step(page.url or "")
        if url_step in ("board_drop", "seats", "passenger"):
            return "checkout"
        return (
            await page.evaluate(
                """
                () => {
                    const url = location.href.toLowerCase();
                    const step = ((new URLSearchParams(location.search).get('step') || '')).toUpperCase();
                    const t = (document.body.innerText || '').slice(0, 8000).toLowerCase();
                    if (/oops|no buses found|no routes available|something went wrong|try again/i.test(t))
                        return 'error';
                    if (step === 'BPDP' || step === 'SEAT' || step === 'SEATS'
                        || step === 'CUST' || step === 'CUSTINFO' || step === 'PI')
                        return 'checkout';
                    const liveBpdp = () => {
                        let left = 0, right = 0;
                        const mid = window.innerWidth * 0.42;
                        for (const r of document.querySelectorAll('input[type="radio"], [role="radio"]')) {
                            const rect = r.getBoundingClientRect();
                            if (rect.width < 1 || rect.height < 1 || rect.y < 100 || rect.y > window.innerHeight * 0.9) continue;
                            const row = (r.closest('label, li, div')?.innerText || '').replace(/\\s+/g, ' ').trim();
                            if (row.length < 8 || row.length > 160) continue;
                            const rl = row.toLowerCase();
                            if (/primo bus|bus duration|bus service|view price|coimbatore to bangalore bus/i.test(rl)) continue;
                            if (rect.x < mid) left++; else right++;
                        }
                        return left >= 1 && right >= 1;
                    };
                    if (liveBpdp()) return 'checkout';
                    const hasPassengerForm = () => {
                        if (!/passenger\\s*\\d|passenger info/i.test(t)) return false;
                        const hasName = !!document.querySelector(
                            'input[placeholder*="name" i], input[placeholder*="Name" i], input[name*="name" i]'
                        );
                        const hasGenderPills = /\\bmale\\b/.test(t) && /\\bfemale\\b/.test(t);
                        return hasName && hasGenderPills;
                    };
                    if (hasPassengerForm()) return 'checkout';
                    if (/select seats|seat(s)? selected|board\\/drop point|passenger info/i.test(t))
                        return 'checkout';
                    if (/seat|viewseat|busdetails|selectseat|custinfo|seatlayout/.test(url))
                        return 'checkout';
                    const isSearchListing = /view price|bus operator|first bus|last bus/i.test(t)
                        && (/coimbatore to bangalore bus service|bus timings|daily bus services/i.test(t)
                            || document.querySelector('table'));
                    if (url.includes('bus-tickets') && isSearchListing && step !== 'BPDP' && !hasPassengerForm())
                        return 'search';
                    if (/view seats|search buses|buses from/i.test(t) && !/select seats/i.test(t)
                        && step !== 'BPDP' && !hasPassengerForm())
                        return 'search';
                    return 'unknown';
                }
                """
            )
            or "unknown"
        )
    except Exception:
        return "unknown"


async def _focus_redbus_checkout_page(engine: "PlaywrightEngine", run: "TravelRun"):
    """Use the browser tab that shows seat selection / checkout (not search results)."""
    pages = list(engine.context.pages) if engine.context else []
    if not pages:
        return engine.page

    async def _score(p) -> int:
        try:
            kind = await _redbus_page_kind(p)
            if kind == "error":
                return -20
            url = (p.url or "").lower()
            url_step = _redbus_url_step(p.url or "")
            url_score = 0
            if url_step == "board_drop":
                url_score += 25
            elif url_step == "seats":
                url_score += 20
            elif url_step == "passenger":
                url_score += 18
            if re.search(r"seat|viewseat|busdetails|selectseat|custinfo|seatlayout", url):
                url_score += 12
            if re.search(r"bus-tickets/[^/?]+-to-", url) and url_step:
                url_score += 15
            elif re.search(r"bus-tickets/[^/?]+-to-", url) and not re.search(
                r"seat|booking|busdetails|step=", url
            ):
                url_score -= 15
            if kind == "search":
                step_hint = await _redbus_booking_step(p)
                if step_hint not in ("passenger", "seats", "board_drop"):
                    return -20
            body_score = int(
                await p.evaluate(
                    """
                    () => {
                        const t = document.body.innerText.slice(0, 12000);
                        let score = 0;
                        if (/passenger\\s*\\d/i.test(t) && /gender/i.test(t)
                            && document.querySelector('input[placeholder*="name" i], input[placeholder*="Name" i]'))
                            score += 24;
                        if (/select seats|seat(s)? selected|board\\/drop|passenger info/i.test(t)) score += 8;
                        if (/select boarding point|boarding points/i.test(t)) score += 14;
                        if (/passenger info|contact details|email|mobile number/i.test(t)) score += 10;
                        if (/\\d+\\s*seat(s)?\\s*selected/i.test(t)) score += 4;
                        if (document.querySelector('#btnProceed')) score += 6;
                        if (/view seats|search buses|buses from|no buses found/i.test(t)
                            && !/select seats/i.test(t)) score -= 10;
                        return score;
                    }
                    """
                )
                or 0
            )
            return url_score + body_score
        except Exception:
            return -1

    best = None
    best_score = -999
    for p in pages:
        s = await _score(p)
        if s > best_score:
            best_score = s
            best = p

    if best is None or best_score < 4:
        urls = ", ".join((p.url or "")[:45] for p in pages[:5])
        run._log(f"⚠ No seat/checkout tab found (best score={best_score}). Tabs: {urls}")
        for p in pages:
            if await _redbus_page_kind(p) not in ("search", "error"):
                engine.page = p
                return p
        return engine.page or pages[0]

    if best != engine.page:
        engine.page = best
        try:
            await best.bring_to_front()
        except Exception:
            pass
        run._log(f"→ Switched to checkout tab ({(best.url or '')[:72]}) score={best_score}")
    return best


async def _wait_redbus_seat_page(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    timeout: float = 45,
):
    """Wait until seat/checkout UI is visible in any tab or iframe."""
    url_re = re.compile(r"seat|viewseat|booking|busdetails|selectseat|custinfo|[?&]step=", re.I)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for p in list(engine.context.pages if engine.context else [page]):
            if url_re.search(p.url or ""):
                engine.page = p
                try:
                    await p.bring_to_front()
                except Exception:
                    pass
                run._log(f"→ Seat page URL detected: {(p.url or '')[:78]}")
                return p
        page = await _focus_redbus_checkout_page(engine, run)
        step = await _redbus_booking_step(page)
        if step in ("seats", "board_drop", "passenger"):
            return page
        await asyncio.sleep(1.0)
    return await _focus_redbus_checkout_page(engine, run)


async def _ensure_board_drop_checkout_page(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    timeout: float = 30,
):
    """Find the Chrome tab showing Board/Drop columns (not search results)."""
    deadline = asyncio.get_event_loop().time() + timeout

    async def _board_drop_score(p) -> int:
        try:
            url = p.url or ""
            url_step = _redbus_url_step(url)
            if url_step == "board_drop":
                return 60
            if await _redbus_page_kind(p) == "error":
                return -1
            if await _redbus_page_kind(p) == "search" and url_step != "board_drop":
                return -1
            if await _redbus_has_live_board_drop(p):
                return 50
            score = int(
                await p.evaluate(
                    """
                    () => {
                        const t = (document.body.innerText || '').slice(0, 14000);
                        let score = 0;
                        const step = ((new URLSearchParams(location.search).get('step') || '')).toUpperCase();
                        if (step === 'BPDP') return 55;
                        if (/select boarding point|boarding points/i.test(t)) score += 30;
                        if (/select dropping point|dropping points/i.test(t)) score += 20;
                        if (/board\\/drop point/i.test(t)) score += 15;
                        const url = location.href.toLowerCase();
                        if (/seat|busdetails|selectseat|custinfo/.test(url)) score += 10;
                        if (url.includes('bus-tickets') && !/seat|busdetails|step=bpdp/i.test(url)) score -= 40;
                        return score;
                    }
                    """
                )
                or 0
            )
            step = await _redbus_booking_step(p)
            if step == "board_drop":
                score += 25
            elif step == "passenger":
                score += 8
            return score
        except Exception:
            return -1

    while asyncio.get_event_loop().time() < deadline:
        pages = list(engine.context.pages) if engine.context else [page]
        best = None
        best_score = -1
        for p in pages:
            s = await _board_drop_score(p)
            if s > best_score:
                best_score = s
                best = p
        min_score = 35
        if best and _redbus_url_step(best.url or "") == "board_drop":
            min_score = 25
        if best and best_score >= min_score:
            engine.page = best
            try:
                await best.bring_to_front()
            except Exception:
                pass
            run._log(f"→ Board/Drop checkout tab: {(best.url or '')[:78]} (score={best_score})")
            return best
        await asyncio.sleep(1.0)

    urls = ", ".join((p.url or "")[:42] for p in (engine.context.pages if engine.context else [page][:6]))
    run._log(f"❌ Board/Drop checkout tab not found — tabs: {urls}")
    return None


_REDBUS_CONTINUE_CLICK_JS = """
(forceIfReady) => {
    const bodyTop = (document.body.innerText || '').slice(0, 6000).toLowerCase();
    const url = location.href.toLowerCase();
    const stepParam = ((new URLSearchParams(location.search).get('step') || '')).toUpperCase();
    const onCheckoutSpa = stepParam === 'BPDP' || stepParam === 'SEAT' || stepParam === 'SEATS'
        || stepParam === 'CUST' || stepParam === 'CUSTINFO' || stepParam === 'PI';
    if (/oops|no buses found|no routes available|something went wrong/i.test(bodyTop)) return '';
    // RedBus is a SPA and often loads the checkout shell before the ?step= param is set.
    // Only bail out if we're confidently on search listing (many "View seats"/"View price")
    // and there's no checkout signal at all.
    if (!onCheckoutSpa) {
        const looksLikeCheckout = /select seats|seat(s)? selected|board\\/drop|boarding points|dropping points|passenger info|contact details/i.test(bodyTop)
            || /seat|viewseat|busdetails|selectseat|custinfo|seatlayout|step=/.test(url)
            || document.querySelector('#btnProceed');
        if (!looksLikeCheckout) {
            let viewSeats = 0, viewPrice = 0;
            for (const el of document.querySelectorAll('button, a, [role="button"]')) {
                const tx = (el.innerText || el.textContent || '').toLowerCase();
                if (tx.includes('view seats')) viewSeats++;
                if (tx.includes('view price')) viewPrice++;
            }
            if ((viewSeats >= 2 || viewPrice >= 2) && /bus-tickets|\\/search/.test(url)) return '';
        }
    }

    const vh = window.innerHeight;
    const footerMinY = (stepParam === 'BPDP' || stepParam === 'SEAT' || stepParam === 'SEATS')
        ? vh * 0.45 : vh * 0.55;
    const isBadLabel = (t) => /oops|no buses|no routes|error|sorry|not found|view seats|search bus/i.test(t);
    const isContinueLabel = (t) => {
        t = (t || '').replace(/\\s+/g, ' ').trim();
        if (!t || t.length > 35) return false;
        if (isBadLabel(t)) return false;
        return /^continue$/i.test(t) || /^proceed$/i.test(t) || /^next$/i.test(t)
            || /^select seat(s)? and continue$/i.test(t);
    };
    const canClick = (target, softOk) => {
        if (!target) return false;
        const r = target.getBoundingClientRect();
        if (r.width < 28 || r.height < 10) return false;
        const style = getComputedStyle(target);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity || '1') < 0.12) return false;
        if (style.pointerEvents === 'none') return false;
        if (target.disabled || target.getAttribute('aria-disabled') === 'true') return false;
        const cls = String(target.className || '').toLowerCase();
        if (!softOk && /disabled|inactive|disable/i.test(cls)) return false;
        return true;
    };
    const fireClick = (target) => {
        target.scrollIntoView({ block: 'center', behavior: 'instant' });
        target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        target.click();
    };

    const bp = document.querySelector('#btnProceed, [id="btnProceed"], [data-automation-id="btnProceed"]');
    if (bp && canClick(bp, forceIfReady)) {
        fireClick(bp);
        return 'btnProceed';
    }

    const matches = [];
    for (const el of document.querySelectorAll('button, a, [role="button"]')) {
        const raw = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
            .replace(/\\s+/g, ' ').trim();
        if (!isContinueLabel(raw)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 40 || r.height < 14) continue;
        if (r.bottom < footerMinY) continue;
        if (!canClick(el, forceIfReady)) continue;
        matches.push({ el, t: raw, y: r.bottom });
    }
    matches.sort((a, b) => b.y - a.y);
    if (matches.length) {
        fireClick(matches[0].el);
        return matches[0].t;
    }
    return '';
}
"""


async def _redbus_scan_continue(page) -> dict:
    best: dict = {"candidates": [], "url": "", "frame": "main"}
    pages = [page]
    ctx = page.context
    if ctx:
        pages = list(dict.fromkeys(list(ctx.pages) + [page]))

    scan_js = """
        () => {
            const stepParam = ((new URLSearchParams(location.search).get('step') || '')).toUpperCase();
            const onCheckoutSpa = stepParam === 'BPDP' || stepParam === 'SEAT' || stepParam === 'SEATS'
                || stepParam === 'CUST' || stepParam === 'CUSTINFO' || stepParam === 'PI';
            const bodyTop = (document.body.innerText || '').slice(0, 3000).toLowerCase();
            if (!onCheckoutSpa && /oops|no buses found|no routes available/i.test(bodyTop))
                return { candidates: [], url: location.href };
            const vh = window.innerHeight;
            const footerMinY = vh * 0.55;
            const out = [];
            for (const el of document.querySelectorAll('#btnProceed, button, a, [role="button"]')) {
                const raw = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                    .replace(/\\s+/g, ' ').trim();
                if (!/^continue$|^proceed$|^next$/i.test(raw) && el.id !== 'btnProceed') continue;
                if (/oops|no buses|no routes/i.test(raw)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 14 || r.bottom < footerMinY) continue;
                out.push({ text: raw.slice(0, 40) || el.id, y: Math.round(r.bottom) });
            }
            out.sort((a, b) => b.y - a.y);
            return { candidates: out.slice(0, 8), url: location.href.slice(0, 120) };
        }
    """
    for p in pages:
        kind = await _redbus_page_kind(p)
        if kind in ("search", "error") and _redbus_url_step(p.url or "") not in (
            "board_drop",
            "seats",
            "passenger",
        ):
            continue
        try:
            info = await p.evaluate(scan_js) or {}
            if len(info.get("candidates") or []) > len(best.get("candidates") or []):
                best = {**info, "frame": "main", "page_url": (p.url or "")[:80]}
        except Exception:
            pass
        for i, frame in enumerate(p.frames):
            if frame == p.main_frame:
                continue
            try:
                info = await frame.evaluate(scan_js) or {}
                if info.get("candidates"):
                    best = {**info, "frame": f"iframe-{i}", "page_url": (p.url or "")[:80]}
            except Exception:
                pass
    return best


async def _click_bus_continue_on_page(
    page,
    run: "TravelRun",
    step: str,
    *,
    engine: Optional["PlaywrightEngine"] = None,
    force_if_seats_ready: bool = False,
) -> bool:
    pages = [page]
    if engine and engine.context:
        pages = list(dict.fromkeys(list(engine.context.pages) + [page]))

    for p in pages:
        kind = await _redbus_page_kind(p)
        if kind in ("search", "error") and _redbus_url_step(p.url or "") not in (
            "board_drop",
            "seats",
            "passenger",
        ):
            continue
        for frame in p.frames:
            try:
                clicked = await frame.evaluate(_REDBUS_CONTINUE_CLICK_JS, force_if_seats_ready)
                if clicked:
                    if engine:
                        engine.page = p
                    try:
                        await p.bring_to_front()
                    except Exception:
                        pass
                    tag = "main" if frame == p.main_frame else "iframe"
                    run._log(f"→ Continue ({step}) [{tag}]: {clicked}")
                    await asyncio.sleep(2.5)
                    return True
            except Exception:
                continue
    return False


async def _redbus_active_step(page) -> str:
    """Detect active RedBus checkout step from layout (not Highlights sidebar)."""
    url_step = _redbus_url_step(page.url or "")
    if url_step:
        return url_step
    return await page.evaluate(
        """
        () => {
            const step = ((new URLSearchParams(location.search).get('step') || '')).toUpperCase();
            if (step === 'BPDP') return 'board_drop';
            if (step === 'SEAT' || step === 'SEATS') return 'seats';
            if (step === 'CUST' || step === 'CUSTINFO' || step === 'PI') return 'passenger';
            const body = document.body.innerText.slice(0, 14000);
            const hasSeatLayout = () => {
                if (/\\d+\\s*seat(s)?\\s*selected/i.test(body)) return true;
                for (const el of document.querySelectorAll(
                    '[class*="seat" i], [id*="seat" i], svg, canvas, [class*="berth" i]'
                )) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 180 && r.height > 120 && r.y > 80 && r.y < window.innerHeight * 0.82)
                        return true;
                }
                return /already booked|selected by you|available only for female/i.test(body);
            };
            const timePointColumns = () => {
                let left = 0, right = 0;
                const mid = window.innerWidth * 0.42;
                for (const r of document.querySelectorAll('input[type="radio"], [role="radio"]')) {
                    const rect = r.getBoundingClientRect();
                    if (rect.width < 1 || rect.height < 1 || rect.y < 120) continue;
                    const t = (r.closest('label, li, div')?.innerText || r.getAttribute('aria-label') || '')
                        .replace(/\\s+/g, ' ').trim();
                    if (t.length < 10 || t.length > 140) continue;
                    if (/primo bus|volvo bus|scania|highly on time/i.test(t.toLowerCase())) continue;
                    if (!/\\d{1,2}:\\d{2}/.test(t)) continue;
                    if (rect.x < mid) left++;
                    else right++;
                }
                return { left, right };
            };
            if ((/passenger info/i.test(body) || /passenger\\s*\\d/i.test(body))
                && document.querySelector(
                    'input[type="email"], input[placeholder*="email" i], input[placeholder*="Email" i], '
                    + 'input[placeholder*="name" i], input[placeholder*="Name" i]'
                )
                && /\\bmale\\b/.test(body.toLowerCase()) && /\\bfemale\\b/.test(body.toLowerCase()))
                return 'passenger';
            const cols = timePointColumns();
            if (!hasSeatLayout() && cols.left >= 1 && cols.right >= 1) return 'board_drop';
            if (hasSeatLayout()) return 'seats';
            for (const el of document.querySelectorAll(
                '[class*="step" i], [class*="progress" i], nav, header'
            )) {
                const r = el.getBoundingClientRect();
                if (r.y > 220 || r.height < 20 || r.height > 120) continue;
                const txt = (el.innerText || '').replace(/\\s+/g, ' ');
                if (!/select seats|board\\/drop|passenger info/i.test(txt)) continue;
                for (const node of el.querySelectorAll('span, div, li, p')) {
                    const t = (node.innerText || node.textContent || '').trim();
                    if (!t) continue;
                    const wrap = node.closest('div, li') || node;
                    const cls = String(wrap.className || '').toLowerCase();
                    const style = getComputedStyle(wrap);
                    const color = style.color || '';
                    const border = style.borderBottom || style.borderBottomColor || '';
                    const isRed = /216|214|220|53|239|244|63|ef4444|dc2626/i.test(color + border + cls);
                    const isActive = cls.includes('active') || cls.includes('current')
                        || cls.includes('selected') || isRed;
                    if (!isActive) continue;
                    if (/passenger info/i.test(t)) return 'passenger';
                    if (/board\\/drop/i.test(t)) return 'board_drop';
                    if (/select seats/i.test(t)) return 'seats';
                }
            }
            if (/view seats|bus search|buses from|view price/i.test(body)) return 'search';
            return 'unknown';
        }
        """
    )


async def _redbus_booking_step(page) -> str:
    """Detect RedBus checkout step (progress bar first, then content)."""
    url_step = _redbus_url_step(page.url or "")
    if url_step:
        return url_step
    step = await _redbus_active_step(page)
    if step != "unknown":
        return step
    return await page.evaluate(
        """
        () => {
            const step = ((new URLSearchParams(location.search).get('step') || '')).toUpperCase();
            if (step === 'BPDP') return 'board_drop';
            if (step === 'SEAT' || step === 'SEATS') return 'seats';
            if (step === 'CUST' || step === 'CUSTINFO' || step === 'PI') return 'passenger';
            const t = document.body.innerText.slice(0, 12000);
            if (/passenger info/i.test(t)) return 'passenger';
            if (/board\\/drop point/i.test(t)) return 'board_drop';
            if (/select seats|already booked|selected by you/i.test(t)) return 'seats';
            if (/view seats|bus search|buses from|view price/i.test(t)) return 'search';
            return 'unknown';
        }
        """
    )


async def _count_seats_selected(page) -> int:
    return int(
        await page.evaluate(
            """
            () => {
                const body = document.body.innerText || '';
                for (const re of [
                    /(\\d+)\\s*seat(s)?\\s*selected/i,
                    /(\\d+)\\s*seat(s)?\\s*chosen/i,
                    /selected\\s*[:\\-]?\\s*(\\d+)\\s*seat/i,
                    /(\\d+)\\s*seat(s)?\\s*added/i,
                    /you\\s*selected\\s*(\\d+)/i,
                ]) {
                    const m = body.match(re);
                    if (m) return parseInt(m[1], 10);
                }
                let n = 0;
                for (const el of document.querySelectorAll(
                    '[class*="selected" i][class*="seat" i], [class*="Selected" i], '
                    + '[data-selected="true"], [aria-selected="true"][class*="seat" i]'
                )) {
                    const cls = String(el.className || '').toLowerCase();
                    if (cls.includes('booked') || cls.includes('legend') || cls.includes('row')) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= 12 && r.height >= 12) n++;
                }
                if (n) return n;
                const footer = body.slice(-2500);
                const nums = footer.match(/\\b([UL][0-9]{1,2}|[0-9]{1,2}[UL]?)\\b/g) || [];
                const uniq = [...new Set(nums.map((s) => s.toUpperCase()))];
                if (uniq.length && /fare|total|₹|rs\\.?/i.test(footer)) return uniq.length;
                return 0;
            }
            """
        )
        or 0
    )


async def _auto_pick_redbus_seats(page, run: "TravelRun", passenger_count: int) -> int:
    """Click available seats when user confirmed but DOM count is still 0."""
    picked = int(
        await page.evaluate(
            """
            (need) => {
                const isBlocked = (el) => {
                    const cls = String(el.className || '').toLowerCase();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const t = (el.innerText || '').toLowerCase();
                    return /booked|sold|reserved|blocked|unavailable|occupied|already|disabled|legend|driver|door|steering|empty|row-label|deck-label/i
                        .test(cls + aria + t);
                };
                const seats = [];
                for (const el of document.querySelectorAll(
                    '[class*="seat" i], [data-seat], [id*="seat" i], .availableSeat, '
                    + 'li[class*="berth" i], [class*="berth" i], canvas + div, svg rect, svg g'
                )) {
                    if (isBlocked(el)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8 || r.width > 90 || r.height > 90) continue;
                    if (r.y < 80 || r.y > window.innerHeight * 0.82) continue;
                    const cls = String(el.className || '').toLowerCase();
                    const style = getComputedStyle(el);
                    const bg = style.backgroundColor || '';
                    const fill = style.fill || '';
                    const score = (/available|vacant|selectable|green|open/i.test(cls + bg + fill) ? 3 : 1);
                    seats.push({ el, score, y: r.y, x: r.x });
                }
                seats.sort((a, b) => b.score - a.score || a.y - b.y || a.x - b.x);
                let n = 0;
                for (const s of seats) {
                    if (n >= need) break;
                    try {
                        s.el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        s.el.click();
                        n++;
                    } catch (e) {}
                }
                return n;
            }
            """,
            passenger_count,
        )
        or 0
    )
    if picked:
        run._log(f"→ Auto-selected {picked} available seat(s) in Chrome")
        await asyncio.sleep(1.2)
    return picked


async def _wait_redbus_continue_enabled(
    page, run: "TravelRun", timeout: float = 20, *, allow_soft_disabled: bool = False
) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        scan = await _redbus_scan_continue(page)
        candidates = scan.get("candidates") or []
        if candidates:
            run._log(f"→ Continue button ready: {candidates[0].get('text', 'Continue')[:50]}")
            return True
        if allow_soft_disabled:
            return True
        await asyncio.sleep(0.5)
    return False


async def _click_bus_continue(
    page,
    run: "TravelRun",
    step: str,
    *,
    engine: Optional["PlaywrightEngine"] = None,
    force_if_seats_ready: bool = False,
) -> bool:
    if engine and "board/drop" in step:
        page = await _ensure_board_drop_checkout_page(engine, run, page, timeout=12) or page
        if not page and _redbus_url_step((engine.page or page).url or "") == "board_drop":
            page = engine.page or page
    elif engine:
        page = await _focus_redbus_checkout_page(engine, run)
    target = engine.page if engine else page
    await target.evaluate(
        """
        () => {
            window.scrollTo(0, document.body.scrollHeight);
            for (const sel of [
                '[class*="footer" i]', '[class*="sticky" i]', '[class*="proceed" i]',
                '[class*="continue" i]', '[class*="book" i][class*="bar" i]',
            ]) {
                for (const el of document.querySelectorAll(sel)) {
                    try { el.scrollIntoView({ block: 'end', behavior: 'instant' }); } catch (e) {}
                }
            }
        }
        """
    )
    await asyncio.sleep(0.5)

    if await _click_bus_continue_on_page(
        target, run, step, engine=engine, force_if_seats_ready=force_if_seats_ready
    ):
        return True

    scan = await _redbus_scan_continue(target)
    labels = [c.get("text", "?")[:30] for c in (scan.get("candidates") or [])[:4]]
    page_hint = scan.get("page_url") or scan.get("url") or (target.url or "")[:60]
    run._log(
        f"⚠ Continue button not found or disabled ({step})"
        + (f" — footer scan: {labels}" if labels else " — no Continue text in footer")
        + f" — page: {page_hint}"
        + (f" frame={scan.get('frame')}" if scan.get("frame") else "")
    )
    return False


async def _advance_redbus_to_board_drop(
    page,
    run: "TravelRun",
    wait_human: Callable[[str], Awaitable[str]],
    passenger_count: int = 1,
    *,
    engine: Optional["PlaywrightEngine"] = None,
    user_confirmed_seats: bool = False,
) -> bool:
    """After seats picked, auto-click Continue until Board/Drop step."""
    if engine:
        page = await _focus_redbus_checkout_page(engine, run)
    url_step = _redbus_url_step(page.url or "")
    if url_step == "board_drop" or await _redbus_has_live_board_drop(page):
        run._log(f"✅ Board/Drop step active (url step={url_step or 'BPDP UI'})")
        if engine:
            engine.page = page
        return True
    if engine:
        page = await _wait_redbus_seat_page(engine, run, page, timeout=20)
        page = await _focus_redbus_checkout_page(engine, run)
        kind = await _redbus_page_kind(page)
        run._log(f"→ Checkout context: {(page.url or '')[:78]} (kind={kind})")
        if kind in ("search", "error"):
            run._log("⚠ Seat/checkout tab missing — keep **Select seats** tab open in Chrome")
            page = await _wait_redbus_seat_page(engine, run, page, timeout=25)
            kind = await _redbus_page_kind(page)
            # RedBus is a SPA and can temporarily look like "search" while the checkout shell loads
            # (no ?step= yet, partial DOM). Avoid hard-failing if we see any checkout signals.
            if kind in ("search", "error"):
                try:
                    url = (page.url or "").lower()
                    url_step = _redbus_url_step(page.url or "")
                    scan = await _redbus_scan_continue(page)
                    has_continue = bool((scan.get("candidates") or [])[:1])
                    has_btn_proceed = bool(
                        await page.evaluate("() => !!document.querySelector('#btnProceed')")
                    )
                    looks_like_checkout = (
                        url_step in ("seats", "board_drop", "passenger")
                        or re.search(r"seat|viewseat|booking|busdetails|selectseat|custinfo|seatlayout|step=", url)
                        or has_continue
                        or has_btn_proceed
                    )
                    if not looks_like_checkout:
                        run._log("❌ Still on search/error page — cannot click Continue safely")
                        return False
                    run._log("ℹ️ Checkout signals detected despite kind=search/error — continuing")
                except Exception:
                    run._log("❌ Still on search/error page — cannot click Continue safely")
                    return False
    seats = await _count_seats_selected(page)
    if seats < passenger_count:
        run._log(f"⚠ Detected {seats} seat(s) — waiting for {passenger_count} in Chrome…")
        for _ in range(20):
            seats = await _count_seats_selected(page)
            if seats >= passenger_count:
                run._log(f"→ {seats} seat(s) detected in Chrome")
                break
            await asyncio.sleep(0.5)

    if seats < passenger_count and user_confirmed_seats:
        await _auto_pick_redbus_seats(page, run, passenger_count)
        seats = await _count_seats_selected(page)
        if seats >= passenger_count:
            run._log(f"→ {seats} seat(s) after auto-pick")

    force_continue = seats >= passenger_count or user_confirmed_seats

    if not await _wait_redbus_continue_enabled(
        page, run, timeout=12, allow_soft_disabled=force_continue
    ):
        if user_confirmed_seats:
            run._log("→ Continue looks inactive — trying click anyway (seats confirmed)")
        else:
            run._log("⚠ Continue is disabled — pick the required seat(s) on the layout first")
            await ask_tagged(
                wait_human,
                TAG_CONFIRM_DONE,
                f"Pick **{passenger_count} green seat(s)** on the layout so **Continue** turns red/active, then confirm.",
                "Seats picked",
            )
            user_confirmed_seats = True
            await _auto_pick_redbus_seats(page, run, passenger_count)
            await _wait_redbus_continue_enabled(page, run, timeout=15, allow_soft_disabled=True)
            force_continue = True

    if not await _click_bus_continue(
        page, run, "seats→board/drop", engine=engine, force_if_seats_ready=force_continue
    ):
        run._log("⚠ First Continue click failed — waiting and retrying")
        await asyncio.sleep(2)
        await _click_bus_continue(
            page,
            run,
            "seats→board/drop retry",
            engine=engine,
            force_if_seats_ready=force_continue,
        )

    deadline = asyncio.get_event_loop().time() + 35
    while asyncio.get_event_loop().time() < deadline:
        if engine:
            page = engine.page or page
        step = await _redbus_booking_step(page)
        if step == "board_drop":
            run._log("✅ Chrome advanced to **Board/Drop point** step")
            return True
        if step == "passenger":
            run._log("ℹ️ Already on Passenger Info step")
            return True
        if step == "seats":
            kind = await _redbus_page_kind(page)
            if kind in ("search", "error"):
                if engine:
                    page = await _focus_redbus_checkout_page(engine, run)
                    if await _redbus_page_kind(page) in ("search", "error"):
                        run._log("⚠ Aborting Continue retries — not on checkout tab")
                        break
            await _click_bus_continue(
                page,
                run,
                "seats→board/drop retry",
                engine=engine,
                force_if_seats_ready=force_continue,
            )
            await asyncio.sleep(1.2)
            continue

    step = await _redbus_booking_step(page)
    if step in ("board_drop", "passenger"):
        return True

    run._log("⚠ Auto-advance to Board/Drop timed out")
    return False


def _board_drop_pref_parts(pref: str) -> tuple[str, list[str]]:
    """Split form pref into optional time and location keywords."""
    pref = (pref or "").strip()
    time_tok = ""
    keywords: list[str] = []
    for part in re.split(r"[,;|]", pref):
        part = part.strip()
        if not part:
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}", part):
            time_tok = part
        elif len(part) >= 3:
            keywords.append(part)
    if not keywords and pref:
        keywords = [pref]
    return time_tok, keywords


def _flexible_location_regex(keyword: str) -> re.Pattern[str]:
    """Regex tolerant of spelling variants (Gandipuram vs Gandhipuram)."""
    kw = keyword.strip().lower()
    if len(kw) < 4:
        return re.compile(re.escape(kw), re.I)
    return re.compile(rf"{re.escape(kw[:4])}\w*{re.escape(kw[4:])}", re.I)


_REDBUS_BPDP_JUNK_TEXT = re.compile(
    r"board\s*/\s*drop\s*point|select\s+boarding|select\s+dropping|"
    r"^boarding\s*point$|^dropping\s*point$|passenger\s*info|select\s+seats|"
    r"highlights|view\s+price|bus\s+operator",
    re.I,
)


async def _scroll_redbus_drop_column(page) -> None:
    await page.evaluate(
        """
        () => {
            const scrollNode = (labelRe) => {
                for (const h of document.querySelectorAll('h1,h2,h3,h4,div,span,p')) {
                    const t = (h.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!labelRe.test(t) || t.length > 50) continue;
                    let node = h.parentElement;
                    for (let i = 0; i < 8 && node; i++) {
                        if (node.scrollHeight > node.clientHeight + 25) {
                            node.scrollTop = Math.min(node.scrollTop + 320, node.scrollHeight);
                            return true;
                        }
                        node = node.parentElement;
                    }
                }
                return false;
            };
            scrollNode(/dropping point/i);
            const mid = window.innerWidth * 0.48;
            for (const el of document.querySelectorAll('div, ul, section')) {
                const r = el.getBoundingClientRect();
                if (r.x < mid || r.width < 180) continue;
                if (el.scrollHeight > el.clientHeight + 25) {
                    el.scrollTop = Math.min(el.scrollTop + 280, el.scrollHeight);
                }
            }
        }
        """
    )
    await asyncio.sleep(0.4)


async def _pick_redbus_board_drop_points(
    page,
    run: "TravelRun",
    *,
    boarding_pref: str = "",
    dropping_pref: str = "",
) -> tuple[str, str]:
    """Select boarding/dropping rows on RedBus Board/Drop step (two columns)."""
    boarding_pref = (boarding_pref or "").strip()
    dropping_pref = (dropping_pref or "").strip()
    await _scroll_redbus_drop_column(page)
    result = await page.evaluate(
        """
        ([boardingPref, droppingPref]) => {
            const normalize = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const scoreMatch = (text, pref) => {
                if (!pref) return 0;
                const t = (text || '').toLowerCase();
                const parts = pref.toLowerCase().split(/[,;|]/).map((s) => s.trim()).filter(Boolean);
                let score = 0;
                for (const part of parts) {
                    if (/^\\d{1,2}:\\d{2}$/.test(part) && t.includes(part)) score += 5;
                    else if (part.length >= 3 && t.includes(part)) score += 4;
                    else {
                        const pn = normalize(part);
                        const tn = normalize(t);
                        if (pn.length >= 4 && tn.includes(pn)) score += 4;
                        for (let len = Math.min(pn.length, 10); len >= 4; len--) {
                            for (let i = 0; i <= pn.length - len; i++) {
                                if (tn.includes(pn.slice(i, i + len))) score = Math.max(score, len);
                            }
                        }
                    }
                }
                return score;
            };
            const isJunkRow = (t) => {
                const tl = (t || '').toLowerCase();
                if (t.length < 10 || t.length > 280) return true;
                if (/^board\\/drop\\s*point$/i.test(t)) return true;
                if (/select boarding|select dropping|^boarding point$|^dropping point$/i.test(tl)) return true;
                if (/primo bus|highly on time|bus duration|bus service|avg\\.|\\bmin(s)?\\b|coimbatore to bangalore/i.test(tl)) return true;
                return false;
            };
            const parseRow = (el) => {
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (isJunkRow(t)) return null;
                const r = el.getBoundingClientRect();
                if (r.width < 80 || r.height < 24 || r.y < 150 || r.y > window.innerHeight * 0.88) return null;
                return { el, t, x: r.x + r.width / 2, y: r.y };
            };
            const findColumnRoot = (labelRe) => {
                for (const h of document.querySelectorAll('h1,h2,h3,h4,div,span,p')) {
                    const ht = (h.innerText || h.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!labelRe.test(ht) || ht.length > 55) continue;
                    let node = h.parentElement;
                    for (let i = 0; i < 7 && node; i++) {
                        const r = node.getBoundingClientRect();
                        if (r.width > 200 && r.height > 100) return node;
                        node = node.parentElement;
                    }
                }
                return null;
            };
            const collectRows = (root) => {
                const rows = [];
                const scope = root || document.body;
                for (const el of scope.querySelectorAll('li, label, div, [role="radio"], [role="listitem"]')) {
                    const row = parseRow(el);
                    if (!row) continue;
                    if (rows.some((r) => Math.abs(r.y - row.y) < 10 && r.t.slice(0, 35) === row.t.slice(0, 35))) continue;
                    rows.push(row);
                }
                rows.sort((a, b) => a.y - b.y);
                return rows;
            };
            const clickRow = (row) => {
                row.el.scrollIntoView({ block: 'center', behavior: 'instant' });
                const radio = row.el.querySelector('input[type="radio"], [role="radio"]');
                if (radio) {
                    radio.click();
                    radio.dispatchEvent(new Event('change', { bubbles: true }));
                }
                for (const c of row.el.querySelectorAll('span, div, i, svg, button')) {
                    const r = c.getBoundingClientRect();
                    if (r.width >= 14 && r.width <= 34 && r.height >= 14 && r.height <= 34) {
                        c.click();
                        return;
                    }
                }
                row.el.click();
            };
            const pickInRows = (rows, pref, requireScore) => {
                if (!rows.length) return null;
                let best = null;
                let bestScore = -1;
                for (const row of rows) {
                    const s = scoreMatch(row.t, pref);
                    if (s > bestScore) { bestScore = s; best = row; }
                }
                if (!best) return null;
                if (requireScore && pref && bestScore <= 0) {
                    const timed = rows.filter((r) => /\\d{1,2}:\\d{2}/.test(r.t));
                    if (timed.length) {
                        best = timed[0];
                        bestScore = 0;
                    } else {
                        return null;
                    }
                } else if (!pref) {
                    best = rows.find((r) => /\\d{1,2}:\\d{2}/.test(r.t)) || rows[0];
                    bestScore = scoreMatch(best.t, pref);
                }
                clickRow(best);
                return { t: best.t.slice(0, 100), score: bestScore };
            };
            const scrollDropCol = () => {
                for (const h of document.querySelectorAll('h1,h2,h3,h4,div,span,p')) {
                    const t = (h.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!/dropping point/i.test(t) || t.length > 50) continue;
                    let node = h.parentElement;
                    for (let i = 0; i < 8 && node; i++) {
                        if (node.scrollHeight > node.clientHeight + 25) {
                            node.scrollTop = node.scrollHeight;
                            return;
                        }
                        node = node.parentElement;
                    }
                }
            };

            const mid = window.innerWidth * 0.42;
            const boardRoot = findColumnRoot(/boarding\\s*point/i);
            const dropRoot = findColumnRoot(/dropping\\s*point/i);
            let boardRows = collectRows(boardRoot);
            let dropRows = collectRows(dropRoot);
            const all = collectRows(document.body);
            if (!boardRows.length) boardRows = all.filter((r) => r.x < mid);
            if (!dropRows.length) dropRows = all.filter((r) => r.x >= mid);

            const b = pickInRows(boardRows, boardingPref, true);
            scrollDropCol();
            dropRows = collectRows(dropRoot);
            if (!dropRows.length) dropRows = collectRows(document.body).filter((r) => r.x >= mid);
            const d = pickInRows(dropRows, droppingPref, true);
            return {
                boarding: b?.t || '',
                dropping: d?.t || '',
                boardingScore: b?.score || 0,
                droppingScore: d?.score || 0,
                boardRows: boardRows.length,
                dropRows: dropRows.length,
            };
        }
        """,
        [boarding_pref, dropping_pref],
    )
    boarding = ((result or {}).get("boarding") or "").strip()
    dropping = ((result or {}).get("dropping") or "").strip()
    b_score = int((result or {}).get("boardingScore") or 0)
    d_score = int((result or {}).get("droppingScore") or 0)
    if boarding_pref:
        run._log(
            f"→ Boarding (form: {boarding_pref[:45]}): {boarding[:70]}"
            + (f" [match={b_score}]" if b_score else " [fallback]")
            + f" ({(result or {}).get('boardRows', 0)} options)"
        )
    elif boarding:
        run._log(f"→ Boarding: {boarding[:75]}")
    if dropping_pref:
        run._log(f"→ Dropping (form: {dropping_pref[:45]}): {dropping[:70]}" + (f" [match={d_score}]" if d_score else " [fallback]"))
    elif dropping:
        run._log(f"→ Dropping: {dropping[:75]}")
    return boarding, dropping


async def _pick_redbus_board_drop_playwright(
    page,
    run: "TravelRun",
    *,
    boarding_pref: str = "",
    dropping_pref: str = "",
) -> None:
    """Playwright fallback — fuzzy match within left/right columns."""
    viewport = page.viewport_size or {"width": 1366, "height": 768}
    mid_x = (viewport.get("width") or 1366) / 2

    async def _click_side(pref: str, label: str, left_side: bool) -> bool:
        pref = (pref or "").strip()
        if not pref:
            return False
        _, keywords = _board_drop_pref_parts(pref)
        loc_keywords = [k for k in keywords if not re.fullmatch(r"\d{1,2}:\d{2}", k)]
        if not loc_keywords:
            loc_keywords = [pref]
        loc_keywords.sort(key=len, reverse=True)

        if not left_side:
            await _scroll_redbus_drop_column(page)

        for kw in loc_keywords:
            pat = _flexible_location_regex(kw)
            loc = page.locator("li, div, label, [role='radio']").filter(has_text=pat)
            count = await loc.count()
            for i in range(min(count, 12)):
                try:
                    target = loc.nth(i)
                    if not await target.is_visible():
                        continue
                    box = await target.bounding_box()
                    if not box:
                        continue
                    cx = box["x"] + box["width"] / 2
                    if left_side and cx > mid_x:
                        continue
                    if not left_side and cx < mid_x:
                        continue
                    text = (await target.inner_text()).replace("\n", " ")[:100]
                    if _REDBUS_BPDP_JUNK_TEXT.search(text) or re.search(
                        r"bus duration|bus service|avg\.|coimbatore to bangalore|\bmin(s)?\b",
                        text,
                        re.I,
                    ):
                        continue
                    await target.scroll_into_view_if_needed()
                    radio = target.locator('[role="radio"], input[type="radio"]')
                    if await radio.count() > 0:
                        await radio.first.click(force=True)
                    else:
                        await target.click(force=True)
                    run._log(f"→ Playwright {label}: {kw[:40]} → {text[:55]}")
                    await asyncio.sleep(0.5)
                    return True
                except Exception:
                    continue
        return False

    await _click_side(boarding_pref, "boarding", left_side=True)
    await _click_side(dropping_pref, "dropping", left_side=False)


async def _wait_user_seat_selection(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    passenger_count: int,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    """User picks seats in Chrome, then we advance to Board/Drop step."""
    run._phase("seats")
    page = await _focus_redbus_checkout_page(engine, run)
    run._log(f"📍 Step 1/3: Select **{passenger_count} seat(s)** in Chrome — tab: {(page.url or '')[:70]}")
    await screenshot(engine, run)
    await ask_tagged(
        wait_human,
        TAG_CONFIRM_DONE,
        f"On the **seat layout**, pick **{passenger_count} green seat(s)**. "
        "Do **not** confirm here until seats are selected — then click **✅ Seats picked** below.",
        "Seats picked",
    )
    run._log("✅ Seat selection confirmed — advancing to Board/Drop step")
    page = await _focus_redbus_checkout_page(engine, run)
    run._log(f"→ Active tab before Continue: {(page.url or '')[:78]}")
    advanced = await _advance_redbus_to_board_drop(
        page,
        run,
        wait_human,
        passenger_count=passenger_count,
        engine=engine,
        user_confirmed_seats=True,
    )
    if not advanced:
        run._log("⚠ Still on Select seats — retrying Continue in Chrome")
        page = await _focus_redbus_checkout_page(engine, run)
        await _advance_redbus_to_board_drop(
            page,
            run,
            wait_human,
            passenger_count=passenger_count,
            engine=engine,
            user_confirmed_seats=True,
        )
    await screenshot(engine, run)


async def _apply_redbus_type_filter(page, run: "TravelRun", bus_type: str) -> None:
    bus_type = (bus_type or "any").lower()
    if bus_type == "any":
        return
    label = "Sleeper" if bus_type == "sleeper" else "Seater"
    for loc in (
        page.get_by_role("button", name=re.compile(rf"^{label}$", re.I)),
        page.get_by_role("tab", name=re.compile(label, re.I)),
        page.locator(f'button:has-text("{label}")'),
        page.locator(f'label:has-text("{label}")'),
    ):
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click(force=True)
                run._log(f"→ Filter applied: {label}")
                await asyncio.sleep(2)
                return
        except Exception:
            continue


async def _open_redbus_seat_page(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    *,
    preferred_bus: str,
    bus_type: str,
):
    """Click View seats and switch Playwright to the seat/checkout tab."""
    context = engine.context
    if not context:
        ok = await _select_redbus_bus(
            page, run, preferred_bus=preferred_bus, bus_type=bus_type
        )
        return page, ok

    async def _trigger_view_seats() -> bool:
        if await _select_redbus_bus(page, run, preferred_bus=preferred_bus, bus_type=bus_type):
            return True
        for loc in (
            page.get_by_role("button", name=re.compile(r"view seats", re.I)),
            page.locator('button:has-text("View seats")'),
            page.locator('a:has-text("View seats")'),
        ):
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click(force=True, timeout=5000)
                    run._log("→ Clicked View seats (Playwright)")
                    return True
            except Exception:
                continue
        return False

    try:
        async with context.expect_page(timeout=15000) as pinfo:
            await _trigger_view_seats()
        new_page = await pinfo.value
        await new_page.wait_for_load_state("domcontentloaded", timeout=25000)
        engine.page = new_page
        run._log(f"→ Seat page opened (new tab): {(new_page.url or '')[:78]}")
        return new_page, True
    except Exception:
        pass

    try:
        await _trigger_view_seats()
        await page.wait_for_url(
            re.compile(r"seat|viewseat|booking|busdetails|selectseat|custinfo", re.I),
            timeout=15000,
        )
        engine.page = page
        run._log(f"→ Seat page URL: {(page.url or '')[:78]}")
        return page, True
    except Exception:
        pass

    page = await _wait_redbus_seat_page(engine, run, page, timeout=20)
    step = await _redbus_booking_step(page)
    ok = step in ("seats", "board_drop", "passenger")
    if ok:
        run._log(f"→ Seat checkout detected (step={step})")
    return page, ok


async def _select_redbus_bus(
    page,
    run: "TravelRun",
    *,
    preferred_bus: str,
    bus_type: str,
) -> bool:
    preferred = (preferred_bus or "").strip().lower()
    bus_type = (bus_type or "any").lower()

    # RedBus listings are often lazily rendered; the "card text contains View seats" heuristic
    # is brittle. Prefer clicking the best visible "View seats" / "Select seats" control and
    # scroll/retry to trigger lazy load.
    for attempt in range(6):
        try:
            picked = await page.evaluate(
                """
                ([preferred, busType, attempt]) => {
                    const norm = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                    const typeOk = (text) => {
                        const t = norm(text);
                        if (busType === 'any') return true;
                        if (busType === 'sleeper') return t.includes('sleeper');
                        if (busType === 'seater') return t.includes('seater') || t.includes('semi');
                        return true;
                    };
                    const btnOk = (t) => /view\\s*seats|select\\s*seats|view\\s*seat|select\\s*seat/i.test(t || '');
                    const canClick = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width < 30 || r.height < 12) return false;
                        const style = getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') return false;
                        if (parseFloat(style.opacity || '1') < 0.12) return false;
                        if (style.pointerEvents === 'none') return false;
                        if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                        return true;
                    };
                    const cardRoot = (el) => {
                        const direct = el.closest('li, article, [role="listitem"], [data-automation-id*="bus" i], div[class*="bus" i]');
                        if (direct) return direct;
                        let n = el.parentElement;
                        for (let i = 0; i < 8 && n; i++) {
                            const t = norm(n.innerText || '');
                            if (t.length >= 30 && t.length <= 1600) return n;
                            n = n.parentElement;
                        }
                        return el.parentElement || el;
                    };

                    // Collect candidate buttons across the page (not inside guessed cards).
                    const out = [];
                    for (const el of document.querySelectorAll('button, a, [role="button"], span, div')) {
                        const raw = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
                        if (!raw || raw.length > 40) continue;
                        if (!btnOk(raw)) continue;
                        if (!canClick(el)) continue;
                        const root = cardRoot(el);
                        const cardText = norm(root.innerText || '');
                        if (!typeOk(cardText)) continue;
                        const prefHit = preferred && cardText.includes(preferred) ? 1 : 0;
                        const r = el.getBoundingClientRect();
                        const y = r.top;
                        out.push({ el, raw: raw.slice(0, 25), prefHit, y, cardText: cardText.slice(0, 120) });
                    }
                    out.sort((a, b) => (b.prefHit - a.prefHit) || (a.y - b.y));
                    if (!out.length) return '';
                    const pick = out[0];
                    try {
                        pick.el.scrollIntoView({ block: 'center', behavior: 'instant' });
                        pick.el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        pick.el.click();
                        return pick.cardText || pick.raw;
                    } catch (e) {
                        return '';
                    }
                }
                """,
                [preferred, bus_type, attempt],
            )
            if picked:
                run._log(f"→ Bus selected: {str(picked)[:80]}")
                return True
        except Exception:
            pass

        # Playwright fallbacks (handles cases where JS couldn't see text due to virtualization).
        for loc in (
            page.get_by_role("button", name=re.compile(r"view seats|select seats", re.I)),
            page.locator('button:has-text("View seats")'),
            page.locator('a:has-text("View seats")'),
            page.locator('[data-automation-id="select-bus"]'),
        ):
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click(force=True, timeout=6000)
                    run._log("→ Opened available bus (View seats)")
                    return True
            except Exception:
                continue

        # Trigger lazy-load by scrolling the results list.
        try:
            await page.evaluate(
                """
                (attempt) => {
                    const vh = window.innerHeight || 800;
                    window.scrollBy(0, Math.max(420, Math.floor(vh * 0.75)));
                    // Nudge any scrollable results container too.
                    for (const el of document.querySelectorAll('div, ul')) {
                        if (el.scrollHeight > el.clientHeight + 40) {
                            const cls = String(el.className || '').toLowerCase();
                            if (/result|bus|listing|list|route|inventory/.test(cls)) {
                                el.scrollTop += Math.max(420, Math.floor(vh * 0.75));
                                break;
                            }
                        }
                    }
                }
                """,
                attempt,
            )
        except Exception:
            pass
        await asyncio.sleep(0.9 if attempt < 2 else 1.2)

    return False


async def _verify_board_drop_selected(page) -> tuple[bool, int, list[str]]:
    """Need one real boarding (left) and one real dropping (right) selection."""
    info = await page.evaluate(
        """
        () => {
            const junk = (t) => {
                t = (t || '').replace(/\\s+/g, ' ').trim();
                const tl = t.toLowerCase();
                if (t.length < 12 || t.length > 200) return true;
                if (/^board\\/drop\\s*point$/i.test(t)) return true;
                if (/select boarding|select dropping|^boarding point$|^dropping point$/i.test(tl)) return true;
                if (/primo bus|bus duration|bus service|avg\\.|coimbatore to bangalore/i.test(tl)) return true;
                return false;
            };
            const mid = window.innerWidth * 0.42;
            const left = [];
            const right = [];
            const addFromRadio = (el) => {
                const row = el.closest('li, label, div');
                if (!row) return;
                const t = (row.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
                if (junk(t)) return;
                const r = row.getBoundingClientRect();
                const cx = r.x + r.width / 2;
                const item = { t: t.slice(0, 100), cx };
                if (cx < mid) left.push(item);
                else right.push(item);
            };
            for (const el of document.querySelectorAll(
                'input[type="radio"]:checked, [role="radio"][aria-checked="true"]'
            )) {
                addFromRadio(el);
            }
            const dedupe = (arr) => {
                const seen = new Set();
                return arr.filter((p) => {
                    const k = p.t.toLowerCase();
                    if (seen.has(k)) return false;
                    seen.add(k);
                    return true;
                });
            };
            const L = dedupe(left);
            const R = dedupe(right);
            const texts = [...L, ...R].map((p) => p.t);
            return {
                ok: L.length >= 1 && R.length >= 1,
                left: L.length,
                right: R.length,
                texts,
            };
        }
        """
    )
    texts = list((info or {}).get("texts") or [])
    left_n = int((info or {}).get("left", 0) or 0)
    right_n = int((info or {}).get("right", 0) or 0)
    ok = bool((info or {}).get("ok")) and left_n >= 1 and right_n >= 1
    return ok, left_n + right_n, texts


async def _auto_board_drop_points(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    wait_human: Callable[[str], Awaitable[str]],
    passenger_count: int = 1,
    *,
    boarding_point: str = "",
    dropping_point: str = "",
    contact_mobile: str = "",
) -> None:
    run._phase("board_drop")
    run._log("📍 Step 2/3: Boarding & dropping points (from Streamlit form)")
    page = await _focus_redbus_checkout_page(engine, run)
    await ensure_redbus_logged_in(page, run, wait_human, mobile=contact_mobile)
    boarding_point = (boarding_point or "").strip()
    dropping_point = (dropping_point or "").strip()
    if boarding_point or dropping_point:
        run._log(f"→ Form prefs — boarding: **{boarding_point[:50]}** | dropping: **{dropping_point[:50]}**")

    page = await _ensure_board_drop_checkout_page(engine, run, page, timeout=30)
    if not page and engine and engine.context:
        for p in engine.context.pages:
            if _redbus_url_step(p.url or "") == "board_drop" or await _redbus_has_live_board_drop(p):
                page = p
                engine.page = p
                try:
                    await p.bring_to_front()
                except Exception:
                    pass
                run._log(f"→ Board/Drop tab (fallback): {(p.url or '')[:78]}")
                break
    if not page:
        await screenshot(engine, run)
        run._log(
            "❌ Keep the **Board/Drop point** Chrome tab open (not search results). "
            "Complete seat step there, then re-run if needed."
        )
        return

    step = await _redbus_booking_step(page)
    if step == "seats":
        run._log("⚠ Still on **Select seats** — clicking Continue to reach Board/Drop step")
        await _advance_redbus_to_board_drop(
            page,
            run,
            wait_human,
            passenger_count=passenger_count,
            engine=engine,
            user_confirmed_seats=True,
        )
        page = await _ensure_board_drop_checkout_page(engine, run, page, timeout=20) or page

    step = await _redbus_booking_step(page)
    if step not in ("board_drop", "passenger"):
        run._log("⚠ Board/Drop step not detected — retrying Continue on checkout tab")
        await _advance_redbus_to_board_drop(
            page,
            run,
            wait_human,
            passenger_count=passenger_count,
            engine=engine,
            user_confirmed_seats=True,
        )
        page = await _ensure_board_drop_checkout_page(engine, run, page, timeout=20) or page
        step = await _redbus_booking_step(page)

    if step == "passenger":
        run._log("ℹ️ Already on Passenger Info — board/drop skipped")
        return

    if step != "board_drop" or await _redbus_page_kind(page) == "error":
        if _redbus_url_step(page.url or "") == "board_drop" or await _redbus_has_live_board_drop(page):
            step = "board_drop"
        else:
            run._log(f"❌ Not on Board/Drop UI (step={step}) — aborting point selection")
            await screenshot(engine, run)
            return

    await asyncio.sleep(1.0)
    checked = 0
    labels: list[str] = []
    for attempt in range(5):
        page = await _ensure_board_drop_checkout_page(engine, run, page, timeout=8) or page
        engine.page = page
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.4)
        await _pick_redbus_board_drop_points(
            page,
            run,
            boarding_pref=boarding_point,
            dropping_pref=dropping_point,
        )
        await asyncio.sleep(0.6)
        ok, checked, labels = await _verify_board_drop_selected(page)
        if ok:
            break
        await _pick_redbus_board_drop_playwright(
            page,
            run,
            boarding_pref=boarding_point,
            dropping_pref=dropping_point,
        )
        await _scroll_redbus_drop_column(page)
        await asyncio.sleep(0.5)
        # Dropping column is often below the fold — retry drop side only.
        if dropping_point:
            await _pick_redbus_board_drop_playwright(
                page,
                run,
                boarding_pref="",
                dropping_pref=dropping_point,
            )
        await _pick_redbus_board_drop_points(
            page,
            run,
            boarding_pref=boarding_point if not ok else "",
            dropping_pref=dropping_point,
        )
        await asyncio.sleep(0.8)
        ok, checked, labels = await _verify_board_drop_selected(page)
        if ok:
            break
        run._log(
            f"⚠ Board/drop retry {attempt + 1}/5 "
            f"(need boarding + dropping in separate columns; got {checked} point label(s))"
        )
        await _scroll_redbus_drop_column(page)
        await asyncio.sleep(0.8)

    if ok:
        run._log(f"✅ Board & drop selected ({checked} points — boarding + dropping)")
        for lb in labels[:2]:
            run._log(f"   • {lb[:70]}")
    else:
        run._log(
            "❌ Could not auto-select **both** board and drop — "
            "use exact dropping name/time from RedBus right column in the Streamlit form "
            f"(got {checked} valid selection(s))."
        )
        await screenshot(engine, run)
        return

    await ensure_redbus_logged_in(page, run, wait_human, mobile=contact_mobile)
    await _click_bus_continue(page, run, "board/drop→passenger", engine=engine)
    await asyncio.sleep(2)
    await screenshot(engine, run)


async def _redbus_passenger_gender_selected(page, passenger_index: int) -> bool:
    try:
        return bool(
            await page.evaluate(
                """
                (idx) => {
                    const n = idx + 1;
                    const roots = [];
                    for (const el of document.querySelectorAll('div, section, article, form')) {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ');
                        const m = t.match(new RegExp('Passenger\\\\s+' + n + '(?:\\\\D|$)', 'i'));
                        if (!m) continue;
                        if (t.length > 2500) continue;
                        roots.push(el);
                    }
                    const scope = roots.sort((a, b) => a.innerText.length - b.innerText.length)[0] || document.body;
                    for (const r of scope.querySelectorAll('input[type="radio"]:checked, [role="radio"][aria-checked="true"]')) {
                        const row = (r.closest('div, label, button')?.innerText || '').toLowerCase();
                        if (/male|female/.test(row)) return true;
                    }
                    for (const el of scope.querySelectorAll('div, button, label')) {
                        const raw = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (!/^(male|female)$/i.test(raw)) continue;
                        const cls = String(el.className || '').toLowerCase();
                        const style = getComputedStyle(el);
                        const bg = style.backgroundColor || '';
                        const border = style.borderColor || '';
                        if (/selected|active|checked/.test(cls)) return true;
                        if (/220|53|239|ef4444|dc2626|f87171/i.test(bg + border)) return true;
                    }
                    return false;
                }
                """,
                passenger_index,
            )
        )
    except Exception:
        return False


async def _fill_redbus_passenger_gender(
    page,
    run: "TravelRun",
    passenger_index: int,
    gender: str,
) -> bool:
    """RedBus uses large Male/Female pill buttons (not always native <label for=radio>)."""
    g = (gender or "").strip().lower()
    want = "Female" if g.startswith("f") else "Male" if g.startswith("m") else gender.strip()
    if not want:
        return False

    clicked = await page.evaluate(
        """
        ([idx, wantLabel]) => {
            const want = (wantLabel || '').toLowerCase();
            const n = idx + 1;
            const fire = (el) => {
                if (!el) return;
                el.scrollIntoView({ block: 'center', behavior: 'instant' });
                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                el.click();
            };
            const roots = [];
            for (const el of document.querySelectorAll('div, section, article, form')) {
                const t = (el.innerText || '').replace(/\\s+/g, ' ');
                const m = t.match(new RegExp('Passenger\\\\s+' + n + '(?:\\\\D|$)', 'i'));
                if (!m) continue;
                if (t.length > 2500) continue;
                roots.push(el);
            }
            const scopes = roots.length ? [roots.sort((a, b) => a.innerText.length - b.innerText.length)[0]] : [document.body];

            const tryScope = (root) => {
                const pills = [];
                for (const el of root.querySelectorAll('button, div, label, span, [role="radio"]')) {
                    const raw = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!/^(male|female)$/i.test(raw)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 36 || r.height < 18) continue;
                    pills.push({ el, raw: raw.toLowerCase(), area: r.width * r.height });
                }
                pills.sort((a, b) => b.area - a.area);
                for (const p of pills) {
                    if (p.raw !== want) continue;
                    const target = p.el.closest('div, button, label') || p.el;
                    fire(target);
                    fire(p.el);
                    const radio = target.querySelector('input[type="radio"]');
                    if (radio) {
                        radio.checked = true;
                        fire(radio);
                        radio.dispatchEvent(new Event('change', { bubbles: true }));
                        radio.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    return true;
                }
                for (const r of root.querySelectorAll('input[type="radio"], [role="radio"]')) {
                    const row = (r.closest('div, label, button')?.innerText || '').toLowerCase();
                    if (!row.includes(want)) continue;
                    fire(r);
                    return true;
                }
                return false;
            };

            for (const root of scopes) {
                if (tryScope(root)) return true;
            }
            return false;
        }
        """,
        [passenger_index, want],
    )
    if clicked:
        await asyncio.sleep(0.35)
        if await _redbus_passenger_gender_selected(page, passenger_index):
            run._log(f"→ Gender selected for Passenger {passenger_index + 1}: {want}")
            return True

    # Playwright fallback: click pill inside Passenger N card.
    try:
        section = page.locator("div, section, article").filter(
            has_text=re.compile(rf"Passenger\s+{passenger_index + 1}\b", re.I)
        )
        if await section.count() > 0:
            pill = section.first.get_by_text(re.compile(rf"^{re.escape(want)}$", re.I))
            if await pill.count() > 0:
                await pill.first.scroll_into_view_if_needed()
                await pill.first.click(force=True)
                await asyncio.sleep(0.35)
                if await _redbus_passenger_gender_selected(page, passenger_index):
                    run._log(f"→ Gender selected (Playwright) for Passenger {passenger_index + 1}: {want}")
                    return True
    except Exception:
        pass

    run._log(f"⚠ Could not select gender for Passenger {passenger_index + 1} ({want})")
    return False


async def _fill_redbus_state_of_residence(
    page,
    run: "TravelRun",
    state: str,
) -> bool:
    """RedBus contact step requires State of Residence (GST invoicing dropdown)."""
    state = (state or "").strip()
    if not state:
        return False

    picked = await page.evaluate(
        """
        (wantState) => {
            const want = (wantState || '').trim();
            if (!want) return false;
            const wantLo = want.toLowerCase();
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const matches = (text) => {
                const t = norm(text).toLowerCase();
                return t === wantLo || t.includes(wantLo) || wantLo.includes(t);
            };
            const fire = (el) => {
                if (!el) return;
                el.scrollIntoView({ block: 'center', behavior: 'instant' });
                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                el.click();
            };

            const inStateSection = (el) => {
                let n = el;
                for (let i = 0; i < 10 && n; i++) {
                    const t = (n.innerText || '').slice(0, 400);
                    if (/state of residence/i.test(t)) return true;
                    n = n.parentElement;
                }
                return false;
            };

            for (const sel of document.querySelectorAll('select')) {
                if (!inStateSection(sel) && !/state/i.test(sel.name || sel.id || '')) continue;
                for (const opt of sel.options) {
                    if (!matches(opt.text)) continue;
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                    return norm(opt.text);
                }
            }

            let trigger = null;
            for (const el of document.querySelectorAll('label, span, div, p')) {
                const t = norm(el.innerText || '');
                if (!/^state of residence/i.test(t) && !/state of residence\\s*\\*/i.test(t)) continue;
                let node = el.parentElement;
                for (let i = 0; i < 8 && node; i++) {
                    const cand = node.querySelector(
                        '[role="combobox"], [role="listbox"], select, button, div[class*="select" i], div[class*="dropdown" i]'
                    );
                    if (cand) { trigger = cand; break; }
                    node = node.parentElement;
                }
                if (trigger) break;
            }
            if (trigger) {
                fire(trigger);
                for (const opt of document.querySelectorAll(
                    '[role="option"], li, div, span, button'
                )) {
                    const raw = norm(opt.innerText || opt.textContent || '');
                    if (!raw || raw.length > 60) continue;
                    if (!matches(raw)) continue;
                    const r = opt.getBoundingClientRect();
                    if (r.width < 20 || r.height < 10) continue;
                    fire(opt);
                    return raw;
                }
            }

            for (const el of document.querySelectorAll('div, span, button, li, [role="option"]')) {
                const raw = norm(el.innerText || el.textContent || '');
                if (!matches(raw) || raw.length > 60) continue;
                if (!inStateSection(el)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 14) continue;
                fire(el);
                return raw;
            }
            return '';
        }
        """,
        state,
    )
    if picked:
        run._log(f"→ State of residence: {str(picked)[:40]}")
        return True

    try:
        section = page.locator("div, section").filter(
            has_text=re.compile(r"State of Residence", re.I)
        )
        if await section.count() > 0:
            box = section.first
            for sel in (
                box.locator("select"),
                box.get_by_role("combobox"),
                box.locator("button"),
            ):
                try:
                    if await sel.count() > 0:
                        await sel.first.click(force=True)
                        break
                except Exception:
                    continue
            opt = page.get_by_role("option", name=re.compile(re.escape(state), re.I))
            if await opt.count() > 0:
                await opt.first.click(force=True)
                run._log(f"→ State of residence (Playwright): {state[:40]}")
                return True
            await page.get_by_text(re.compile(rf"^{re.escape(state)}$", re.I)).first.click(
                force=True, timeout=5000
            )
            run._log(f"→ State of residence (text click): {state[:40]}")
            return True
    except Exception:
        pass

    run._log(f"⚠ Could not select state of residence ({state}) — pick it manually in Chrome")
    return False


async def _fill_bus_passenger_info(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    passenger_count: int,
    wait_human: Callable[[str], Awaitable[str]],
    *,
    contact_name: str = "",
    contact_email: str = "",
    contact_mobile: str = "",
    contact_state: str = "",
    passengers_detail: list[dict] | None = None,
) -> None:
    run._phase("passengers")
    run._log("📍 Step 3/3: Passenger information (from Streamlit form)")

    page = await _focus_redbus_checkout_page(engine, run)
    await ensure_redbus_logged_in(page, run, wait_human, mobile=contact_mobile)
    step = await _redbus_booking_step(page)
    if step != "passenger":
        if step == "board_drop":
            run._log("⚠ Still on Board/Drop — complete boarding/dropping in Chrome, then confirm")
        else:
            run._log(f"⚠ Not on Passenger Info yet (current: {step}) — clicking Continue on checkout tab")
            await _click_bus_continue(page, run, "board/drop→passenger", engine=engine)
            page = await _focus_redbus_checkout_page(engine, run)
            await asyncio.sleep(2)
        if await _redbus_booking_step(page) != "passenger":
            await ask_tagged(
                wait_human,
                TAG_CONFIRM_DONE,
                "Complete **Board/Drop** and click **Continue** until **Passenger Info** step, then confirm.",
                "Passenger step ready",
            )

    if not contact_name or not contact_email or not contact_mobile:
        run._log("❌ Missing passenger contact — fill the Bus page form before Start")
        run.status = "failed"
        return

    passengers_detail = list(passengers_detail or [])
    while len(passengers_detail) < passenger_count:
        passengers_detail.append({"name": contact_name, "age": "30", "gender": "Male"})

    await fill_first(
        page,
        (
            'input[type="email"]',
            'input[placeholder*="Email" i]',
            "#contactEmail",
            'input[name*="email" i]',
        ),
        contact_email,
    )
    await fill_first(
        page,
        (
            'input[type="tel"]',
            'input[placeholder*="Mobile" i]',
            "#contactMobile",
            'input[name*="mobile" i]',
            'input[name*="phone" i]',
        ),
        contact_mobile,
    )

    state_val = (contact_state or os.getenv("BUS_CONTACT_STATE", "Tamil Nadu")).strip()
    if state_val:
        await _fill_redbus_state_of_residence(page, run, state_val)

    for i in range(passenger_count):
        row = passengers_detail[i] if i < len(passengers_detail) else {}
        pname = (row.get("name") or contact_name).strip()
        age = str(row.get("age") or "30").strip()
        gender = (row.get("gender") or "Male").strip()
        run._log(f"  Passenger {i + 1}: {pname}, {age}, {gender}")

        name_inputs = page.locator(
            'input[placeholder*="Passenger" i], input[placeholder*="Name" i], '
            'input[name*="passengerName" i], input[name*="name" i]'
        )
        if await name_inputs.count() > i:
            try:
                field = name_inputs.nth(i)
                await field.scroll_into_view_if_needed()
                await field.fill(pname)
            except Exception:
                await fill_first(page, ('input[placeholder*="Name" i]',), pname)

        age_inputs = page.locator(
            'input[placeholder*="Age" i], input[name*="age" i], input[type="number"]'
        )
        if age and await age_inputs.count() > i:
            try:
                await age_inputs.nth(i).fill(age)
            except Exception:
                pass

        if not await _fill_redbus_passenger_gender(page, run, i, gender):
            g = gender.lower()
            if g.startswith("f"):
                for loc in (
                    page.get_by_role("radio", name=re.compile(r"female", re.I)),
                    page.locator('label:has-text("Female")'),
                    page.get_by_text(re.compile(r"^Female$", re.I)),
                ):
                    try:
                        target = loc.nth(i) if await loc.count() > i else loc.first
                        await target.click(force=True)
                        break
                    except Exception:
                        continue
            elif g.startswith("m"):
                for loc in (
                    page.get_by_role("radio", name=re.compile(r"male", re.I)),
                    page.locator('label:has-text("Male")'),
                    page.get_by_text(re.compile(r"^Male$", re.I)),
                ):
                    try:
                        target = loc.nth(i) if await loc.count() > i else loc.first
                        await target.click(force=True)
                        break
                    except Exception:
                        continue

        try:
            all_selects = page.locator("select")
            if await all_selects.count() > i:
                await all_selects.nth(i).select_option(label=gender)
        except Exception:
            pass

    run._log(f"→ Contact: {contact_name}, {contact_mobile[:4]}******")
    await screenshot(engine, run)

    if not await _click_bus_continue(page, run, "passenger form", engine=engine):
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Check **passenger details** in Chrome, click **Proceed to pay** / **Continue**, then confirm.",
            "Passenger info done",
        )
    await asyncio.sleep(2)


async def _bus_payment_handoff(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    page,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    run._phase("payment")
    run._log("📍 Payment — review fare in Chrome")
    await screenshot(engine, run)

    pay_choice = await ask_tagged(
        wait_human,
        TAG_PAYMENT_CONFIRM,
        "Review **fare and passenger summary** in Chrome. Proceed to payment?",
    )
    if pay_choice.upper() in ("NO", "CANCEL"):
        run._log("⏹ Payment cancelled by user")
        run.status = "failed"
        return

    if await click_first(
        page,
        (
            'button:has-text("Pay")',
            'button:has-text("Proceed to pay")',
            'button:has-text("Proceed to Pay")',
            'span:has-text("UPI")',
            'label:has-text("UPI")',
            '[class*="upi"]',
        ),
    ):
        run._log("→ Payment option selected — complete UPI/card in Chrome")

    pay_otp = await ask_tagged(
        wait_human,
        TAG_OTP,
        "Complete payment in Chrome (UPI OTP / bank PIN). Enter OTP if needed, or type **SKIP**.",
    )
    if pay_otp.upper() not in ("", "SKIP"):
        run._log("→ Payment OTP noted")

    await ask_tagged(
        wait_human,
        TAG_CONFIRM_DONE,
        "When you see **booking confirmation / ticket** in Chrome, click confirm below.",
        "Booking confirmed",
    )

    run._log("✅ Bus booking flow complete — Chrome stays open 45s to save ticket")
    run.status = "done"
    run._phase("done")
    await asyncio.sleep(45)
    await finish_page_summary(engine, run, page)


async def bus_portal_booking(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    *,
    portal_url: str,
    origin: str,
    destination: str,
    journey_date: str,
    portal_name: str,
    passenger_count: int,
    wait_human: Callable[[str], Awaitable[str]],
    preferred_bus: str = "",
    bus_type: str = "any",
    contact_name: str = "",
    contact_email: str = "",
    contact_mobile: str = "",
    contact_state: str = "",
    passengers_detail: list[dict] | None = None,
    boarding_point: str = "",
    dropping_point: str = "",
) -> None:
    """RedBus / AbhiBus: search → select bus → passenger details → payment handoff."""
    page = engine.page
    if not await open_url(engine, run, portal_url, label=portal_name):
        run.status = "failed"
        return

    run._phase("search")
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    passenger_count = max(1, min(6, int(passenger_count or 1)))
    if not contact_name and not contact_email and not contact_mobile:
        contact_name, contact_email, contact_mobile, env_state = _bus_contact_from_env()
        if not contact_state:
            contact_state = env_state
    elif not contact_state:
        contact_state = os.getenv("BUS_CONTACT_STATE", "Tamil Nadu").strip()

    if _is_redbus(portal_url):
        await wait_redbus_search_widget(
            page, run, wait_human=wait_human, mobile=contact_mobile
        )
        ok_from = await fill_redbus_city(page, run, field="from", city=origin)
        ok_to = await fill_redbus_city(page, run, field="to", city=destination)
        ok_date = await select_redbus_date(page, run, journey_date)
    else:
        await dismiss_cookie_banner(page)
        from_selectors = (
            "#src",
            "input#mmtSrc",
            'input[placeholder*="From" i]',
            'input[placeholder*="Leaving" i]',
            'input[id*="source" i]',
            'input[name*="source" i]',
        )
        to_selectors = (
            "#dest",
            "input#mmtDest",
            'input[placeholder*="To" i]',
            'input[placeholder*="Going" i]',
            'input[id*="dest" i]',
            'input[name*="dest" i]',
        )
        ok_from = await fill_travel_autocomplete(page, run, from_selectors, origin, "From")
        ok_to = await fill_travel_autocomplete(page, run, to_selectors, destination, "To")
        ok_date = await select_travel_date(page, run, journey_date, portal_url)

    if not ok_from:
        run._log(f"⚠ From city not set ({origin})")
    if not ok_to:
        run._log(f"⚠ To city not set ({destination})")
    if not ok_date:
        run._log(f"⚠ Journey date not set ({journey_date})")

    if not ok_from or not ok_to or not ok_date:
        run._log(
            "ℹ️ Streamlit trip fields do NOT auto-fill Chrome — "
            "complete the search form in the **Chrome window** only."
        )
        run._log("⚠ Auto-fill incomplete — finish search form in Chrome if needed")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"In **Chrome** (not Streamlit), set From={origin}, To={destination}, "
            f"Date={journey_date}, click **Search buses**, then click **✅ Search done** below.",
            "Search done",
        )
        if _is_redbus(portal_url):
            chk_from, chk_to, chk_date = await redbus_form_filled(page, origin, destination, journey_date)
            ok_from = ok_from or chk_from
            ok_to = ok_to or chk_to
            ok_date = ok_date or chk_date
            if ok_from and ok_to:
                run._log("✅ Search form detected in Chrome after your confirm")

    await screenshot(engine, run)
    if _is_redbus(portal_url):
        clicked = await click_redbus_search(page, run)
        if not clicked:
            await click_text(page, r"search buses|search")
    elif await click_first(
        page,
        (
            "#search_button",
            "#search_btn",
            'button:has-text("Search buses")',
            'button:has-text("Search Buses")',
            'button:has-text("Search")',
            ".search-btn",
        ),
    ):
        run._log("→ Clicked Search")
    else:
        await click_text(page, r"search buses|search")

    run._phase("select")
    await asyncio.sleep(4)
    await screenshot(engine, run)

    if preferred_bus or (bus_type and bus_type != "any"):
        pref_bits = []
        if bus_type and bus_type != "any":
            pref_bits.append(bus_type)
        if preferred_bus:
            pref_bits.append(f'"{preferred_bus}"')
        run._log(f"→ Bus preference: {' / '.join(pref_bits)}")

    if _is_redbus(portal_url):
        await _apply_redbus_type_filter(page, run, bus_type)
        page, bus_clicked = await _open_redbus_seat_page(
            engine,
            run,
            page,
            preferred_bus=preferred_bus,
            bus_type=bus_type,
        )
    else:
        bus_clicked = await click_first(
            page,
            (
                'div[class*="bus-item"] button:has-text("View seats")',
                'button:has-text("View seats")',
                'a:has-text("View seats")',
            ),
        )

    if not bus_clicked:
        run._log("⚠ Could not auto-select a matching bus")
        hint = f" ({bus_type} {preferred_bus})".strip() if bus_type != "any" or preferred_bus else ""
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"Pick a **{bus_type or 'suitable'}** bus{hint} and click **View seats**, then confirm.",
            "Bus selected",
        )
        page = await _wait_redbus_seat_page(engine, run, page, timeout=30)

    page = await _focus_redbus_checkout_page(engine, run)
    post_bus_step = await _redbus_booking_step(page)
    if post_bus_step not in ("seats", "board_drop", "passenger"):
        run._log("⚠ Seat page not detected — confirm bus opened in Chrome")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Click **View seats** on your chosen bus in Chrome until the **Select seats** screen appears, then confirm.",
            "Bus selected",
        )
        page = await _wait_redbus_seat_page(engine, run, page, timeout=30)
        page = await _focus_redbus_checkout_page(engine, run)
    else:
        run._log("✅ Seat selection screen open in Chrome")

    await asyncio.sleep(2)
    await screenshot(engine, run)

    await _wait_user_seat_selection(engine, run, page, passenger_count, wait_human)
    await _auto_board_drop_points(
        engine,
        run,
        page,
        wait_human,
        passenger_count,
        boarding_point=boarding_point,
        dropping_point=dropping_point,
        contact_mobile=contact_mobile,
    )
    await _fill_bus_passenger_info(
        engine,
        run,
        page,
        passenger_count,
        wait_human,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_mobile=contact_mobile,
        contact_state=contact_state,
        passengers_detail=passengers_detail,
    )
    if run.status == "failed":
        return
    await _bus_payment_handoff(engine, run, page, wait_human)
