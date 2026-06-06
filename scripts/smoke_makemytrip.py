import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


async def main() -> None:
    from agent.playwright_engine import PlaywrightEngine

    url = (
        "https://www.makemytrip.com/hotels/"
    )
    engine = PlaywrightEngine(headless=False)
    await engine.launch()
    print("launched")
    res = await engine.navigate(url)
    print("navigate", res)
    # keep open briefly to see if it crashes
    await asyncio.sleep(5)
    await engine.close()
    print("closed")


if __name__ == "__main__":
    asyncio.run(main())

