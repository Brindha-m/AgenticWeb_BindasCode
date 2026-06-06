"""
All scripted (Playwright-only) automations — no Claude/OpenAI.
Dispatched by travel_runner via config["scripted_id"].
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Awaitable, Callable

from agent.scripted_common import (
    ask,
    ask_tagged,
    bus_portal_booking,
    click_first,
    click_text,
    fill_first,
    finish_page_summary,
    open_url,
    open_url_with_fallbacks,
    read_math_captcha,
    screenshot,
    state_transport_portal_booking,
    travel_portal_search,
)
from agent.human_prompts import (
    TAG_CONFIRM_DONE,
    TAG_LOGIN_FORM,
    TAG_OTP,
    TAG_PAYMENT_CONFIRM,
    TAG_PILGRIM_FORM,
    TAG_TEXT,
    parse_pilgrim_response,
)
from agent.ttd_automation import (
    SPAT_SLOT_URL,
    _ttd_pilgrim_screen_visible,
    _ttd_slot_screen_visible,
    _wait_until,
    clear_pilgrim_page_alerts,
    click_pay_now,
    click_slot_continue,
    complete_date_and_slot_booking,
    continue_after_pilgrims,
    ensure_ttd_logged_in,
    fill_pilgrims,
    open_spat_slot_booking,
    proceed_to_payment,
    select_time_slot,
)
from agent.tneb_automation import run_quickpay

TIRUPATI_URLS = (
    "https://ttdsevaonline.com",
    "https://www.ttdsevaonline.com",
    "https://www.tirupatibalaji.ap.gov.in",
)

if TYPE_CHECKING:
    from agent.playwright_engine import PlaywrightEngine
    from agent.travel_runner import TravelRun

CONSIGNMENT_RE = re.compile(r"^[A-Z]{2}\d{9}[A-Z]{2}$", re.I)

# All IDs that support scripted mode in the UI
SCRIPTED_GOV_IDS = frozenset({
    "tirupati",
    "passport",
    "indiapost",
    "tneb",
    "fastag",
    "pan_gst_lpg",
    "exam",
    "state",
    "state_transport",
})
SCRIPTED_TRAVEL_IDS = frozenset({"bus", "flights", "state", "state_transport"})
SCRIPTED_HOME_IDS = SCRIPTED_GOV_IDS  # quick-launch on Home maps to gov ids

ALL_SCRIPTED_IDS = SCRIPTED_GOV_IDS | SCRIPTED_TRAVEL_IDS


def supports_scripted(scripted_id: str) -> bool:
    return scripted_id in ALL_SCRIPTED_IDS


async def run_indiapost(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    params = config.get("params", {})
    page = engine.page
    consignment = (
        config.get("consignment_number")
        or params.get("consignment_number")
        or ""
    ).strip().upper()

    consignment = (
        await ask_tagged(
            wait_human,
            TAG_TEXT,
            "Enter your **13-character India Post consignment number** (e.g. EK403807171IN):",
            prefilled=consignment,
        )
    ).upper()

    if not consignment or consignment == "CANCEL":
        run.status = "failed"
        return
    if not CONSIGNMENT_RE.match(consignment):
        run.error = "Invalid consignment format (e.g. EK403807171IN)."
        run.status = "failed"
        return

    run._log(f"📮 Tracking {consignment} (scripted)")
    if not await open_url(engine, run, "https://www.indiapost.gov.in/"):
        run.status = "failed"
        return

    await click_text(page, r"consignment\s*id")
    await click_text(page, r"consignment\s*number")
    run._phase("search")

    if not await fill_first(
        page,
        ('input[name="conNo"]', 'input[id*="consignment" i]', 'input[placeholder*="Consignment" i]'),
        consignment,
    ):
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            f"Type **{consignment}** in the consignment field in Chrome, then confirm.",
            "Consignment entered",
        )
        await finish_page_summary(engine, run, page)
        return

    captcha = await read_math_captcha(page)
    if not captcha:
        captcha = await ask_tagged(
            wait_human,
            TAG_TEXT,
            "Enter the **math CAPTCHA** answer (e.g. 3+5 → 8):",
        )
    if captcha:
        await fill_first(
            page,
            ('input[name="captchaAnswer"]', 'input[id*="captcha" i]', "#captchaInput"),
            captcha,
        )

    await click_first(page, ('button#btnSearch', 'input[value="Search"]', 'button:has-text("Search")'))
    await asyncio.sleep(3)
    await finish_page_summary(engine, run, page)


async def run_tneb(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    """TNPDCL Quick Pay — consumer number + image CAPTCHA (IRCTC-style human prompts)."""
    ok = await run_quickpay(engine, run, config, wait_human)
    if not ok:
        return
    await finish_page_summary(engine, run, engine.page)


async def run_passport(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    url = config.get("portal_url") or "https://www.passportindia.gov.in"
    if not await open_url(engine, run, url):
        run.status = "failed"
        return
    page = engine.page
    run._phase("search")
    await click_text(page, r"existing\s*user\s*login")
    await click_text(page, r"login")
    await screenshot(engine, run)
    creds = await ask_tagged(
        wait_human,
        TAG_LOGIN_FORM,
        "Enter your **Passport Seva** login id and password.",
    )
    if creds.upper() == "CANCEL":
        run.status = "failed"
        return
    if "|" in creds:
        user, pwd = creds.split("|", 1)
        await fill_first(page, ('input[name="loginId"]', "#userName", 'input[id*="user" i]'), user.strip())
        await fill_first(page, ('input[name="password"]', "#password", 'input[type="password"]'), pwd.strip())
        await click_first(page, ('button[type="submit"]', 'input[type="submit"]', 'button:has-text("Login")'))
        await asyncio.sleep(2)
    await ask_tagged(
        wait_human,
        TAG_CONFIRM_DONE,
        "Navigate to **appointment booking** in Chrome (stop before payment), then confirm.",
        "Ready for appointment",
    )
    await finish_page_summary(engine, run, page)


def _pilgrim_count(params: dict, config: dict) -> int:
    raw = str(params.get("pilgrim_count") or config.get("pilgrim_count") or "2")
    m = re.search(r"\d+", raw)
    return max(1, min(8, int(m.group()) if m else 2))


async def run_tirupati(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    params = config.get("params", {})
    target_date = (params.get("target_date") or config.get("target_date") or "").strip()
    pilgrim_count = _pilgrim_count(params, config)
    mobile = (params.get("mobile") or "").strip()
    if mobile.upper().startswith("USER"):
        mobile = ""

    primary = config.get("portal_url") or SPAT_SLOT_URL
    portal_urls = [u for u in ([primary] if primary else []) + list(TIRUPATI_URLS) if u]

    run._log("🌐 Opening TTD Sri PAT slot booking")
    opened = await open_url(engine, run, SPAT_SLOT_URL, label="Sri PAT slot booking")
    if not opened:
        opened = await open_url_with_fallbacks(engine, run, portal_urls, label="TTD portal")
        if opened:
            page = engine.page
            if await click_text(page, r"special\s*entry\s*darshan"):
                run._log("→ Opened Special Entry Darshan menu")
                await asyncio.sleep(0.6)
            if await click_text(page, r"darshan\s*slots"):
                run._log("→ Darshan Slots")
                await asyncio.sleep(0.8)
    if not opened:
        run.status = "failed"
        return

    page = engine.page
    run._phase("search")
    await screenshot(engine, run)

    if not await ensure_ttd_logged_in(page, run, wait_human, mobile=mobile):
        run.status = "failed"
        return

    if "slot" not in (page.url or "").lower() and "spat" not in (page.url or "").lower():
        run._log("→ Navigating to Sri PAT slot booking after login")
        if not await open_url(engine, run, SPAT_SLOT_URL, label="Sri PAT slot booking"):
            await open_spat_slot_booking(engine, run, fallback_urls=tuple(portal_urls))
        page = engine.page

    await screenshot(engine, run)
    run._phase("select")

    # Step A: calendar date → time slot (Continue only AFTER pilgrim fill)
    run._log("📅 Step: select date and time slot")
    slot_ok = await complete_date_and_slot_booking(
        page, run, target_date, ticket_count=pilgrim_count
    )
    if not slot_ok:
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Pick a **green calendar date** (if needed), select a **time slot** radio. "
            "**Do not click Continue** — confirm when slot is selected.",
            "Date & slot done",
        )
        if await select_time_slot(page, run, ticket_count=pilgrim_count):
            await click_slot_continue(page, run, ticket_count=pilgrim_count)
            await clear_pilgrim_page_alerts(page, run)
        else:
            run._log("⚠ Still need a time slot in Chrome")

    await screenshot(engine, run)
    if not await _wait_until(page, _ttd_pilgrim_screen_visible, timeout=15.0):
        if await _ttd_slot_screen_visible(page):
            run._log("⚠ Slot page — re-selecting time slot")
            await select_time_slot(page, run, ticket_count=pilgrim_count)
            await click_slot_continue(page, run, ticket_count=pilgrim_count)
            await clear_pilgrim_page_alerts(page, run)
        if not await _wait_until(page, _ttd_pilgrim_screen_visible, timeout=10.0):
            await ask_tagged(
                wait_human,
                TAG_CONFIRM_DONE,
                "Select a **time slot** radio. Scroll to **Pilgrim Details** — "
                "**do not click Continue** yet. Confirm when pilgrim form is visible.",
                "Pilgrim page open",
            )
            await clear_pilgrim_page_alerts(page, run)
            run._log("→ Pilgrim section confirmed — continuing to pilgrim details step")

    # Step B: pilgrim details (after slot Continue)
    run._log("👤 Step: pilgrim details (name, age, gender, ID)")
    await clear_pilgrim_page_alerts(page, run)
    pilgrim_payload = await ask_tagged(
        wait_human,
        TAG_PILGRIM_FORM,
        f"Enter **name, age, gender, photo ID proof, and ID number** for each pilgrim "
        f"(ticket count: {pilgrim_count}).",
        str(pilgrim_count),
    )
    if pilgrim_payload.upper() == "CANCEL":
        run.status = "failed"
        return

    pilgrims = parse_pilgrim_response(pilgrim_payload)
    page = engine.page
    await screenshot(engine, run)

    filled = 0
    if pilgrims:
        try:
            filled = await fill_pilgrims(page, run, pilgrims)
        except Exception as exc:
            run._log(f"⚠ Pilgrim auto-fill failed: {exc}")
    else:
        run._log("⚠ No pilgrim data parsed — fill manually in Chrome")

    human_filled_pilgrims = filled < len(pilgrims or [])
    if human_filled_pilgrims:
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "If auto-fill failed: in Chrome select **Gender → Female** and **Photo ID Proof → Aadhaar Card**, "
            "enter **12-digit Aadhaar**, then confirm.",
            "Pilgrim details done",
        )

    await screenshot(engine, run)
    run._log("→ Clicking **Continue** after pilgrim details (toward payment)")
    if not await continue_after_pilgrims(page, run, trust_human=human_filled_pilgrims):
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Fix any red errors on pilgrim page, then click **Continue** at the bottom in Chrome.",
            "Ready for payment",
        )

    # Step C: payment handoff (pilgrim Continue → Pay Now page)
    await proceed_to_payment(page, run)
    await screenshot(engine, run)
    run._phase("checkout")

    pay_choice = await ask_tagged(
        wait_human,
        TAG_PAYMENT_CONFIRM,
        "Verify the **booking amount** and pilgrim summary in Chrome, then confirm to **Pay Now**.",
    )
    if pay_choice.upper() in ("NO", "CANCEL"):
        run._log("⏹ Payment cancelled by user")
        run.status = "failed"
        return

    if not await click_pay_now(page, run):
        run._log("⚠ **Pay Now** auto-click failed — click it manually in Chrome")
    await screenshot(engine, run)

    pay_otp = await ask_tagged(
        wait_human,
        TAG_OTP,
        "Complete payment in Chrome (UPI/card). Enter **payment OTP** here if your bank asks, or type **SKIP** if not needed.",
    )
    if pay_otp.upper() not in ("", "SKIP"):
        run._log("→ Payment OTP received")

    await ask_tagged(
        wait_human,
        TAG_CONFIRM_DONE,
        "When you see the **booking confirmation / receipt** in Chrome, click below.",
        "Booking confirmed",
    )

    run._log("✅ Booking complete — save receipt in Chrome, then click **Stop**")
    run.status = "done"
    await asyncio.sleep(5)
    await finish_page_summary(engine, run, page)


async def run_fastag(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    params = config.get("params", {})
    url = params.get("fastag_portal_url") or config.get("portal_url") or "https://www.onefastag.com"
    if not await open_url(engine, run, url):
        run.status = "failed"
        return
    page = engine.page
    await click_text(page, r"login|check\s*balance")
    await screenshot(engine, run)
    await ask_tagged(
        wait_human,
        TAG_LOGIN_FORM,
        "Enter **mobile number** and **OTP** to log in to FASTag.",
        "otp",
    )
    await ask_tagged(
        wait_human,
        TAG_CONFIRM_DONE,
        "Check **balance / recharge** in Chrome, then confirm.",
        "FASTag done",
    )
    await finish_page_summary(engine, run, page)


async def run_pan_gst_lpg(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    if not await open_url(engine, run, "https://eportal.incometax.gov.in", label="Income Tax"):
        run.status = "failed"
        return
    await ask_tagged(wait_human, TAG_CONFIRM_DONE, "Check **ITR status** on Income Tax portal, then confirm.", "ITR done")
    await open_url(engine, run, "https://www.gst.gov.in", label="GST")
    await ask_tagged(wait_human, TAG_CONFIRM_DONE, "Check **GST filings**, then confirm.", "GST done")
    await open_url(engine, run, "https://cx.indianoil.in", label="Indane LPG")
    await ask_tagged(wait_human, TAG_CONFIRM_DONE, "Check/book **LPG** in Chrome, then confirm.", "LPG done")
    await finish_page_summary(engine, run, engine.page)


async def run_exam(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    params = config.get("params", {})
    url = config.get("portal_url") or "https://results.nic.in"
    roll = await ask_tagged(
        wait_human, TAG_TEXT, "Enter **exam roll number**:", prefilled=params.get("roll_number", "")
    )
    school = await ask_tagged(
        wait_human, TAG_TEXT, "Enter **school number** (if required):", prefilled=params.get("school_number", "")
    )
    dob = await ask_tagged(
        wait_human, TAG_TEXT, "Enter **DOB** (DD/MM/YYYY):", prefilled=params.get("dob", "")
    )

    if not await open_url(engine, run, url):
        if not await open_url(engine, run, "https://cbseresults.nic.in"):
            run.status = "failed"
            return

    page = engine.page
    run._phase("search")
    exam = params.get("exam_name", "")
    if exam:
        await click_text(page, re.escape(exam[:20]))

    await fill_first(page, ('input[name="rollno"]', "#rollNumber", 'input[id*="roll" i]'), roll)
    await fill_first(page, ('input[name="schoolno"]', 'input[id*="school" i]'), school)
    await fill_first(page, ('input[name="dob"]', 'input[type="date"]', 'input[id*="dob" i]'), dob)

    captcha = await read_math_captcha(page)
    if captcha:
        await fill_first(page, ('input[name="captcha"]', 'input[id*="captcha" i]'), captcha)
    else:
        cap = await ask_tagged(wait_human, TAG_TEXT, "Enter **CAPTCHA** shown on the results page:")
        await fill_first(page, ('input[name="captcha"]', 'input[id*="captcha" i]'), cap)

    await click_first(page, ('button[type="submit"]', 'input[type="submit"]'))
    await asyncio.sleep(3)
    await finish_page_summary(engine, run, page)


async def run_bus(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    p = config.get("params", {})
    await bus_portal_booking(
        engine,
        run,
        portal_url=config.get("portal_url", ""),
        origin=p.get("origin", ""),
        destination=p.get("destination", ""),
        journey_date=p.get("date", ""),
        portal_name=p.get("portal_label", config.get("category_label", "RedBus")),
        passenger_count=int(p.get("passengers", config.get("passengers", 1)) or 1),
        wait_human=wait_human,
        preferred_bus=p.get("preferred_bus", ""),
        bus_type=p.get("bus_type", "any"),
        contact_name=p.get("contact_name", ""),
        contact_email=p.get("contact_email", ""),
        contact_mobile=p.get("contact_mobile", ""),
        contact_state=p.get("contact_state", ""),
        passengers_detail=p.get("passengers_detail"),
        boarding_point=p.get("boarding_point", ""),
        dropping_point=p.get("dropping_point", ""),
    )


async def run_travel(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    p = config.get("params", {})
    await travel_portal_search(
        engine,
        run,
        portal_url=config.get("portal_url", ""),
        origin=p.get("origin", ""),
        destination=p.get("destination", ""),
        journey_date=p.get("date", ""),
        portal_name=p.get("portal_label", config.get("category_label", "portal")),
        wait_human=wait_human,
    )


async def run_state_transport(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    p = config.get("params", {})
    await state_transport_portal_booking(
        engine,
        run,
        portal_url=config.get("portal_url", ""),
        origin=p.get("origin", ""),
        destination=p.get("destination", ""),
        journey_date=p.get("date", ""),
        portal_name=p.get("portal_label", config.get("category_label", "State Transport")),
        passenger_count=int(p.get("passengers", config.get("passengers", 1)) or 1),
        wait_human=wait_human,
    )


SCRIPTED_REGISTRY: dict[str, Callable] = {
    "indiapost": run_indiapost,
    "tneb": run_tneb,
    "passport": run_passport,
    "tirupati": run_tirupati,
    "fastag": run_fastag,
    "pan_gst_lpg": run_pan_gst_lpg,
    "exam": run_exam,
    "bus": run_bus,
    "flights": run_travel,
    "state": run_state_transport,
    "state_transport": run_state_transport,
}


async def run_scripted(
    scripted_id: str,
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> None:
    run._log(f"⚡ Scripted Playwright mode — no AI API ({scripted_id})")
    run._phase("launch")

    fn = SCRIPTED_REGISTRY.get(scripted_id)
    if not fn:
        run._log(f"❌ No scripted flow for: {scripted_id}")
        run.status = "failed"
        run.error = f"Scripted mode not available for {scripted_id}"
        return

    await fn(engine, run, config, wait_human)
