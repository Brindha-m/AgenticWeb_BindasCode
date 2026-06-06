"""
Background runner for category travel agents (bus, flights, state transport).
Uses Playwright + Orchestrator with live log / screenshot sync.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.irctc_runner import clear_human_response, take_human_response

_lock = threading.Lock()
_runs: dict[str, "TravelRun"] = {}


@dataclass
class TravelRun:
    page_id: str
    log: list[str] = field(default_factory=list)
    status: str = "idle"
    screenshot: str = ""
    phases_done: list[str] = field(default_factory=list)
    human_q: Optional[str] = None
    error: str = ""
    result: dict = field(default_factory=dict)
    thread: Optional[threading.Thread] = None
    orchestrator: Any = None
    _stop: bool = False
    keep_browser_open: bool = False

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _phase(self, key: str) -> None:
        if key not in self.phases_done:
            self.phases_done.append(key)


def get_run(page_id: str) -> Optional[TravelRun]:
    return _runs.get(page_id)


def is_running(page_id: str) -> bool:
    run = _runs.get(page_id)
    return run is not None and run.thread is not None and run.thread.is_alive()


def provide_human_response(page_id: str, text: str) -> bool:
    from agent.irctc_runner import provide_human_response as _provide

    run = _runs.get(page_id)
    if run and run.orchestrator:
        run.orchestrator.provide_human_response(text)
    return _provide(text)


def stop(page_id: str) -> None:
    run = _runs.get(page_id)
    if run:
        run._stop = True
        provide_human_response(page_id, "CANCEL")


def sync_ui(state: Any, page_id: str, prefix: str) -> None:
    run = _runs.get(page_id)
    if not run:
        return

    setattr(state, f"{prefix}_log", list(run.log))
    setattr(state, f"{prefix}_status", run.status)
    setattr(state, f"{prefix}_screenshot", run.screenshot)
    setattr(state, f"{prefix}_phases", list(run.phases_done))
    setattr(state, f"{prefix}_human_q", run.human_q)
    setattr(state, f"{prefix}_result", dict(run.result) if run.result else {})
    if run.error:
        setattr(state, f"{prefix}_error", run.error)


def _update_phases(run: TravelRun, action: dict) -> None:
    t = action.get("type", "")
    if t == "navigate":
        if "open_portal" not in run.phases_done:
            run._phase("open_portal")
        else:
            run._phase("search")
    elif t == "type":
        run._phase("search")
    elif t == "click":
        if "search" in run.phases_done:
            run._phase("select")
        else:
            run._phase("open_portal")
    elif t == "confirm":
        run._phase("checkout")
    elif t == "done":
        run._phase("done")


async def _wait_human(run: TravelRun, page_id: str) -> str:
    """Block until Streamlit submits an answer (shared queue with IRCTC)."""
    for _ in range(6000):
        if run._stop:
            return ""
        pending = take_human_response()
        if pending:
            run.human_q = None
            run.status = "running"
            run._log(f"→ human responded: {pending[:40]}")
            return pending
        await asyncio.sleep(0.1)
    return ""


async def _run_scripted_agent(page_id: str, config: dict) -> None:
    from agent.scripted_flows import run_scripted
    from agent.playwright_engine import PlaywrightEngine

    run = _runs[page_id]
    scripted_id = config["scripted_id"]
    portal_url = config.get("portal_url", "")
    headless = config.get("headless", False)
    keep_open = bool(config.get("keep_browser_open", True))
    run.keep_browser_open = keep_open

    clear_human_response()
    run._log(f"Starting scripted {scripted_id} (no Claude API)...")
    run.status = "running"
    run._phase("launch")

    engine = PlaywrightEngine(headless=headless)
    try:
        await engine.launch()
        run._log("✅ Chrome opened")
        run._phase("launch")

        if portal_url and scripted_id != "indiapost":
            nav = await engine.navigate(portal_url)
            if nav.get("success"):
                run._log(f"✅ Opened {portal_url[:60]}")
                run._phase("open_portal")

        async def wait_human(question: str) -> str:
            run._log(f"HUMAN INPUT NEEDED: {question[:200]}")
            run.human_q = question
            run.status = "waiting"
            return await _wait_human(run, page_id)

        await run_scripted(scripted_id, engine, run, config, wait_human)

        try:
            run.screenshot = await engine.get_screenshot_b64() or run.screenshot
        except Exception:
            pass

    except Exception as e:
        run._log(f"❌ Error: {e}")
        run.status = "failed"
        run.error = str(e)
    finally:
        if keep_open and not run._stop:
            run._log("✅ Chrome stays open — complete booking/payment, then click **Stop**")
            while not run._stop:
                await asyncio.sleep(0.5)
        try:
            await engine.close()
        except Exception:
            pass
        run._log("ℹ️ Browser session ended")


async def _run_agent(page_id: str, config: dict) -> None:
    from agent.orchestrator import AgentSession, Orchestrator, StepStatus
    from agent.playwright_engine import PlaywrightEngine

    run = _runs[page_id]
    task = config["task"]
    portal_url = config.get("portal_url", "")
    headless = config.get("headless", False)
    max_steps = int(config.get("max_steps", 22))

    clear_human_response()
    run._log(f"Starting {config.get('category_label', page_id)} agent...")
    run.status = "running"
    run._phase("launch")

    engine = PlaywrightEngine(headless=headless)
    try:
        await engine.launch()
        run._log("✅ Chrome opened")
        run._phase("launch")

        if portal_url:
            nav = await engine.navigate(portal_url)
            if nav.get("success"):
                run._log(f"✅ Opened {portal_url[:60]}")
                run._phase("open_portal")
            else:
                run._log(f"⚠ Navigate: {nav.get('error', 'failed')[:80]}")

        session = AgentSession(task=task, engine_type="playwright")
        orch = Orchestrator(engine=engine, session=session, max_steps=max_steps)
        run.orchestrator = orch

        async for step in orch.run():
            if run._stop:
                run._log("⏹ Stopped by user")
                run.status = "failed"
                break

            obs = await engine.observe()
            run.screenshot = obs.get("screenshot_b64", "") or run.screenshot

            action = step.action
            _update_phases(run, action)

            at = action.get("type", "?")
            if at == "navigate":
                run._log(f"→ navigate {action.get('url', '')[:70]}")
            elif at == "click":
                run._log(f"→ click [{action.get('index')}] {action.get('reason', '')[:50]}")
            elif at == "type":
                run._log(f"→ type \"{str(action.get('text', ''))[:30]}\"")
            elif at == "done":
                run._log(f"✅ {action.get('result', 'Done')[:120]}")
            elif at == "failed":
                run._log(f"❌ {action.get('reason', 'failed')[:120]}")
            elif at in ("ask_user", "confirm"):
                q = action.get("question") or action.get("summary", "")
                run._log(f"HUMAN INPUT NEEDED: {q[:200]}")
                run.human_q = q
                run.status = "waiting"

            sv = step.status.value if hasattr(step.status, "value") else str(step.status)

            if sv == "waiting":
                run.status = "waiting"
                # Wait for human (poll shared queue + orchestrator)
                for _ in range(600):
                    if run._stop:
                        break
                    pending = take_human_response()
                    if pending:
                        orch.provide_human_response(pending)
                        run.human_q = None
                        run.status = "running"
                        run._log(f"→ human responded: {pending[:40]}")
                        break
                    await asyncio.sleep(0.1)
                continue

            if step.error:
                run._log(f"  ⚠ {step.error[:100]}")

            if sv == StepStatus.DONE.value:
                run.status = "done"
                run._phase("done")
                break
            if sv == StepStatus.FAILED.value and action.get("type") == "failed":
                run.status = "failed"
                break

        if run.status == "running":
            run.status = "done" if session.status == StepStatus.DONE else run.status

    except Exception as e:
        run._log(f"❌ Error: {e}")
        run.status = "failed"
        run.error = str(e)
    finally:
        try:
            await engine.close()
        except Exception:
            pass
        run._log("ℹ️ Browser session ended")
        run.orchestrator = None


def start(page_id: str, config: dict, state: Any, prefix: str) -> None:
    global _runs

    if is_running(page_id):
        return

    run = TravelRun(page_id=page_id)
    with _lock:
        _runs[page_id] = run

    def thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if config.get("scripted_id"):
                loop.run_until_complete(_run_scripted_agent(page_id, config))
            else:
                loop.run_until_complete(_run_agent(page_id, config))
        except Exception as e:
            run.error = str(e)
            run.status = "failed"
            run._log(f"❌ {e}")
        finally:
            # Do not touch st.session_state from a background thread — the UI fragment
            # syncs via travel_runner.sync_ui() on the main Streamlit thread.
            loop.close()

    setattr(state, f"{prefix}_running", True)
    setattr(state, f"{prefix}_error", "")
    setattr(state, f"{prefix}_status", "running")
    setattr(state, f"{prefix}_human_q", None)
    setattr(state, f"{prefix}_log", [f"Starting {config.get('category_label', page_id)}..."])
    setattr(state, f"{prefix}_phases", [])

    t = threading.Thread(target=thread_main, daemon=True)
    run.thread = t
    t.start()
