"""Save TNSTC search results HTML if available."""
import asyncio
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parents[1] / "scripts" / "tnstc_sample.html"


async def fill_place(page, field_id: str, text: str):
    loc = page.locator(f"#{field_id}")
    await loc.click()
    await loc.fill(text)
    await page.dispatch_event(f"#{field_id}", "input")
    await page.dispatch_event(f"#{field_id}", "keyup")
    await asyncio.sleep(2)
    await page.evaluate(
        """
        ([needle]) => {
            const n = needle.toLowerCase();
            let best = null, bestScore = -1;
            for (const el of document.querySelectorAll('.ui-autocomplete li')) {
                const t = (el.innerText || '').trim();
                const tl = t.toLowerCase();
                let score = 0;
                if (tl === n) score = 100;
                else if (tl.startsWith(n)) score = 85;
                else if (tl.includes(n)) score = 70;
                else if (n.includes(tl) && tl.length > 5) score = 50;
                if (score > bestScore) { bestScore = score; best = el; }
            }
            if (best) best.click();
        }
        """,
        [text],
    )


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://www.tnstc.in/OTRSOnline/", wait_until="networkidle", timeout=90000)
        await asyncio.sleep(2)
        await fill_place(page, "matchStartPlace", "Coimbatore")
        await fill_place(page, "matchEndPlace", "Chennai")
        dt = datetime.now()
        date_str = dt.strftime("%d/%m/%Y")
        await page.locator("#txtdeptDateOtrip").click()
        await asyncio.sleep(0.5)
        await page.evaluate(
            f"""
            () => {{
                for (const el of document.querySelectorAll('#ui-datepicker-div td a')) {{
                    if ((el.innerText||'').trim() === '{dt.day}') {{ el.click(); return; }}
                }}
            }}
            """
        )
        await page.locator("#searchButton").click()
        await page.wait_for_load_state("networkidle", timeout=60000)
        await asyncio.sleep(3)
        html = await page.content()
        OUT.write_text(html, encoding="utf-8")
        print("saved", OUT, "url", page.url, "len", len(html))
        # grep interesting strings
        for kw in ["Select", "select", "Service", "seat", "Seat", "onclick", "hiddenAction", "COIMBATORE", "radio"]:
            if kw in html:
                print("has", kw)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
