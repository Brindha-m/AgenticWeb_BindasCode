import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.playwright_engine import PlaywrightEngine


async def main() -> None:
    engine = PlaywrightEngine(headless=True)
    await engine.launch()
    print("launched")
    res = await engine.navigate("https://example.com")
    print("navigate", res)
    await engine.close()
    print("closed")


if __name__ == "__main__":
    asyncio.run(main())

