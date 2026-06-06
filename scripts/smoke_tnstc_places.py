import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.playwright_engine import PlaywrightEngine
from agent.scripted_common import (
    _tnstc_ensure_places_before_search,
    _tnstc_places_ready,
    fill_tnstc_place,
    select_tnstc_date,
)
from agent.travel_runner import TravelRun


async def main():
    run = TravelRun(page_id="state")
    engine = PlaywrightEngine(headless=True)
    await engine.launch()
    await engine.navigate("https://www.tnstc.in/OTRSOnline/")
    page = engine.page
    await asyncio.sleep(2)
    await select_tnstc_date(page, run, "08/06/2026")
    from_p = await fill_tnstc_place(page, run, field_id="matchStartPlace", city="Coimbatore", label="From")
    to_p = await fill_tnstc_place(page, run, field_id="matchEndPlace", city="Chennai", label="To")
    await _tnstc_ensure_places_before_search(page, run, from_place=from_p, to_place=to_p)
    val = await _tnstc_places_ready(page)
    print("places", val)
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
