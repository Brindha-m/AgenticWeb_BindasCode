"""
agent/orchestrator.py
---------------------
The main agent loop: observe → plan → act → verify → recover.

This is the intelligence glue between the LLM planner and the browser engine.
It handles:
  - Step-by-step execution with live streaming to Streamlit
  - Recovery when actions fail (retry, re-plan, ask user)
  - Human-in-the-loop for CAPTCHAs / OTPs / payment confirmation
  - Session state persistence across steps
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator, Callable, Optional

from agent.url_utils import extract_primary_url, same_site, url_host, urls_equivalent


class StepStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    WAITING = "waiting"      # waiting for human input
    DONE = "done"


@dataclass
class Step:
    number: int
    action: dict
    status: StepStatus = StepStatus.RUNNING
    result: dict = field(default_factory=dict)
    screenshot_b64: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_ms: int = 0
    error: str = ""


@dataclass
class AgentSession:
    task: str
    engine_type: str           # "playwright" or "cdp"
    steps: list[Step] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)   # LLM conversation history
    status: StepStatus = StepStatus.RUNNING
    final_result: str = ""
    human_input_needed: Optional[str] = None   # question to ask user


class Orchestrator:
    """
    Manages a single agent run.
    Yields Step objects as they complete so Streamlit can display them live.
    """

    def __init__(
        self,
        engine,                          # PlaywrightEngine or CDPEngine instance
        session: AgentSession,
        on_human_input_needed: Callable = None,
        max_steps: int = 30,
    ):
        self.engine = engine
        self.session = session
        self.on_human_input_needed = on_human_input_needed
        self.max_steps = max_steps
        self._failure_counts: dict = {}    # action_key -> count
        self._human_response: Optional[str] = None
        self._start_url = extract_primary_url(session.task)
        self._start_host = url_host(self._start_url or "")
        self._opened_start = False
        self._blocked_same_site_navs = 0

    def provide_human_response(self, response: str):
        """Called by Streamlit when user answers a human-input prompt"""
        self._human_response = response

    async def run(self) -> AsyncGenerator[Step, None]:
        """
        Main agent loop. Yields each Step as it executes.
        Use in Streamlit: async for step in orchestrator.run(): update_ui(step)
        """
        from agent.planner import decide_next_action

        # Open target site once — planner should click/type instead of re-navigating
        if self._start_url and not self._opened_start:
            self._opened_start = True
            nav = await self.engine.navigate(self._start_url)
            if nav.get("success"):
                self.session.history.append({
                    "role": "user",
                    "content": (
                        f"Browser opened at {self._start_url}. "
                        "Stay on this site. Use click, type, scroll, wait, or ask_user — "
                        "do NOT navigate again unless opening a clearly different required page."
                    ),
                })

        for step_num in range(1, self.max_steps + 1):
            # ── 1. OBSERVE ──────────────────────────────────────────────
            page_state = await self.engine.observe()

            stuck_warning = self._build_stuck_warning(page_state)
            if step_num >= 12 and step_num % 4 == 0:
                stuck_warning = (
                    (stuck_warning + "\n") if stuck_warning else ""
                ) + (
                    "Running low on steps: if bill/status/info is visible, use done with a summary. "
                    "If you need consumer number, OTP, or login, use ask_user now."
                )

            # ── 2. PLAN ─────────────────────────────────────────────────
            action = decide_next_action(
                task=self.session.task,
                page_state=page_state,
                history=self.session.history,
                stuck_warning=stuck_warning,
                start_url=self._start_url,
                start_host=self._start_host,
            )
            action = self._guard_action(action, page_state)

            step = Step(
                number=step_num,
                action=action,
                screenshot_b64=page_state["screenshot_b64"],
            )
            t_start = time.time()

            # ── 3. TERMINAL STATES ───────────────────────────────────────
            if action["type"] == "done":
                step.status = StepStatus.DONE
                step.result = {"message": action.get("result", "Task completed")}
                step.duration_ms = int((time.time() - t_start) * 1000)
                self.session.steps.append(step)
                self.session.status = StepStatus.DONE
                self.session.final_result = action.get("result", "Task completed")
                yield step
                return

            if action["type"] == "failed":
                step.status = StepStatus.FAILED
                step.error = action.get("reason", "Unknown failure")
                step.duration_ms = int((time.time() - t_start) * 1000)
                self.session.steps.append(step)
                self.session.status = StepStatus.FAILED
                yield step
                return

            # ── 4. HUMAN-IN-THE-LOOP ─────────────────────────────────────
            if action["type"] in ("ask_user", "confirm"):
                question = action.get("question") or action.get("summary", "")
                step.status = StepStatus.WAITING
                self.session.human_input_needed = question
                self.session.steps.append(step)
                yield step

                # Wait for human response (set via provide_human_response())
                self._human_response = None
                for _ in range(600):      # up to 60s wait
                    await asyncio.sleep(0.1)
                    if self._human_response is not None:
                        break

                user_answer = self._human_response or "(no response)"
                self.session.human_input_needed = None

                # Inject human answer into conversation history
                self.session.history.append({
                    "role": "assistant",
                    "content": f"Asked user: {question}",
                })
                self.session.history.append({
                    "role": "user",
                    "content": f"User responded: {user_answer}",
                })

                step.status = StepStatus.SUCCESS
                step.result = {"human_input": user_answer}
                step.duration_ms = int((time.time() - t_start) * 1000)
                continue

            # ── 5. EXECUTE ACTION ────────────────────────────────────────
            result = await self._execute(action, page_state)
            step.duration_ms = int((time.time() - t_start) * 1000)

            if result.get("success"):
                step.status = StepStatus.SUCCESS
                step.result = result
                action_key = self._action_key(action)
                self._failure_counts[action_key] = 0     # reset failure count

                # Update conversation history with what we did
                self.session.history.append({
                    "role": "assistant",
                    "content": f"Executed: {json.dumps(action)}",
                })
            else:
                step.status = StepStatus.FAILED
                step.error = result.get("error", "Unknown error")

                # Track consecutive failures
                action_key = self._action_key(action)
                self._failure_counts[action_key] = self._failure_counts.get(action_key, 0) + 1

                if self._failure_counts[action_key] >= 3:
                    # Tell LLM we're stuck
                    self.session.history.append({
                        "role": "user",
                        "content": (
                            f"Action {json.dumps(action)} has failed {self._failure_counts[action_key]} times. "
                            f"Last error: {step.error}. Please try a completely different approach."
                        ),
                    })
                    self._failure_counts[action_key] = 0
                else:
                    self.session.history.append({
                        "role": "user",
                        "content": (
                            f"Action {json.dumps(action)} failed: {step.error}. "
                            f"Try again or use a different strategy."
                        ),
                    })

            self.session.steps.append(step)
            yield step

        # Max steps reached
        final_step = Step(
            number=self.max_steps + 1,
            action={"type": "failed"},
            status=StepStatus.FAILED,
            error=f"Reached maximum steps ({self.max_steps}) without completing the task.",
        )
        self.session.steps.append(final_step)
        self.session.status = StepStatus.FAILED
        yield final_step

    async def _execute(self, action: dict, page_state: dict) -> dict:
        """Route action type to the correct engine method"""
        t = action["type"]

        if t == "navigate":
            current = page_state.get("url", "")
            url = action.get("url", "")
            if urls_equivalent(url, current):
                await self.engine.wait(0.8)
                return {"success": True, "skipped": True, "url": current}
            return await self.engine.navigate(url)

        elif t == "click":
            # CDP engine supports vision_click as a fallback
            result = await self.engine.click(action["index"])
            if not result["success"] and hasattr(self.engine, "vision_click"):
                # Fallback: ask LLM to identify click coordinates visually
                return await self.engine.vision_click(
                    page_state["screenshot_b64"],
                    f"click element at index {action['index']}: {action.get('reason', '')}",
                )
            return result

        elif t == "type":
            return await self.engine.type_text(action["index"], action["text"])

        elif t == "select":
            if hasattr(self.engine, "select_option"):
                return await self.engine.select_option(action["index"], action["value"])
            # CDP fallback via JS
            val = json.dumps(action["value"])
            idx = action["index"]
            await self.engine.evaluate(f"""
                (() => {{
                    const selects = [...document.querySelectorAll('select')].filter(e => e.offsetParent);
                    if (selects[{idx}]) {{
                        selects[{idx}].value = {val};
                        selects[{idx}].dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }})()
            """)
            return {"success": True}

        elif t == "scroll":
            return await self.engine.scroll(
                action.get("direction", "down"),
                action.get("amount", 400),
            )

        elif t == "wait":
            return await self.engine.wait(action.get("seconds", 2))

        else:
            return {"success": False, "error": f"Unknown action type: {t}"}

    def _build_stuck_warning(self, page_state: dict) -> str:
        parts = []
        recent = self.session.steps[-6:]
        recent_types = [s.action.get("type") for s in recent]

        if len(recent_types) >= 3 and len(set(recent_types)) == 1:
            parts.append(
                f"WARNING: Same action '{recent_types[0]}' repeated 3+ times. "
                "Try click, type, scroll, ask_user, or done — not the same action again."
            )

        nav_count = sum(1 for s in recent if s.action.get("type") == "navigate")
        if nav_count >= 2:
            parts.append(
                "WARNING: Too many navigate actions. You are already on the site — "
                "use click/type on visible elements. Do NOT reload or change URL."
            )

        if self._start_host and self._start_host in url_host(page_state.get("url", "")):
            parts.append(
                f"You are on the target site ({self._start_host}). "
                "Prefer click and type over navigate."
            )

        return "\n".join(parts)

    def _guard_action(self, action: dict, page_state: dict) -> dict:
        """Block redundant navigations that cause flicker and step waste."""
        if action.get("type") != "navigate":
            return action

        target = action.get("url", "")
        current = page_state.get("url", "")

        if urls_equivalent(target, current):
            return {
                "type": "wait",
                "seconds": 0.5,
                "reason": "Already on this page — skipped duplicate navigate",
            }

        # Same website: never full-page goto — click the link in the UI instead
        if self._opened_start and self._start_host:
            if same_site(target, current) or same_site(target, self._start_url or ""):
                self._blocked_same_site_navs += 1
                return {
                    "type": "scroll",
                    "direction": "down",
                    "amount": 350,
                    "reason": (
                        "Same-site URL change blocked (prevents flicker) — "
                        "click the menu/link on the page instead"
                    ),
                }

        recent_nav = sum(
            1 for s in self.session.steps[-4:] if s.action.get("type") == "navigate"
        )
        if recent_nav >= 1:
            return {
                "type": "wait",
                "seconds": 0.5,
                "reason": "Too many navigations — use click/type on current page",
            }

        return action

    def _action_key(self, action: dict) -> str:
        t = action["type"]
        if t == "navigate":
            return f"nav:{action.get('url', '')[:50]}"
        if t in ("click", "type"):
            return f"{t}:{action.get('index', '')}"
        return t
