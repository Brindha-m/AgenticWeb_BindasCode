"""
Government & utility agents — same live UI pattern as IRCTC / travel pages.
Uses travel_runner (background thread + log + screenshot + human input).
"""

from __future__ import annotations

import base64
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from agent import travel_runner
from ui.agent_mode import build_scripted_config, render_mode_selector, validate_ai_api_or_stop
from ui.gov_prompts import build_agent_task, build_user_task, gov_items_for_streamlit
from ui.human_input import render_human_input_panel, sync_runner_state
from ui.tneb_result import render_tneb_bill_result
from ui.streamlit_nav import inject_travel_css, render_sidebar

if sys.platform == "win32":
    try:
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

GENERIC_PROGRESS = [
    ("launch", "🚀 Launch browser"),
    ("open_portal", "🌐 Open government portal"),
    ("search", "🔍 Navigate & fill forms"),
    ("select", "📋 Complete workflow steps"),
    ("checkout", "💳 Confirm / payment handoff"),
    ("done", "✅ Done"),
]

STATE_TRANSPORT_PORTALS = [
    ("TNSTC", "https://www.tnstc.in/OTRSOnline/"),
    ("KSRTC", "https://ksrtc.in/oprs-web/"),
    ("APSRTC", "https://www.apsrtconline.in/oprs-web/"),
]

STATE_TRANSPORT_PROGRESS = [
    ("launch", "🚀 Launch browser"),
    ("open_portal", "🌐 Open STU portal"),
    ("search", "🔍 Search route & date"),
    ("select", "🛣️ Select service"),
    ("checkout", "👤 Passenger details"),
    ("done", "✅ Ticket / payment handoff"),
]


def _render_state_transport_trip_form(prefix: str) -> dict:
    """Trip fields for TNSTC / KSRTC / APSRTC (shown on Government Services hub)."""
    st.markdown("### 🎫 Trip details")
    c1, c2, c3 = st.columns(3)
    with c1:
        origin = st.text_input("From", value="Coimbatore", key=f"{prefix}_from")
    with c2:
        destination = st.text_input("To", value="Chennai", key=f"{prefix}_to")
    with c3:
        journey_date = st.text_input(
            "Date (DD/MM/YYYY)",
            value=datetime.now().strftime("%d/%m/%Y"),
            key=f"{prefix}_date",
        )
    c4, c5 = st.columns(2)
    with c4:
        portal_label = st.selectbox(
            "Portal",
            options=[p[0] for p in STATE_TRANSPORT_PORTALS],
            index=0,
            key=f"{prefix}_portal",
        )
        portal_url = next(u for n, u in STATE_TRANSPORT_PORTALS if n == portal_label)
    with c5:
        passengers = st.number_input("Passengers", 1, 6, 1, key=f"{prefix}_pax")
        budget = st.text_input("Max budget (optional)", placeholder="e.g. 2500", key=f"{prefix}_budget")
    st.caption(
        "TNSTC scripted mode fills **Source / Destination / Date** on "
        "[OTRS](https://www.tnstc.in/OTRSOnline/). Booking window: **02:30–23:46 IST**."
    )
    return {
        "origin": origin.strip(),
        "destination": destination.strip(),
        "journey_date": journey_date.strip(),
        "date": journey_date.strip(),
        "passengers": int(passengers),
        "portal_label": portal_label,
        "portal_url": portal_url,
        "budget": budget.strip(),
    }


def _ensure_state(prefix: str) -> None:
    defaults = {
        f"{prefix}_running": False,
        f"{prefix}_log": [],
        f"{prefix}_status": "idle",
        f"{prefix}_human_q": None,
        f"{prefix}_screenshot": "",
        f"{prefix}_phases": [],
        f"{prefix}_error": "",
        f"{prefix}_result": {},
        "_human_sent": False,
        "_prev_status": "idle",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _label_for_param(key: str, default: str) -> str:
    if str(default).upper().startswith("ASK_USER"):
        return key.replace("_", " ").title() + " (optional — agent will ask if empty)"
    return key.replace("_", " ").title()


def render_gov_service_hub(*, initial_service_id: Optional[str] = None) -> None:
    """Full hub: pick a gov service, optional params, IRCTC-style live panels."""
    inject_travel_css()
    render_sidebar(show_engine=False)

    services = gov_items_for_streamlit()
    if not services:
        st.error("No government services configured.")
        return

    ids = [s["id"] for s in services]
    titles = [f"{s['emoji']} {s['title']}" for s in services]

    preselect = initial_service_id or st.session_state.pop("gov_selected_id", None)
    if preselect and preselect in ids:
        default_idx = ids.index(preselect)
    else:
        default_idx = 0

    st.markdown('<span class="category-pill">GOVERNMENT & UTILITIES</span>', unsafe_allow_html=True)
    st.markdown("# 🏛️ Government Services Agent")
    st.markdown(
        "Dedicated agents for **Tirupati**, **Passport**, **India Post**, **EB bill**, "
        "**FASTag**, **PAN/GST/LPG**, **exam results**, and **State Transport (TNSTC / KSRTC / APSRTC)** "
        "— with live browser view, agent log, and human-in-the-loop (OTP/CAPTCHA), same as IRCTC."
    )

    picked_title = st.selectbox(
        "Service",
        options=titles,
        index=default_idx,
        key="gov_service_picker",
    )
    service = services[titles.index(picked_title)]
    service_id = service["id"]
    prefix = f"gov_{service_id}"
    page_id = prefix

    _ensure_state(prefix)

    st.markdown(f"**{service.get('category', 'Government')}** · {service['desc']}")
    st.caption(f"Portal: {service['url']}")

    with st.expander("ℹ️ Steps this agent follows", expanded=False):
        for n, step in enumerate(service.get("navigation_steps", []), 1):
            st.markdown(f"{n}. {step}")
        if not service.get("navigation_steps"):
            st.caption("Agent uses the detailed prompt below; ask_user for login/OTP/CAPTCHA.")

    trip_params: dict = {}
    param_values: dict = {}
    if service_id == "state_transport":
        trip_params = _render_state_transport_trip_form(prefix)
        param_values = {k: v for k, v in trip_params.items() if k != "portal_url"}
    else:
        st.markdown("### 📝 Details (optional)")
        st.caption(
            "Leave fields blank and the agent will ask via **structured forms** (OTP, login, pilgrim details). "
            "Set **target date** and **pilgrim count** for Tirupati auto-booking."
        )
        defaults = service.get("defaults", {})
        if defaults:
            cols = st.columns(2)
            for i, (key, default_val) in enumerate(defaults.items()):
                with cols[i % 2]:
                    placeholder = str(default_val) if not str(default_val).upper().startswith("ASK_USER") else ""
                    param_values[key] = st.text_input(
                        _label_for_param(key, str(default_val)),
                        value="" if str(default_val).upper().startswith("ASK_USER") else str(default_val),
                        placeholder=placeholder or "Leave blank to be asked",
                        key=f"{prefix}_param_{key}",
                    ).strip()
        else:
            st.info("No extra fields for this service — the agent will ask as it goes.")

    use_scripted = render_mode_selector(key=f"{prefix}_mode")

    st.markdown("### 📄 Task preview")
    filled = {k: v for k, v in param_values.items() if v}
    user_preview = build_user_task(service, filled if filled else None)
    st.text_area(
        "Plain English task",
        value=user_preview,
        height=100,
        disabled=True,
        label_visibility="collapsed",
    )

    st.divider()
    b1, b2, b3 = st.columns([2, 1, 1])
    status = st.session_state[f"{prefix}_status"]
    labels = {
        "idle": "⚪ Ready — click Start agent",
        "running": "🔵 Agent running — watch Chrome",
        "waiting": "🟡 Waiting for your input below",
        "done": "🟢 Task complete",
        "failed": "🔴 Failed — see log",
    }
    with b1:
        if status == "running":
            st.info(labels["running"])
        elif status == "waiting":
            st.warning(labels["waiting"])
        elif status == "done":
            st.success(labels["done"])
        elif status == "failed":
            st.error(labels["failed"])
        else:
            st.caption(labels["idle"])

    with b2:
        start = st.button(
            "🚀 Start agent",
            type="primary",
            use_container_width=True,
            disabled=st.session_state[f"{prefix}_running"],
            key=f"{prefix}_start",
        )
    with b3:
        if st.button(
            "⏹ Stop",
            use_container_width=True,
            disabled=not st.session_state[f"{prefix}_running"],
            key=f"{prefix}_stop",
        ):
            travel_runner.stop(page_id)
            st.session_state[f"{prefix}_running"] = False
            st.session_state[f"{prefix}_status"] = "failed"
            st.rerun()

    if st.session_state.get(f"{prefix}_error"):
        st.error(st.session_state[f"{prefix}_error"])

    if start:
        extra = {k: v for k, v in param_values.items() if v not in ("", None)}
        if service_id == "state_transport":
            extra = {k: v for k, v in trip_params.items() if v not in ("", None)}
            portal = trip_params.get("portal_url") or service.get("url", "")
            scripted_id = "state"
        else:
            portal = service.get("url", "")
            scripted_id = service_id
        task = build_agent_task(service, extra if extra else None)
        fallback = service.get("fallback_url")
        if fallback and fallback not in task:
            portal = portal or fallback

        if use_scripted:
            run_config = build_scripted_config(
                scripted_id=scripted_id,
                portal_url=portal,
                params=extra,
                category_label=service["title"],
                task=task,
            )
        else:
            validate_ai_api_or_stop()
            run_config = {
                "task": task,
                "portal_url": portal,
                "headless": False,
                "max_steps": 28,
                "category_label": service["title"],
            }

        st.session_state[f"{prefix}_result"] = {}
        travel_runner.start(page_id, run_config, st.session_state, prefix)
        st.rerun()

    human_banner_ph = st.empty()

    if st.session_state.get(f"{prefix}_running") or travel_runner.is_running(page_id):
        sync_runner_state(page_id, prefix)

    with human_banner_ph.container():
        render_human_input_panel(page_id=page_id, prefix=prefix)

    if service_id == "tneb":
        result_ph = st.empty()
        with result_ph.container():
            render_tneb_bill_result(prefix=prefix)

    st.markdown("---")
    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("### 📋 Agent log")
        log_ph = st.empty()
    with right:
        st.markdown("### 📸 Browser view")
        ss_ph = st.empty()
        st.markdown("### 📊 Progress")
        prog_ph = st.empty()

    def render_log(lines: list) -> None:
        def cls(line: str) -> str:
            if "→" in line or "Step" in line:
                return "step"
            if "✅" in line:
                return "ok"
            if "❌" in line:
                return "err"
            if "HUMAN" in line or "waiting" in line.lower():
                return "wait"
            return "info"

        html = "".join(
            f'<div style="margin:2px 0;font-family:monospace;font-size:12px;color:#e2e8f0;">{l}</div>'
            for l in lines[-80:]
        )
        log_ph.markdown(
            f'<div style="background:#0d1117;border:1px solid #2d3139;border-radius:8px;'
            f'padding:14px;height:420px;overflow-y:auto;">{html}</div>',
            unsafe_allow_html=True,
        )

    def render_progress(phases: list) -> None:
        steps = STATE_TRANSPORT_PROGRESS if service_id == "state_transport" else GENERIC_PROGRESS
        rows = ""
        for key, label in steps:
            done = key in phases
            icon = "✅" if done else "⏳"
            color = "#22c55e" if done else "#475569"
            rows += (
                f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;'
                f'border-bottom:1px solid #2d3139;color:{color}">'
                f'<span>{icon}</span><span style="font-size:13px">{label}</span></div>'
            )
        prog_ph.markdown(
            f'<div style="background:#1a1d23;border:1px solid #2d3139;border-radius:8px;padding:12px">{rows}</div>',
            unsafe_allow_html=True,
        )

    @st.fragment(run_every=timedelta(seconds=1))
    def _refresh() -> None:
        if st.session_state.get(f"{prefix}_running") or travel_runner.is_running(page_id):
            prev = st.session_state.get(f"{prefix}_prev_status", "idle")
            sync_runner_state(page_id, prefix)
            if (
                st.session_state[f"{prefix}_status"] == "waiting"
                and prev != "waiting"
                and not st.session_state.get("_human_sent")
            ):
                st.session_state[f"{prefix}_prev_status"] = "waiting"
                st.rerun(scope="app")
            st.session_state[f"{prefix}_prev_status"] = st.session_state[f"{prefix}_status"]
            if st.session_state[f"{prefix}_status"] == "running" and st.session_state.get("_human_sent"):
                st.session_state._human_sent = False
            if (
                st.session_state.get("_human_sent")
                and st.session_state[f"{prefix}_status"] == "waiting"
                and time.time() - st.session_state.get("_human_sent_at", 0) > 4
            ):
                st.session_state._human_sent = False
            if st.session_state[f"{prefix}_running"] and not travel_runner.is_running(page_id):
                st.session_state[f"{prefix}_running"] = False

        log = st.session_state.get(f"{prefix}_log", [])
        if log:
            render_log(log)
        phases = st.session_state.get(f"{prefix}_phases", [])
        if phases or st.session_state.get(f"{prefix}_running"):
            render_progress(phases)
        ss = st.session_state.get(f"{prefix}_screenshot", "")
        if ss:
            with ss_ph.container():
                st.image(base64.b64decode(ss), use_container_width=True, caption="Live browser view")
        if service_id == "tneb":
            with result_ph.container():
                render_tneb_bill_result(prefix=prefix)

    _refresh()

    with st.expander("💡 Tips"):
        tips = """
- **Scripted mode** works for all services on this page — no API key; fill fields above when you can.
- **AI mode** needs Anthropic credits or `LLM_PROVIDER=openai` + `OPENAI_API_KEY` in `.env`.
- **IRCTC Railways** uses its own Playwright agent (sidebar).
- When the log says **HUMAN INPUT NEEDED**, use the **structured form** above the log (OTP, login, pilgrim details, confirm buttons) — same pattern as IRCTC.
- **Tirupati**: set target date + pilgrim count above; agent opens [Sri PAT slot booking](https://ttdevasthanams.ap.gov.in/spat/slot-booking?flow=spat&flowIdentifier=spat), auto-selects date, fills pilgrim details from your form.
"""
        if service_id == "state_transport":
            tips += """
- **State Transport**: pick **TNSTC** for Tamil Nadu routes; do not click the empty **Source** box after the agent fills it (site clears the field on click).
- TNSTC online booking: **02:30–23:46 IST** only.
"""
        st.markdown(tips)
