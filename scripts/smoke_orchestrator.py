import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv


async def main() -> None:
    from agent.playwright_engine import PlaywrightEngine
    from agent.orchestrator import AgentSession, Orchestrator

    # Minimal task should at least launch + navigate once
    task = "Go to https://example.com and tell me the page title."
    engine = PlaywrightEngine(headless=True)
    await engine.launch()

    session = AgentSession(task=task, engine_type="playwright")
    orch = Orchestrator(engine=engine, session=session, max_steps=3)

    try:
        async for step in orch.run():
            print(step.number, step.status, step.action.get("type"), step.error)
    finally:
        await engine.close()

    print("final:", session.status, session.final_result)


if __name__ == "__main__":
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
    print("ANTHROPIC_API_KEY set?", bool(os.getenv("ANTHROPIC_API_KEY")))
    asyncio.run(main())

