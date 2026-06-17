"""
Background runner for IRCTC agent.
Keeps Streamlit responsive so login prompts and live logs work.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

_active_agent = None
_thread: Optional[threading.Thread] = None
_stop_requested = False
_human_lock = threading.Lock()
_pending_human: Optional[str] = None


def get_active_agent():
    return _active_agent


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def provide_human_response(response: str) -> bool:
    """Queue a human response (thread-safe). Works even if agent is momentarily busy."""
    global _pending_human
    text = (response or "").strip()
    if not text:
        return False
    with _human_lock:
        _pending_human = text
    return True


def take_human_response() -> Optional[str]:
    """Pop the next queued human response (called from agent thread)."""
    global _pending_human
    with _human_lock:
        if _pending_human is None:
            return None
        text = _pending_human
        _pending_human = None
        return text


def clear_human_response() -> None:
    global _pending_human
    with _human_lock:
        _pending_human = None


def stop(close_browser: bool = False) -> None:
    """Stop the agent; by default keep Chrome open so user can finish login manually."""
    global _stop_requested
    _stop_requested = True
    provide_human_response("CANCEL")
    agent = _active_agent
    if agent:
        if close_browser:
            agent._force_close_browser = True


def sync_ui(state: Any) -> None:
    """Copy live agent data into Streamlit session state (call from main thread)."""
    agent = _active_agent
    if not agent:
        return

    session = agent.session
    state.irctc_log = list(getattr(session, "log", []))
    screenshot = getattr(session, "current_screenshot", "")
    if screenshot:
        state.irctc_screenshot = screenshot

    status = getattr(session, "status", "idle")
    if status in ("waiting", "running", "done", "failed"):
        state.irctc_status = status

    if status == "waiting":
        if not getattr(state, "_human_sent", False):
            for line in reversed(state.irctc_log):
                if "HUMAN INPUT NEEDED:" in line:
                    q = line.split("HUMAN INPUT NEEDED:", 1)[1].strip()
                    if "[LOGIN_FORM]" in q:
                        state.irctc_human_q = "[LOGIN_FORM]"
                    elif "[LOGIN_DONE]" in q:
                        state.irctc_human_q = "[LOGIN_DONE]"
                    elif "[SEARCH_DONE]" in q:
                        state.irctc_human_q = "[SEARCH_DONE]"
                    elif "[CONFIRM_DONE]" in q:
                        state.irctc_human_q = "[CONFIRM_DONE]"
                    elif "[AADHAAR_DONE]" in q:
                        state.irctc_human_q = "[AADHAAR_DONE]"
                    elif "[DATE_CARD]" in q:
                        state.irctc_human_q = "[DATE_CARD]"
                    else:
                        state.irctc_human_q = q
                    break
    elif status == "running":
        state.irctc_human_q = None
        state._human_sent = False

    state._irctc_steps_done = list(getattr(session, "steps_done", []))


def _human_callback_for_streamlit(question: str) -> str:
    """Block until Streamlit UI provides a response via provide_human_response()."""
    import time

    for _ in range(int(300 / 0.2)):
        time.sleep(0.2)
        raw = take_human_response()
        if raw is not None:
            return raw
    return ""


async def _run_playwright_booking(config: dict) -> None:
    global _active_agent, _stop_requested

    from agent.irctc_config import IRCTCConfig, Passenger
    from agent.irctc_playwright import IRCTCPlaywrightBot

    _stop_requested = False
    clear_human_response()

    env_cfg = IRCTCConfig.from_env()
    pw_cfg = IRCTCConfig(
        username=env_cfg.username or config.get("irctc_username", ""),
        password=env_cfg.password or config.get("irctc_password", ""),
        from_station=config.get("source", env_cfg.from_station),
        from_name=config.get("source_name", env_cfg.from_name),
        to_station=config.get("destination", env_cfg.to_station),
        to_name=config.get("dest_name", env_cfg.to_name),
        journey_date=config.get("date", env_cfg.journey_date),
        train_class=config.get("train_class", env_cfg.train_class),
        journey_quota=config.get("journey_quota", env_cfg.journey_quota),
        preferred_train=config.get("preferred_train", env_cfg.preferred_train),
        mobile=env_cfg.mobile,
        payment_method=env_cfg.payment_method,
        payment_provider=env_cfg.payment_provider,
        passengers=env_cfg.passengers or [
            Passenger(name="Passenger", age="30", gender="Male")
            for _ in range(int(config.get("passengers", 1)))
        ],
        captcha_mode=env_cfg.captcha_mode,
        headless=env_cfg.headless,
        stop_before_payment=env_cfg.stop_before_payment,
        login_only=env_cfg.login_only,
        keep_alive_seconds=env_cfg.keep_alive_seconds,
        engine="playwright",
        slow_mo=env_cfg.slow_mo,
        anthropic_model=env_cfg.anthropic_model,
    )

    class _PlaywrightAdapter:
        """Thin wrapper so sync_ui() works like IRCTCAgent."""

        def __init__(self, bot: IRCTCPlaywrightBot):
            self.bot = bot
            self._force_close_browser = False

        @property
        def session(self):
            return self.bot

        async def close(self, kill_browser: bool = True):
            if kill_browser:
                await self.bot.close()

        async def disconnect(self):
            pass

    bot = IRCTCPlaywrightBot(
        config=pw_cfg,
        human_callback=_human_callback_for_streamlit,
    )
    adapter = _PlaywrightAdapter(bot)
    _active_agent = adapter
    bot.status = "running"

    try:
        bot._log("Starting IRCTC Playwright agent (credentials from .env)...")
        await bot.run()
    except Exception as e:
        bot._log(f"❌ [ERROR] {e}")
        bot.status = "failed"
        raise
    finally:
        keep_open = (
            bot.status == "done"
            and pw_cfg.stop_before_payment
            and not adapter._force_close_browser
        )
        if adapter._force_close_browser or (bot.status == "done" and not keep_open):
            await bot.close()
        else:
            bot._log("ℹ️ Chrome left open — close the browser window when finished.")
            if bot.status == "failed" and pw_cfg.keep_alive_seconds > 0:
                bot._log(
                    f"ℹ️ Keeping failed browser session visible for "
                    f"{pw_cfg.keep_alive_seconds}s"
                )
                await asyncio.sleep(pw_cfg.keep_alive_seconds)
        _active_agent = None


async def _run_booking(config: dict) -> None:
    global _active_agent, _stop_requested

    from agent.irctc_config import IRCTCConfig

    engine = IRCTCConfig.from_env().engine
    if engine == "playwright":
        await _run_playwright_booking(config)
        return

    from agent.irctc_agent import IRCTCAgent, IRCTCSession

    _stop_requested = False
    clear_human_response()
    session = IRCTCSession(**config)
    agent = IRCTCAgent(session=session)
    agent._force_close_browser = False
    _active_agent = agent
    agent.session.status = "running"

    try:
        agent._log("Starting IRCTC CDP/vision agent...")
        agent._log("Launching Google Chrome (visible window)...")
        await agent.launch()
        agent._log("✅ Chrome opened — watch for the browser window on your taskbar")

        steps = [
            ("open_irctc", agent._step_open_irctc),
            ("login", agent._step_login),
            ("search", agent._step_search_trains),
            ("select_train", agent._step_select_train),
            ("passengers", agent._step_add_passengers),
            ("payment", agent._step_payment_handoff),
        ]

        for step_key, step_fn in steps:
            agent._log(f"\n{'=' * 40}")
            agent.session.status = "running"

            task = asyncio.create_task(step_fn())
            while not task.done():
                await asyncio.sleep(0.5)

            success = await task
            agent.session.steps_done.append(step_key)

            if success is False or agent.session.status == "failed":
                if agent.session.status != "failed":
                    agent.session.status = "failed"
                agent._log(
                    f"❌ Stopped at step '{step_key}' — Chrome is still open. "
                    "Fix login in the browser or click Start again."
                )
                break

        if agent.session.status not in ("failed", "done", "waiting"):
            agent.session.status = "done"

    except Exception as e:
        agent._log(f"❌ [ERROR] {e}")
        agent.session.status = "failed"
        raise
    finally:
        kill = getattr(agent, "_force_close_browser", False) or agent.session.status == "done"
        if kill:
            await agent.close(kill_browser=True)
        else:
            await agent.disconnect()
            agent._log("ℹ️ Chrome left open — close the browser window yourself when finished.")
        _active_agent = None


def start(config: dict, state: Any) -> None:
    """Start the IRCTC agent on a background thread."""
    global _thread

    if is_running():
        return

    def thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_booking(config))
        except Exception as e:
            state.irctc_error = str(e)
            state.irctc_status = "failed"
        finally:
            state.irctc_running = False
            sync_ui(state)
            loop.close()

    state.irctc_running = True
    state.irctc_error = ""
    state.irctc_status = "running"
    state.irctc_human_q = None
    state._human_sent = False
    _thread = threading.Thread(target=thread_main, daemon=True)
    _thread.start()
