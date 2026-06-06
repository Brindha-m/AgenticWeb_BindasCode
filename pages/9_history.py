"""
pages/3_history.py
------------------
Task history viewer with screenshot gallery and JSON export.
Changes: fixed status enum serialisation, added clear history button, better cards.
"""

import base64
import json
import os

import streamlit as st

from ui.streamlit_nav import inject_travel_css, render_sidebar

st.set_page_config(page_title="Task History", page_icon="📜", layout="wide")
inject_travel_css()
render_sidebar(show_engine=False)

st.markdown("""
<style>
.hist-card {
    background:#1a1d23; border:1px solid #2d3139; border-radius:10px;
    padding:14px 18px; margin-bottom:8px;
}
.tag {
    display:inline-block; padding:2px 8px; border-radius:12px;
    font-size:11px; font-weight:600; margin-right:6px;
}
.tag-done    { background:#052e16; color:#22c55e; }
.tag-failed  { background:#2d0e0e; color:#ef4444; }
.tag-running { background:#0c1a3a; color:#60a5fa; }
</style>
""", unsafe_allow_html=True)

st.markdown("# 📜 Task History")

history = st.session_state.get("task_history", [])

if not history:
    st.info("No tasks run yet. Go to the Home page or IRCTC Booking page and run an agent task.")
    st.stop()

hcol1, hcol2 = st.columns([3, 1])
hcol1.markdown(f"**{len(history)} task(s)** run this session")
if hcol2.button("🗑 Clear history", use_container_width=True):
    st.session_state.task_history = []
    st.rerun()

st.divider()

for i, h in enumerate(reversed(history)):
    status  = h.get("status", "unknown")
    engine  = h.get("engine", "playwright")
    task    = h.get("task", "")
    steps   = h.get("steps", [])
    result  = h.get("result", "")
    ts      = h.get("timestamp", "")

    status_icon = {"done":"✅","failed":"❌","running":"🔄"}.get(status,"⚪")
    tag_cls     = f"tag-{status}" if status in ("done","failed","running") else "tag-failed"
    eng_label   = "🎭 Playwright" if engine == "playwright" else ("🔌 Raw CDP" if engine == "cdp" else "🚂 IRCTC")

    with st.expander(f"{status_icon}  [{len(history)-i}]  {task[:70]}", expanded=(i == 0)):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Status",  status.upper())
        c2.metric("Engine",  eng_label)
        c3.metric("Steps",   len(steps))
        c4.metric("Time",    ts[11:19] if len(ts) > 18 else "—")

        if result:
            st.success(f"Result: {result}")

        # Step log
        if steps:
            st.markdown("**Step log:**")
            html = ""
            for s in steps:
                action    = s.get("action", {})
                t         = action.get("type", "?")
                sv        = s.get("status", {})
                sv        = sv.value if hasattr(sv, "value") else str(sv)
                err       = s.get("error", "")
                icon      = {"success":"✅","failed":"❌","done":"🎉","waiting":"⏳"}.get(sv,"•")
                bdr_color = {"success":"#22c55e","failed":"#ef4444","done":"#8b5cf6"}.get(sv,"#475569")
                html += (
                    f'<div style="background:#1a1d23;border-left:3px solid {bdr_color};'
                    f'border-radius:5px;padding:5px 10px;margin-bottom:4px;font-size:12px;color:#94a3b8">'
                    f'<b style="color:#e2e8f0">#{s.get("number","?")} {icon} {t}</b>'
                    + (f' — <span style="color:#ef4444">{err[:55]}</span>' if err else "")
                    + "</div>"
                )
            st.markdown(html, unsafe_allow_html=True)

        # Screenshot gallery
        screenshots = [(s.get("number"), s.get("screenshot_b64")) for s in steps if s.get("screenshot_b64")]
        if screenshots:
            st.markdown("**Screenshots:**")
            cols = st.columns(min(4, len(screenshots)))
            for j, (num, b64) in enumerate(screenshots[:4]):
                cols[j].image(base64.b64decode(b64), caption=f"Step {num}", use_container_width=True)

        # Export
        btn_col1, btn_col2 = st.columns([1, 3])
        with btn_col1:
            export_data = {
                **{k: v for k, v in h.items() if k != "steps"},
                "steps": [
                    {k: (v.value if hasattr(v,"value") else v)
                     for k, v in s.items() if k != "screenshot_b64"}
                    for s in steps
                ],
            }
            st.download_button(
                "⬇ Export JSON",
                data=json.dumps(export_data, indent=2, default=str),
                file_name=f"agent_run_{len(history)-i}.json",
                mime="application/json",
                key=f"dl_{i}",
                use_container_width=True,
            )