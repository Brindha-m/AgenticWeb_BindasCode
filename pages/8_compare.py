"""
pages/2_compare.py
------------------
Side-by-side engine comparison.
Changes: fixed asyncio.gather error handling, added metrics table, better layout.
"""

import asyncio, base64, os, time
import streamlit as st
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=True)

from ui.streamlit_nav import inject_travel_css, render_sidebar

st.set_page_config(page_title="Compare Engines", page_icon="⚖️", layout="wide")
inject_travel_css()
render_sidebar(show_engine=False)

st.markdown("""
<style>
.engine-hdr {
    background:#1a1d23; border:1px solid #2d3139; border-radius:10px;
    padding:16px; margin-bottom:14px; text-align:center;
}
.step-mini {
    background:#1a1d23; border-left:3px solid #3b82f6;
    border-radius:5px; padding:6px 10px; margin-bottom:5px;
    font-size:12px; color:#94a3b8;
}
.step-mini.success { border-left-color:#22c55e; }
.step-mini.failed  { border-left-color:#ef4444; }
.step-mini.done    { border-left-color:#8b5cf6; }
</style>
""", unsafe_allow_html=True)

st.markdown("# ⚖️ Engine Comparison")
st.markdown("Run the identical task on both engines simultaneously and compare speed, reliability, and behaviour.")

if not os.getenv("ANTHROPIC_API_KEY"):
    st.error("Set ANTHROPIC_API_KEY in .env or via the Home page sidebar.")
    st.stop()

task = st.text_input(
    "Task for both engines",
    value="Go to example.com and read what the page says",
    placeholder="Enter a web task...",
)

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    max_steps = st.slider("Max steps per engine", 3, 20, 8)
with c2:
    headless  = st.toggle("Headless", value=True)
with c3:
    run_btn   = st.button("▶ Compare both", type="primary", use_container_width=True)

st.divider()

pw_col, cdp_col = st.columns(2)

def engine_header(col, emoji, name, subtitle):
    col.markdown(
        f'<div class="engine-hdr">'
        f'<div style="font-size:26px">{emoji}</div>'
        f'<div style="font-weight:700;color:#e2e8f0;font-size:17px">{name}</div>'
        f'<div style="color:#64748b;font-size:12px">{subtitle}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

engine_header(pw_col,  "🎭", "Playwright", "High-level API · Auto-wait · Smart selectors")
engine_header(cdp_col, "🔌", "Raw CDP",    "Zero abstraction · Vision click · Network intercept")

pw_status  = pw_col.empty()
cdp_status = cdp_col.empty()
pw_steps   = pw_col.empty()
cdp_steps  = cdp_col.empty()
pw_screen  = pw_col.empty()
cdp_screen = cdp_col.empty()
pw_metrics = pw_col.empty()
cdp_metrics= cdp_col.empty()


def render_steps(placeholder, steps):
    html = ""
    for s in steps:
        t      = s["action"].get("type", "?")
        status = s["status"]
        desc   = t
        if t == "navigate":  desc = f"→ {s['action'].get('url','')[:45]}"
        elif t == "click":   desc = f"click [{s['action'].get('index','?')}]"
        elif t == "type":    desc = f"type \"{s['action'].get('text','')[:22]}\""
        icon = {"success":"✅","failed":"❌","done":"🎉","waiting":"⏳"}.get(status,"•")
        html += (
            f'<div class="step-mini {status}">'
            f'<b style="color:#e2e8f0">#{s["number"]}</b> {icon} '
            f'<span style="color:#60a5fa">{t}</span> — {desc}'
            + (f' <span style="color:#ef4444">({s["error"][:35]})</span>' if s.get("error") else "")
            + "</div>"
        )
    placeholder.markdown(html or "<i style='color:#475569'>No steps yet</i>", unsafe_allow_html=True)


async def run_one(engine_type, task, headless, max_steps):
    from agent.playwright_engine import PlaywrightEngine
    from agent.cdp_engine        import CDPEngine
    from agent.orchestrator      import Orchestrator, AgentSession

    engine  = PlaywrightEngine(headless=headless) if engine_type == "playwright" else CDPEngine(headless=headless)
    steps   = []
    t_start = time.time()

    try:
        await engine.launch()
        session = AgentSession(task=task, engine_type=engine_type)
        orch    = Orchestrator(engine=engine, session=session, max_steps=max_steps)
        async for step in orch.run():
            steps.append({
                "number":        step.number,
                "action":        step.action,
                "status":        step.status.value if hasattr(step.status,"value") else str(step.status),
                "error":         step.error,
                "screenshot_b64":step.screenshot_b64,
                "duration_ms":   step.duration_ms,
            })
    finally:
        await engine.close()

    return steps, time.time() - t_start


if run_btn and task.strip():
    async def run_both():
        pw_status.info("🔵 Playwright running...")
        cdp_status.info("🔵 CDP running...")

        results = await asyncio.gather(
            run_one("playwright", task, headless, max_steps),
            run_one("cdp",        task, headless, max_steps),
            return_exceptions=True,
        )

        for i, (st_el, steps_el, screen_el, metrics_el, label) in enumerate([
            (pw_status,  pw_steps,  pw_screen,  pw_metrics,  "Playwright"),
            (cdp_status, cdp_steps, cdp_screen, cdp_metrics, "Raw CDP"),
        ]):
            res = results[i]
            if isinstance(res, Exception):
                st_el.error(f"❌ {label} error: {res}")
                continue

            steps, total = res
            final = next((s for s in reversed(steps) if s["status"] in ("done","failed")), None)
            ok    = final and final["status"] == "done"

            if ok:
                st_el.success(f"✅ {label} done in {total:.1f}s")
            else:
                st_el.error(f"❌ {label} failed after {total:.1f}s")

            render_steps(steps_el, steps)

            last_ss = next((s["screenshot_b64"] for s in reversed(steps) if s.get("screenshot_b64")), None)
            if last_ss:
                screen_el.image(base64.b64decode(last_ss), use_container_width=True)

            ok_n  = sum(1 for s in steps if s["status"] == "success")
            err_n = sum(1 for s in steps if s["status"] == "failed")
            avg   = sum(s["duration_ms"] for s in steps) / max(len(steps),1)
            metrics_el.markdown(
                f"**Steps:** {len(steps)} &nbsp;|&nbsp; ✅ {ok_n} &nbsp;|&nbsp; "
                f"❌ {err_n} &nbsp;|&nbsp; **Avg:** {avg:.0f}ms &nbsp;|&nbsp; **Total:** {total:.1f}s"
            )

    asyncio.run(run_both())