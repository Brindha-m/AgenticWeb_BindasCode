"""Probe TNSTC pages after search."""
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright


async def dismiss(page):
    for sel in ['button:has-text("Close")', ".close", '[data-dismiss="modal"]']:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=1500)
        except Exception:
            pass


async def fill_place(page, field_id: str, text: str):
    loc = page.locator(f"#{field_id}")
    await loc.click()
    await loc.fill(text)
    await asyncio.sleep(1.8)
    await page.evaluate(
        """
        ([needle]) => {
            const n = needle.toLowerCase();
            let best = null, bestScore = -1;
            for (const el of document.querySelectorAll('.ui-autocomplete li')) {
                const t = (el.innerText || '').trim();
                if (!t) continue;
                const tl = t.toLowerCase();
                let score = 0;
                if (tl === n) score = 100;
                else if (tl.startsWith(n)) score = 80;
                else if (tl.includes(n)) score = 60;
                else if (n.includes(tl.slice(0, 6))) score = 40;
                if (score > bestScore) { bestScore = score; best = el; }
            }
            if (best) best.click();
        }
        """,
        [text],
    )
    return await loc.input_value()


async def pick_date(page, date_str: str):
    dt = datetime.strptime(date_str, "%d/%m/%Y")
    await page.locator("#txtdeptDateOtrip").click()
    await asyncio.sleep(0.8)
    for _ in range(16):
        title = await page.evaluate(
            "() => document.querySelector('#ui-datepicker-div .ui-datepicker-title')?.innerText || ''"
        )
        if str(dt.year) in title and dt.strftime("%b")[:3] in title:
            break
        nxt = page.locator("#ui-datepicker-div a.ui-datepicker-next:not(.ui-state-disabled)").first
        if await nxt.count():
            await nxt.click()
            await asyncio.sleep(0.3)
        else:
            break
    await page.evaluate(
        "([d]) => { for (const el of document.querySelectorAll('#ui-datepicker-div td a')) { if ((el.innerText||'').trim()===String(d)) { el.click(); return; } } }",
        [dt.day],
    )


async def dump_page(page, label: str):
    print(f"\n=== {label} url={page.url[:100]} ===")
    info = await page.evaluate(
        """
        () => {
            const btns = [];
            document.querySelectorAll('a, button, input, select').forEach(el => {
                const tag = el.tagName;
                const id = el.id || '';
                const name = el.name || '';
                const type = el.type || '';
                const val = (el.value || '').slice(0, 40);
                const txt = (el.innerText || '').trim().slice(0, 50);
                if (/select|book|seat|proceed|continue|view|login|search|radio/i.test(txt+val+id+name) || tag==='INPUT' && type==='radio')
                    btns.push({tag,id,name,type,val,txt});
            });
            const tables = document.querySelectorAll('table').length;
            const rows = [...document.querySelectorAll('tr')].slice(0,8).map(tr => (tr.innerText||'').trim().slice(0,120));
            return {btns: btns.slice(0, 35), tables, rows, body: (document.body.innerText||'').slice(0, 1200)};
        }
        """
    )
    print("tables:", info["tables"])
    for r in info["rows"][:6]:
        print(" row:", r[:100])
    for b in info["btns"][:20]:
        print(" ", b)
    print("body:", info["body"][:800])


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://www.tnstc.in/OTRSOnline/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        await dismiss(page)
        v = await fill_place(page, "matchStartPlace", "Coimbatore")
        print("from value:", v)
        v2 = await fill_place(page, "matchEndPlace", "Chennai")
        print("to value:", v2)
        await pick_date(page, "08/06/2026")
        await page.locator("#searchButton").click()
        await asyncio.sleep(6)
        await dump_page(page, "after search")
        # try click first select-like
        clicked = await page.evaluate(
            """
            () => {
                for (const el of document.querySelectorAll('a, button, input')) {
                    const t = ((el.innerText||'')+(el.value||'')).trim();
                    if (/^select$/i.test(t) || /^book$/i.test(t)) { el.click(); return t; }
                }
                return '';
            }
            """
        )
        print("clicked:", clicked)
        if clicked:
            await asyncio.sleep(5)
            await dump_page(page, "after select")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
