"""
TNPDCL Quick Pay automation — https://www.tnebnet.org/qwp/qpay

PrimeFaces form (no region dropdown on Quick Pay):
  - Consumer / Acknowledgement number: #userName (name=j_username)
  - Image CAPTCHA: #CaptchaImgID → input #CaptchaID
  - Submit button
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from agent.human_prompts import TAG_CAPTCHA, TAG_CONFIRM_DONE, TAG_PAYMENT_CONFIRM, TAG_TEXT
from agent.scripted_common import (
    ask_image_captcha,
    ask_tagged,
    click_first,
    fill_first,
    open_url,
    screenshot,
)

if TYPE_CHECKING:
    from agent.playwright_engine import PlaywrightEngine
    from agent.travel_runner import TravelRun

TNEB_QUICKPAY_URL = "https://www.tnebnet.org/qwp/qpay"

_CONSUMER_INPUT = (
    "#userName",
    'input[name="j_username"]',
    'input[placeholder*="Consumer" i]',
    'input[placeholder*="Acknowledgement" i]',
)
_CAPTCHA_INPUT = ("#CaptchaID", 'input[name="CaptchaID"]')
_SUBMIT_BUTTONS = ('button:has-text("Submit")', 'button[type="submit"]')
_REFRESH_CAPTCHA = ('a:has-text("Refresh")', 'a[href="#"]:has-text("Refresh")')

# Only real submit failures — NOT "Consumer No:" on the Bill Details success page
_SUBMIT_ERROR_RE = re.compile(
    r"check\s+captcha|invalid\s+captcha|captcha\s+is\s+invalid|"
    r"invalid\s+consumer\s+number|consumer\s+number\s+is\s+invalid|"
    r"enter\s+the\s+number\s+in\s+the\s+box",
    re.I,
)

_SUCCESS_MARKERS_RE = re.compile(
    r"bill\s+details|consumer\s+name\s*:|no\s+pending\s+bill|my\s+transactions",
    re.I,
)


async def _page_snippet(page, limit: int = 2400) -> str:
    try:
        return await page.evaluate(
            f"() => (document.body.innerText || '').slice(0, {limit})"
        )
    except Exception:
        return ""


async def _looks_like_bill_success(page, text: str = "") -> bool:
    """True when Quick Pay returned bill details (not the entry form)."""
    try:
        ok = await page.evaluate(
            """() => {
            const t = (document.body.innerText || '').toLowerCase();
            if (/bill\\s+details/.test(t) && /consumer\\s+name/.test(t)) return true;
            if (/no\\s+pending\\s+bill/.test(t)) return true;
            if (/consumer\\s+no\\s*:\\s*\\d/.test(t) && /consumer\\s+name\\s*:/.test(t)) return true;
            return false;
        }"""
        )
        if ok:
            return True
    except Exception:
        pass
    return bool(_SUCCESS_MARKERS_RE.search(text or ""))


def _looks_like_submit_error(text: str) -> bool:
    """True only for CAPTCHA/consumer validation failures on the entry form."""
    if not text:
        return False
    if _looks_like_bill_success_sync(text):
        return False
    return bool(_SUBMIT_ERROR_RE.search(text))


def _looks_like_bill_success_sync(text: str) -> bool:
    return bool(_SUCCESS_MARKERS_RE.search(text or ""))


_NO_PENDING_RE = re.compile(r"no\s+pending\s+bill", re.I)


async def parse_tneb_bill_status(page) -> dict:
    """Parse Quick Pay result table (Due Date, Info, amounts)."""
    return await page.evaluate(
        """() => {
        const raw = document.body.innerText || '';
        const text = raw.replace(/\\s+/g, ' ').trim();
        const fields = {};
        for (const row of document.querySelectorAll('tr')) {
            const cells = [...row.querySelectorAll('td, th')].map(c => (c.innerText || '').trim());
            if (cells.length >= 2) {
                const key = cells[0].replace(/\\s+/g, ' ').trim();
                const val = cells.slice(1).join(' ').trim();
                if (key) fields[key] = val;
            }
        }
        const pick = (re) => { const m = text.match(re); return m ? m[1].trim() : ''; };
        const dueDate = fields['Due Date'] || fields['Due date'] || pick(/Due\\s*Date\\s*:?\\s*([^\\n|]+)/i);
        const info = fields['Info'] || fields['INFO'] || pick(/Info\\s*:?\\s*([^\\n|]+)/i);
        const billAmount = fields['Amount'] || fields['Bill Amount'] || fields['Payable Amount']
            || pick(/(?:Amount|Bill\\s*Amount|Payable)[:\\s]*(?:Rs\\.?|₹)?\\s*([\\d,]+(?:\\.\\d+)?)/i);
        const consumerName = fields['Consumer Name'] || fields['Name']
            || pick(/Consumer\\s*Name\\s*:?\\s*([^\\n|]+?)(?:\\s{2,}|$)/i);
        const noPending = /no\\s+pending\\s+bill/i.test(text)
            || /no\\s+pending\\s+bill/i.test(info || '');
        const dueEmpty = !dueDate || dueDate === '-' || dueDate === '—';
        const hasBillDetails = /bill\\s+details/i.test(text);
        let status = 'unknown';
        if (noPending) status = 'no_pending';
        else if (billAmount && !dueEmpty) status = 'pending';
        else if (billAmount) status = 'pending';
        else if (hasBillDetails && dueEmpty && !billAmount) status = 'no_pending';
        else if (info && !noPending) status = 'info';
        return {
            status,
            no_pending: noPending,
            due_date: dueDate || '-',
            info: info || '',
            bill_amount: billAmount || '',
            consumer_name: consumerName || '',
            units: fields['Units'] || fields['Consumption'] || pick(/(?:Units|Consumption)[:\\s]+([\\d.]+)/i),
            service_number: fields['Service Number'] || fields['Consumer No']
                || pick(/(?:Service|Consumer)\\s*(?:No|Number)[:\\s]+([\\d]+)/i),
            fields,
        };
    }"""
    )


def apply_tneb_result_to_run(run: "TravelRun", data: dict) -> dict:
    """Store structured bill outcome on the run for Streamlit."""
    status = (data or {}).get("status") or "unknown"
    no_pending = bool((data or {}).get("no_pending"))
    info = ((data or {}).get("info") or "").strip()
    due = ((data or {}).get("due_date") or "-").strip()
    amount = ((data or {}).get("bill_amount") or "").strip()

    consumer = ((data or {}).get("consumer_name") or "").strip()
    if no_pending or _NO_PENDING_RE.search(info):
        status = "no_pending"
        headline = "No pending bill"
        message = info or "NO PENDING BILL — nothing to pay right now."
    elif status == "no_pending" or (not amount and due in ("-", "—") and consumer):
        status = "no_pending"
        headline = "No pending bill"
        message = info or "No amount due — your account has no pending bill."
    elif amount:
        status = "pending"
        headline = "Bill pending"
        message = f"Amount due: ₹{amount}" + (f" · Due: {due}" if due and due != "-" else "")
    elif info:
        headline = "Bill status"
        message = info
    else:
        headline = "Bill lookup complete"
        message = "Review Chrome for full details."

    result = {
        "status": status,
        "headline": headline,
        "message": message,
        "due_date": due,
        "info": info,
        "bill_amount": amount,
        "consumer_name": (data or {}).get("consumer_name") or "",
        "units": (data or {}).get("units") or "",
        "service_number": (data or {}).get("service_number") or "",
    }
    run.result = result

    run._log(f"📋 Due Date: {due or '-'}")
    if info:
        run._log(f"📋 Info: {info}")
    if no_pending or status == "no_pending":
        run._log("✅ NO PENDING BILL — nothing to pay")
    elif amount:
        run._log(f"💰 Amount due: ₹{amount}")
        if (data or {}).get("consumer_name"):
            run._log(f"  Consumer: {data['consumer_name']}")
    return result


async def _extract_bill_fields(page, run: "TravelRun") -> dict:
    data = await parse_tneb_bill_status(page)
    return apply_tneb_result_to_run(run, data)


async def run_quickpay(
    engine: "PlaywrightEngine",
    run: "TravelRun",
    config: dict,
    wait_human: Callable[[str], Awaitable[str]],
) -> bool:
    """Quick Pay: consumer number + image CAPTCHA → bill view (optional payment)."""
    params = config.get("params", {})
    url = config.get("portal_url") or TNEB_QUICKPAY_URL

    consumer = (
        params.get("consumer_number")
        or params.get("service_number")
        or config.get("consumer_number")
        or ""
    ).strip()
    consumer = await ask_tagged(
        wait_human,
        TAG_TEXT,
        "Enter your **Consumer Number** or **Acknowledgement Number** (from TNPDCL bill / receipt):",
        prefilled=consumer,
    )
    if not consumer:
        run._log("❌ Consumer number is required")
        run.status = "failed"
        return False

    if not await open_url(engine, run, url, label="TNPDCL Quick Pay"):
        run.status = "failed"
        return False

    page = engine.page
    run._phase("search")

    try:
        await page.wait_for_selector(_CONSUMER_INPUT[0], timeout=45000)
    except Exception:
        run._log("⚠ Quick Pay form slow to load — check Chrome manually")
        await screenshot(engine, run)

    if not await fill_first(page, _CONSUMER_INPUT, consumer):
        run._log("⚠ Could not fill consumer number — enter it in Chrome")
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Enter the consumer number in Chrome, then continue:",
            param="Consumer number entered",
        )

    captcha_attempts = 2
    submitted = False
    for attempt in range(1, captcha_attempts + 1):
        if attempt > 1:
            await click_first(page, _REFRESH_CAPTCHA)
            await asyncio.sleep(1.2)

        captcha = await ask_image_captcha(
            engine,
            run,
            wait_human,
            message=(
                "Type the **CAPTCHA** exactly as shown in Chrome "
                "(letters and numbers; case may matter)."
            ),
        )
        if not captcha:
            run._log("❌ CAPTCHA is required")
            run.status = "failed"
            return False

        await fill_first(page, _CAPTCHA_INPUT, captcha)
        await screenshot(engine, run)
        await click_first(page, _SUBMIT_BUTTONS)
        run._log("→ Submitted Quick Pay form")
        await asyncio.sleep(3)
        await screenshot(engine, run)

        body = await _page_snippet(page)
        if await _looks_like_bill_success(page, body):
            run._log("✅ Bill details loaded")
            submitted = True
            break
        if _looks_like_submit_error(body):
            run._log(f"⚠ Portal message: {body[:120].replace(chr(10), ' ')}")
            if attempt < captcha_attempts:
                run._log("→ Retry with a fresh CAPTCHA")
                continue
            await ask_tagged(
                wait_human,
                TAG_CONFIRM_DONE,
                "Fix consumer number / CAPTCHA in Chrome if needed, then continue:",
                param="Form corrected",
            )
        else:
            submitted = True
            break

    if not submitted:
        run._log("⚠ Submit may have failed — review Chrome")

    run._phase("select")
    bill = await _extract_bill_fields(page, run)

    if bill.get("status") == "no_pending":
        run._phase("done")
        return True

    if not bill.get("bill_amount") and bill.get("consumer_name"):
        run._log("✅ No payment due — bill lookup complete")
        run._phase("done")
        return True

    want_pay = (
        str(params.get("pay_bill", "") or config.get("pay_bill", "")).lower()
        in ("yes", "true", "1", "pay")
    )
    if not want_pay:
        choice = await ask_tagged(
            wait_human,
            TAG_PAYMENT_CONFIRM,
            "Bill loaded in Chrome. **Pay now** via UPI/Card/NetBanking, or stop here?",
        )
        want_pay = choice.strip().upper() in ("YES", "Y", "PAY")

    if want_pay:
        run._phase("checkout")
        upi = (params.get("upi_id") or "").strip()
        if not upi:
            upi = await ask_tagged(
                wait_human,
                TAG_TEXT,
                "If paying via **UPI**, enter your UPI ID (or type **SKIP** for other methods):",
                prefilled=params.get("upi_id", ""),
            )
        if upi and upi.upper() != "SKIP":
            await fill_first(
                page,
                (
                    'input[placeholder*="UPI" i]',
                    'input[name*="upi" i]',
                    "#upiId",
                ),
                upi,
            )
        await click_first(
            page,
            (
                'button:has-text("Pay")',
                'button:has-text("Proceed")',
                'input[value*="Pay" i]',
                'a:has-text("Pay Now")',
            ),
        )
        await screenshot(engine, run)
        await ask_tagged(
            wait_human,
            TAG_CONFIRM_DONE,
            "Complete payment in Chrome (UPI/Card/NetBanking). "
            "When you see success or transaction reference, click continue:",
            param="Payment done",
        )

    return True
