"""
app.py — Made It Streamlit UI
----------------------------------
Main entry point. Run with: streamlit run app.py

Changes from v1:
  - Added IRCTC quick-launch card on home page
  - API key can be entered inline and saved to .env
  - Headless toggle now shows warning for IRCTC
  - Agent status bar shows engine badge
  - Step cards show vision-click actions correctly
  - Footer updated with page links
"""

import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from agent import travel_runner
from ui.agent_mode import build_scripted_config, render_mode_selector, validate_ai_api_or_stop
from ui.streamlit_nav import PROJECT_SUBTITLE, PROJECT_TAGLINE, PROJECT_TITLE
from ui.gov_prompts import (
    GOV_QUICK_LAUNCH,
    build_agent_task,
    build_user_task,
    get_prompt_by_id,
    quick_launch_for_ui,
    resolve_agent_task,
)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=True)

# Windows + some Python distributions (incl. conda) can default to an event loop
# that doesn't support subprocesses, which Playwright needs to launch the browser.
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


def page_link(page: str, *, label: str) -> None:
    """Navigate to another Streamlit page."""
    href = "/" if page == "app.py" else f"/{os.path.splitext(os.path.basename(page))[0]}"

    if hasattr(st, "page_link"):
        try:
            st.page_link(page, label=label)
            return
        except KeyError:
            # Streamlit 1.50 can crash when pages aren't registered yet.
            pass

    st.markdown(
        f'<p style="margin:0.15rem 0;">'
        f'<a href="{href}" target="_self" style="color:#fafafa;text-decoration:none;">{label}</a>'
        f"</p>",
        unsafe_allow_html=True,
    )


# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title=PROJECT_TITLE,
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ─────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
.main { background: #0e1117; }

.step-card {
    background: #1a1d23;
    border: 1px solid #2d3139;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 10px;
}
.step-card.success { border-left: 3px solid #22c55e; }
.step-card.failed  { border-left: 3px solid #ef4444; }
.step-card.waiting { border-left: 3px solid #f59e0b; }
.step-card.running { border-left: 3px solid #3b82f6; }
.step-card.done    { border-left: 3px solid #8b5cf6; }

.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
}
.badge-playwright { background: #1d4ed8; color: #bfdbfe; }
.badge-cdp        { background: #7c3aed; color: #ddd6fe; }
.badge-irctc      { background: #065f46; color: #6ee7b7; }

.action-navigate    { color: #60a5fa; }
.action-click       { color: #34d399; }
.action-type        { color: #f59e0b; }
.action-scroll      { color: #a78bfa; }
.action-wait        { color: #94a3b8; }
.action-done        { color: #8b5cf6; }
.action-failed      { color: #ef4444; }
.action-ask_user    { color: #f97316; }
.action-confirm     { color: #f97316; }
.action-vision_click{ color: #34d399; }

.human-input-card {
    background: #1c1a0f;
    border: 1px solid #78350f;
    border-radius: 10px;
    padding: 16px;
    margin: 12px 0;
}

.quick-card {
    background: #1a1d23;
    border: 1px solid #2d3139;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: border-color 0.2s;
}
.quick-card:hover { border-color: #6366f1; }
.quick-card h4 { margin: 0 0 6px 0; color: #e2e8f0; font-size: 15px; }
.quick-card p  { margin: 0; color: #64748b; font-size: 12px; }

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
.pulse { animation: pulse 1.5s ease-in-out infinite; }
</style>
""", unsafe_allow_html=True)

# ─── SESSION STATE ────────────────────────────────────────────────────────────

def init_state():
    defaults = {
        "running": False,
        "steps": [],
        "session": None,
        "orchestrator": None,
        "task_history": [],
        "human_input_needed": None,
        "final_result": "",
        "agent_status": "idle",
        "current_screenshot": "",
        "engine_type": "playwright",
        "log_lines": [],
        "agent_error": "",
        "home_running": False,
        "home_log": [],
        "home_status": "idle",
        "home_human_q": None,
        "home_screenshot": "",
        "home_phases": [],
        "home_error": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(f"## 🎯 {PROJECT_TITLE}")
    st.markdown(f"*{PROJECT_TAGLINE}*")
    st.caption(PROJECT_SUBTITLE)
    st.divider()

    st.markdown("### 🧭 Travel")
    page_link("app.py", label="🏠 Home — General agent")
    page_link("pages/2_irctc.py", label="🚂 IRCTC Railways")
    page_link("pages/3_bus.py", label="🚌 Bus Travel Booking")
    page_link("pages/4_flights.py", label="✈️ Flights")
    page_link("pages/6_government.py", label="🏛️ Government Services")
    st.divider()
    st.markdown("### 🛠 Tools")
    page_link("pages/7_cdp_inspector.py", label="🔌 CDP Inspector")
    page_link("pages/8_compare.py", label="⚖️ Compare engines")
    page_link("pages/9_history.py", label="📜 Task history")
    st.divider()

    st.markdown("### ⚙️ Engine")
    engine_choice = st.radio(
        "Browser backend",
        options=["Playwright", "Raw CDP"],
        captions=["Fast · reliable · production", "Zero abstraction · full control"],
        label_visibility="collapsed",
    )
    st.session_state.engine_type = "playwright" if engine_choice == "Playwright" else "cdp"

    if st.session_state.engine_type == "playwright":
        st.info("**Playwright** wraps CDP with auto-wait and smart selectors.", icon="🎭")
    else:
        st.info("**Raw CDP** speaks directly to Chrome. Enables vision clicks and network interception.", icon="🔌")

    st.divider()

    st.markdown("### ⚙️ Settings")
    headless = st.toggle(
        "Headless browser",
        value=False,
        help="Headless = no visible Chrome window (still works). Turn OFF to watch the browser.",
    )
    if headless:
        st.caption("Headless is ON — Chrome will run in background (no window).")
    else:
        st.warning("Headless is OFF — Chrome window will open on your screen.", icon="👁")
    max_steps = st.slider("Max steps", 5, 50, 30)

    st.divider()

    # API key
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        st.success("✅ API key loaded", icon="🔑")
    else:
        st.error("❌ ANTHROPIC_API_KEY missing")
        api_key_input = st.text_input("Paste API key", type="password", placeholder="sk-ant-...")
        if api_key_input:
            os.environ["ANTHROPIC_API_KEY"] = api_key_input
            # Write to .env so it persists
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            with open(env_path, "w") as f:
                f.write(f"ANTHROPIC_API_KEY={api_key_input}\n")
            st.success("Saved to .env!")
            st.rerun()

    st.divider()

    # Recent history
    if st.session_state.task_history:
        st.markdown("### 📜 Recent tasks")
        for i, h in enumerate(reversed(st.session_state.task_history[-5:])):
            icon = "✅" if h["status"] == "done" else "❌"
            label = h["task"][:32] + ("..." if len(h["task"]) > 32 else "")
            if st.button(f"{icon} {label}", key=f"hist_{i}", use_container_width=True):
                st.session_state.steps = h["steps"]
                st.session_state.final_result = h.get("result", "")

# ─── MAIN ────────────────────────────────────────────────────────────────────

st.markdown(f"# 🎯 {PROJECT_TITLE}")
st.markdown(f"**{PROJECT_TAGLINE}**")
st.markdown(PROJECT_SUBTITLE)

# ── QUICK LAUNCH (8 government services) ───────────────────────────────────

st.markdown("### 🚀 Quick launch")
st.caption(
    "Click a card to open a **dedicated agent page** (live log + browser view + OTP/CAPTCHA help). "
    "IRCTC opens Railways; other services (including **State Transport**) open Government Services."
)


def _run_quick_launch(item: dict) -> None:
    if item.get("page"):
        gov_id = item.get("gov_id")
        if gov_id:
            st.session_state.gov_selected_id = gov_id
        st.switch_page(item["page"])
    else:
        st.session_state.task_input_text = item["task"]
        st.session_state._ql_internal_task = item.get("agent_task", "")
        st.session_state._ql_user_task_snapshot = item["task"]
        st.session_state.ql_selected_id = item.get("id")
        st.rerun()


QUICK_LAUNCH = quick_launch_for_ui()

# Row 1
r1c = st.columns(4)
for col, item in zip(r1c, QUICK_LAUNCH[:4]):
    with col:
        if st.button(
            f"{item['emoji']} {item['title']}",
            use_container_width=True,
            help=item["desc"],
            key=f"ql_{item['id']}_1",
        ):
            _run_quick_launch(item)

# Row 2
r2c = st.columns(4)
for col, item in zip(r2c, QUICK_LAUNCH[4:]):
    with col:
        if st.button(
            f"{item['emoji']} {item['title']}",
            use_container_width=True,
            help=item["desc"],
            key=f"ql_{item['id']}_2",
        ):
            _run_quick_launch(item)

with st.expander("ℹ️ What each quick launch does"):
    _ql_titles = [g["title"] for g in GOV_QUICK_LAUNCH]
    _default_idx = 0
    if st.session_state.get("ql_selected_id"):
        for _i, _g in enumerate(GOV_QUICK_LAUNCH):
            if _g["id"] == st.session_state.ql_selected_id:
                _default_idx = _i
                break
    _picked = st.selectbox("Service", _ql_titles, index=_default_idx, key="ql_prompt_ref")
    _gov = GOV_QUICK_LAUNCH[_ql_titles.index(_picked)]
    if _gov.get("page") == "pages/2_irctc.py":
        st.info("Opens the dedicated IRCTC booking page with login, search, and payment help.")
    elif _gov.get("page"):
        st.info(
            "Opens **Government Services** with live agent log, browser screenshot, "
            "and human-in-the-loop — same pattern as IRCTC."
        )
    else:
        st.markdown(build_user_task(_gov))
        if _gov["navigation_steps"]:
            st.markdown("**Steps the agent follows**")
            for _n, _step in enumerate(_gov["navigation_steps"], 1):
                st.markdown(f"{_n}. {_step}")

st.divider()

home_use_scripted = render_mode_selector(key="home_agent_mode")
_ql_id = st.session_state.get("ql_selected_id")
if _ql_id:
    _ql_svc = get_prompt_by_id(_ql_id)
    if _ql_svc:
        st.caption(f"Quick launch service: **{_ql_svc['emoji']} {_ql_svc['title']}**")
if home_use_scripted and not _ql_id:
    st.info(
        "Scripted mode on Home: click a **Quick launch** card above first "
        "(or use **Government Services** / **Bus Travel Booking** / **Flights** pages for full forms)."
    )

# ── TASK INPUT ────────────────────────────────────────────────────────────

prefill = st.session_state.pop("_prefill_task", "")
if "task_input_text" not in st.session_state:
    st.session_state.task_input_text = ""
if prefill:
    st.session_state.task_input_text = prefill

col1, col2 = st.columns([3, 1])

with col1:
    task_input = st.text_area(
        "What should the agent do?",
        placeholder=(
            "Examples:\n"
            "• Check TTD Tirupati darshan slots for next week\n"
            "• Track India Post parcel (consignment number)\n"
            "• View TNPDCL electricity bill on tnebnet.org (stop before payment)\n"
            "• Check CBSE / university exam results"
        ),
        height=120,
        label_visibility="visible",
        key="task_input_text",
    )

with col2:
    st.markdown("<br>", unsafe_allow_html=True)
    engine_label = "🎭 Playwright" if st.session_state.engine_type == "playwright" else "🔌 Raw CDP"
    badge_cls    = "badge-playwright" if st.session_state.engine_type == "playwright" else "badge-cdp"
    st.markdown(
        f'<div style="text-align:center;margin-bottom:10px">'
        f'<span class="badge {badge_cls}">{engine_label}</span></div>',
        unsafe_allow_html=True,
    )
    has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip()) or bool(
        os.getenv("OPENAI_API_KEY", "").strip()
    )
    has_task = bool(task_input.strip())
    disable_reason = None
    if st.session_state.running or st.session_state.get("home_running"):
        disable_reason = "Agent already running."
    elif home_use_scripted and not _ql_id:
        disable_reason = "Scripted mode: pick a Quick launch card, or switch to AI mode."
    elif not home_use_scripted and not has_key:
        disable_reason = "AI mode: add API key in sidebar, or switch to Scripted mode."
    run_btn = st.button(
        "▶ Run agent",
        type="primary",
        use_container_width=True,
        disabled=disable_reason is not None,
    )
    stop_btn = st.button(
        "⏹ Stop",
        use_container_width=True,
        disabled=not st.session_state.running,
    )
    if disable_reason:
        st.caption(disable_reason)
    elif not has_task:
        st.caption("Type a task (or click a Quick launch card), then click Run.")

# IRCTC shortcut notice
if task_input and "irctc" in task_input.lower() and "book" in task_input.lower():
    st.info(
        "💡 For full IRCTC ticket booking (with login, CAPTCHA, OTP, payment), "
        "use the dedicated **[🚂 IRCTC Booking Agent](/4_irctc)** page.",
        icon="🚂",
    )

st.divider()

# ── CONTROLS ─────────────────────────────────────────────────────────────

if run_btn and task_input.strip():
    if home_use_scripted:
        ql = st.session_state.get("ql_selected_id")
        if not ql or ql == "irctc":
            st.warning(
                "Scripted mode needs a Quick launch service (not IRCTC). "
                "Open **Government Services** from the sidebar for the full form."
            )
            st.stop()
        item = get_prompt_by_id(ql)
        if not item:
            st.error("Unknown quick launch service.")
            st.stop()
        page_id = f"home_{ql}"
        portal = item.get("url", "")
        if item.get("fallback_url"):
            portal = portal or item["fallback_url"]
        run_config = build_scripted_config(
            scripted_id=ql,
            portal_url=portal,
            params={},
            category_label=item["title"],
            task=build_agent_task(item),
        )
        travel_runner.start(page_id, run_config, st.session_state, "home")
        st.rerun()
    else:
        validate_ai_api_or_stop()
        st.session_state.running = True
        st.session_state.steps = []
        st.session_state.final_result = ""
        st.session_state.agent_status = "running"
        st.session_state.agent_error = ""
        st.session_state.human_input_needed = None
        st.session_state._current_task = resolve_agent_task(
            task_input.strip(), st.session_state
        )
        st.session_state._max_steps = max_steps
        st.session_state._headless = headless
        st.rerun()

if run_btn and not task_input.strip():
    st.warning("Please type a task first (or click a Quick launch card).")

if stop_btn:
    st.session_state.running = False
    st.session_state.agent_status = "idle"
    if _ql_id:
        travel_runner.stop(f"home_{_ql_id}")
    st.session_state.home_running = False
    st.session_state.home_status = "idle"
    st.rerun()

# ── HOME SCRIPTED (background runner log) ─────────────────────────────────

_home_page_id = f"home_{_ql_id}" if _ql_id else ""
if st.session_state.get("home_running") or (
    _home_page_id and travel_runner.is_running(_home_page_id)
):
    from datetime import timedelta

    travel_runner.sync_ui(st.session_state, _home_page_id, "home")
    st.markdown("### 📋 Scripted agent (Home)")
    h1, h2 = st.columns([1.2, 1])
    with h1:
        for line in st.session_state.get("home_log", [])[-40:]:
            st.text(line)
        hq = st.session_state.get("home_human_q")
        if hq and travel_runner.is_running(_home_page_id):
            with st.form("home_human_form"):
                st.warning(hq)
                ans = st.text_input("Your answer", label_visibility="collapsed")
                if st.form_submit_button("Submit ↵", type="primary"):
                    if ans.strip():
                        travel_runner.provide_human_response(_home_page_id, ans.strip())
                        st.rerun()
    with h2:
        ss = st.session_state.get("home_screenshot", "")
        if ss:
            st.image(base64.b64decode(ss), use_container_width=True, caption="Live browser")
    if st.session_state.get("home_error"):
        st.error(st.session_state.home_error)

    @st.fragment(run_every=timedelta(seconds=1))
    def _home_scripted_refresh():
        if st.session_state.get("home_running") or travel_runner.is_running(_home_page_id):
            travel_runner.sync_ui(st.session_state, _home_page_id, "home")
            if st.session_state.get("home_running") and not travel_runner.is_running(_home_page_id):
                st.session_state.home_running = False

    _home_scripted_refresh()

# ── STATUS BAR ───────────────────────────────────────────────────────────

if st.session_state.get("agent_error"):
    st.error(f"Agent error: {st.session_state.agent_error}")

if st.session_state.agent_status != "idle":
    colors = {
        "running": ("#3b82f6", "🔵 Running"),
        "waiting": ("#f59e0b", "🟡 Waiting for your input"),
        "done":    ("#22c55e", "🟢 Completed"),
        "failed":  ("#ef4444", "🔴 Failed"),
    }
    color, label = colors.get(st.session_state.agent_status, ("#475569", "⚪ Unknown"))
    engine_badge = (
        '<span class="badge badge-playwright">Playwright</span>'
        if st.session_state.engine_type == "playwright"
        else '<span class="badge badge-cdp">Raw CDP</span>'
    )
    st.markdown(
        f'<div style="background:#1a1d23;border:1px solid #2d3139;border-radius:8px;'
        f'padding:10px 16px;margin-bottom:16px;display:flex;align-items:center;gap:12px;">'
        f'<span style="font-weight:600;color:{color}">{label}</span>'
        f'{engine_badge}'
        f'<span style="color:#64748b;margin-left:auto">Steps: {len(st.session_state.steps)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── HUMAN INPUT ──────────────────────────────────────────────────────────

if st.session_state.human_input_needed:
    st.markdown(
        f'<div class="human-input-card">'
        f'<div style="color:#f59e0b;font-weight:700;margin-bottom:8px;">🙋 Agent needs your help</div>'
        f'<div style="color:#d1d5db;">{st.session_state.human_input_needed}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    hi1, hi2 = st.columns([4, 1])
    with hi1:
        human_answer = st.text_input(
            "response", key="human_answer_input",
            placeholder="Type your answer...", label_visibility="collapsed",
        )
    with hi2:
        if st.button("Submit ↵", type="primary", use_container_width=True):
            if st.session_state.orchestrator:
                st.session_state.orchestrator.provide_human_response(human_answer)
            st.session_state.human_input_needed = None
            st.rerun()

# ── RESULT ───────────────────────────────────────────────────────────────

if st.session_state.final_result:
    st.success(f"✅ **Task completed:** {st.session_state.final_result}")

# ── STEPS + SCREENSHOT ───────────────────────────────────────────────────

if st.session_state.steps:
    main_col, screen_col = st.columns([1.4, 1])

    with main_col:
        st.markdown("### 📋 Agent steps")

        for step in reversed(st.session_state.steps):
            action    = step["action"]   if isinstance(step, dict) else step.action
            status    = step["status"]   if isinstance(step, dict) else step.status
            error     = step.get("error", "")      if isinstance(step, dict) else step.error
            num       = step["number"]   if isinstance(step, dict) else step.number
            duration  = step.get("duration_ms", 0) if isinstance(step, dict) else step.duration_ms

            action_type = action.get("type", "unknown")
            status_val  = status.value if hasattr(status, "value") else str(status)

            icons = {"success": "✅", "failed": "❌", "waiting": "⏳", "running": "🔄", "done": "🎉"}
            icon  = icons.get(status_val, "•")

            if action_type == "navigate":
                desc = f"navigate → {action.get('url','')[:55]}"
            elif action_type in ("click", "vision_click"):
                desc = f"{action_type} [{action.get('index','?')}] — {action.get('reason','')[:40]}"
            elif action_type == "type":
                desc = f"type \"{action.get('text','')[:28]}\" → [{action.get('index','?')}]"
            elif action_type == "scroll":
                desc = f"scroll {action.get('direction','down')} {action.get('amount','')}px"
            elif action_type == "wait":
                desc = f"wait {action.get('seconds','')}s — {action.get('reason','')[:40]}"
            elif action_type == "ask_user":
                desc = f"❓ {action.get('question','')[:55]}"
            elif action_type == "confirm":
                desc = f"⚠️ {action.get('summary','')[:55]}"
            elif action_type == "done":
                desc = f"✅ {action.get('result','')[:55]}"
            else:
                desc = action_type

            dur_str = f"{duration}ms" if duration else ""

            st.markdown(
                f'<div class="step-card {status_val}">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                f'<span style="color:#64748b;font-size:12px">#{num}</span>'
                f'<span>{icon}</span>'
                f'<span class="action-{action_type}" style="font-weight:600">{action_type}</span>'
                f'<span style="color:#64748b;font-size:11px;margin-left:auto">{dur_str}</span>'
                f'</div>'
                f'<div style="color:#94a3b8;font-size:13px">{desc}</div>'
                + (f'<div style="color:#ef4444;font-size:12px;margin-top:4px">⚠ {error}</div>' if error else "")
                + "</div>",
                unsafe_allow_html=True,
            )

    with screen_col:
        st.markdown("### 📸 Live view")

        latest_ss = next(
            (
                (step.get("screenshot_b64","") if isinstance(step,dict) else step.screenshot_b64)
                for step in reversed(st.session_state.steps)
                if (step.get("screenshot_b64","") if isinstance(step,dict) else step.screenshot_b64)
            ),
            "",
        )
        if latest_ss:
            st.image(base64.b64decode(latest_ss), use_container_width=True, caption="Latest page state")

        # Step scrubber
        step_nums = [
            (s["number"] if isinstance(s,dict) else s.number)
            for s in st.session_state.steps
            if (s.get("screenshot_b64","") if isinstance(s,dict) else s.screenshot_b64)
        ]
        if len(step_nums) > 1:
            st.markdown("**Browse steps:**")
            sel = st.select_slider("Step", options=step_nums, value=step_nums[-1],
                                   label_visibility="collapsed")
            for s in st.session_state.steps:
                n  = s["number"] if isinstance(s,dict) else s.number
                ss = s.get("screenshot_b64","") if isinstance(s,dict) else s.screenshot_b64
                if n == sel and ss:
                    st.image(base64.b64decode(ss), use_container_width=True)
                    break

# ── ASYNC RUNNER ─────────────────────────────────────────────────────────

async def run_agent_async():
    from agent.playwright_engine import PlaywrightEngine
    from agent.cdp_engine        import CDPEngine
    from agent.orchestrator      import Orchestrator, AgentSession

    task        = st.session_state._current_task
    engine_type = st.session_state.engine_type
    headless    = st.session_state._headless
    max_steps   = st.session_state._max_steps

    engine = PlaywrightEngine(headless=headless) if engine_type == "playwright" else CDPEngine(headless=headless)

    try:
        await engine.launch()
        session = AgentSession(task=task, engine_type=engine_type)
        orch    = Orchestrator(engine=engine, session=session, max_steps=max_steps)
        st.session_state.orchestrator = orch

        async for step in orch.run():
            step_dict = {
                "number":        step.number,
                "action":        step.action,
                "status":        step.status,
                "result":        step.result,
                "screenshot_b64":step.screenshot_b64,
                "error":         step.error,
                "duration_ms":   step.duration_ms,
            }
            st.session_state.steps.append(step_dict)
            st.session_state.current_screenshot = step.screenshot_b64

            sv = step.status.value if hasattr(step.status, "value") else str(step.status)
            if sv == "waiting":
                st.session_state.human_input_needed = orch.session.human_input_needed
                st.session_state.agent_status = "waiting"
            elif sv == "done":
                st.session_state.agent_status = "done"
                st.session_state.final_result = orch.session.final_result
            elif sv == "failed":
                st.session_state.agent_status = "failed"

    finally:
        await engine.close()
        st.session_state.running = False
        st.session_state.task_history.append({
            "task":      st.session_state._current_task,
            "status":    st.session_state.agent_status,
            "steps":     st.session_state.steps,
            "result":    st.session_state.final_result,
            "timestamp": datetime.now().isoformat(),
            "engine":    engine_type,
        })


if st.session_state.running and "_current_task" in st.session_state:
    st.session_state.agent_status = "running"
    engine_label = "Playwright" if st.session_state.engine_type == "playwright" else "Raw CDP"
    with st.spinner(f"Agent running ({engine_label}) — browser is launching. Keep this tab open."):
        try:
            asyncio.run(run_agent_async())
        except Exception as e:
            st.session_state.agent_error  = str(e)
            st.session_state.running      = False
            st.session_state.agent_status = "failed"
    st.rerun()

# ─── FOOTER ──────────────────────────────────────────────────────────────────
# (Removed per request)