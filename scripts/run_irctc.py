#!/usr/bin/env python3
"""
Standalone IRCTC Playwright runner — no Streamlit required.

Usage:
  1. Copy .env.example to .env and fill credentials
  2. pip install -r requirements.txt && playwright install chromium
  3. python scripts/run_irctc.py

Engine selection (IRCTC_ENGINE in .env):
  playwright — default, selector-based (like the GitHub JS repos)
  cdp        — legacy vision/CDP agent (uses Claude for vision clicks)

Claude API is used ONLY when CAPTCHA_MODE=claude.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Windows: Playwright subprocess support
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.irctc_config import IRCTCConfig


def main() -> int:
    config = IRCTCConfig.from_env()
    errors = config.validate()
    if errors:
        print("Configuration errors:")
        for err in errors:
            print(f"  • {err}")
        print("\nFix .env (see .env.example) and run again.")
        return 1

    print("=" * 60)
    print("IRCTC Playwright Automation (Python)")
    print("=" * 60)
    print(f"  Engine:     {config.engine}")
    print(f"  Headless:   {config.headless}")
    print(f"  CAPTCHA:    {config.captcha_mode}")
    print(f"  Login only: {config.login_only}")
    if config.login_only:
        print("  ⚠ LOGIN_ONLY=true → will NOT search trains or book tickets")
    if not config.login_only:
        print(f"  Route:      {config.from_station} → {config.to_station}")
        print(f"  Train:      {config.preferred_train or 'first available'}")
        print(f"  Passengers: {len(config.passengers)}")
        print(f"  Payment:    {config.payment_method} via {config.payment_provider}")
        print(f"  Auto-pay:   {not config.stop_before_payment}")
    print("=" * 60)

    if config.engine == "cdp":
        return asyncio.run(_run_cdp(config))
    return asyncio.run(_run_playwright(config))


async def _run_playwright(config: IRCTCConfig) -> int:
    from agent.irctc_playwright import IRCTCPlaywrightBot

    bot = IRCTCPlaywrightBot(config=config)
    try:
        status = await bot.run()
        print(f"\nFinished with status: {status}")
        if status == "done" and config.login_only and config.keep_alive_seconds > 0:
            print("Browser will stay open until you press Enter...")
            await asyncio.get_event_loop().run_in_executor(None, input)
        return 0 if status == "done" else 1
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 130
    except Exception as exc:
        print(f"\nFailed: {exc}")
        print("Chrome left open — fix the issue in the browser, then press Enter to close.")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except EOFError:
            pass
        return 1
    finally:
        if bot.browser:
            await bot.close()


async def _run_cdp(config: IRCTCConfig) -> int:
    """Legacy CDP + vision agent (uses Claude for vision when needed)."""
    from agent.irctc_agent import IRCTCAgent, IRCTCSession

    session = IRCTCSession(
        source=config.from_station,
        destination=config.to_station,
        source_name=config.from_name,
        dest_name=config.to_name,
        date=config.journey_date,
        passengers=len(config.passengers) or 1,
        train_class=config.train_class,
        preferred_train=config.preferred_train,
    )
    agent = IRCTCAgent(session=session)
    try:
        await agent.launch()
        await agent._step_open_irctc()
        # CDP agent reads credentials via Streamlit human form by default;
        # pre-fill from env when available.
        if config.username and config.password:
            if not await agent._ensure_login_modal():
                raise RuntimeError("Could not open login modal")
            await agent._fill_login_credentials(config.username, config.password)
            captcha_mode = config.captcha_mode
            if captcha_mode == "claude":
                # reuse playwright-style captcha screenshot path via evaluate
                pass
            await agent._click_sign_in()
            await asyncio.sleep(4)
        else:
            await agent._step_login()

        if config.login_only:
            if config.keep_alive_seconds > 0:
                end = asyncio.get_event_loop().time() + config.keep_alive_seconds
                while asyncio.get_event_loop().time() < end:
                    await asyncio.sleep(30)
                    await agent.navigate(agent.IRCTC_URL if hasattr(agent, "IRCTC_URL") else "https://www.irctc.co.in/nget/train-search")
            print("CDP login-only complete.")
            return 0

        await agent._step_search_trains()
        await agent._step_select_train()
        await agent._step_add_passengers()
        await agent._step_payment_handoff()
        return 0
    except Exception as exc:
        print(f"CDP run failed: {exc}")
        return 1
    finally:
        await agent.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
