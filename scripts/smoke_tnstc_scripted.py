"""Smoke: TNSTC scripted search (no Streamlit)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.scripted_flows import SCRIPTED_REGISTRY, supports_scripted
from agent.playwright_engine import PlaywrightEngine
from agent.travel_runner import TravelRun


class _Wait:
    async def __call__(self, q: str) -> str:
        print("HUMAN:", q[:120])
        if "LOGIN" in q:
            return "SKIP"
        if "OTP" in q or "payment" in q.lower():
            return "SKIP"
        return "DONE"


async def main():
    assert supports_scripted("state")
    assert "state" in SCRIPTED_REGISTRY
    run = TravelRun(page_id="state")
    engine = PlaywrightEngine(headless=True)
    await engine.launch()
    await engine.navigate("https://www.tnstc.in/OTRSOnline/")
    await SCRIPTED_REGISTRY["state"](
        engine,
        run,
        {
            "portal_url": "https://www.tnstc.in/OTRSOnline/",
            "params": {
                "origin": "Coimbatore",
                "destination": "Chennai",
                "date": "10/06/2026",
                "passengers": 1,
                "portal_label": "TNSTC",
            },
        },
        _Wait(),
    )
    for line in run.log[-15:]:
        print(line)
    print("status:", run.status)
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
