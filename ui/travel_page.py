"""
Reusable Streamlit layout: booking form + Agent log + Browser view + Progress.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import streamlit as st

from agent import travel_runner
from ui.agent_mode import build_scripted_config, render_mode_selector, validate_ai_api_or_stop
from ui.human_input import render_human_input_panel, sync_runner_state
from ui.streamlit_nav import inject_travel_css, render_sidebar

if sys.platform == "win32":
    try:
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


def _ensure_state(prefix: str) -> None:
    defaults = {
        f"{prefix}_running": False,
        f"{prefix}_log": [],
        f"{prefix}_status": "idle",
        f"{prefix}_human_q": None,
        f"{prefix}_screenshot": "",
        f"{prefix}_phases": [],
        f"{prefix}_error": "",
        "_human_sent": False,
        "_prev_status": "idle",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def render_travel_category_page(
    *,
    page_id: str,
    prefix: str,
    title: str,
    subtitle: str,
    category_pill: str,
    portal_options: list[tuple[str, str]],
    default_portal: str,
    progress_steps: list[tuple[str, str]],
    build_task: Callable[..., str],
    tips_md: str,
    default_from: str = "Coimbatore",
    default_to: str = "Bangalore",
    show_passengers: bool = True,
    extra_form: Callable[[], dict] | None = None,
    validate_params: Callable[[dict, dict], dict | None] | None = None,
) -> None:
    """
    Full category page with unified Agent log / Browser view / Progress panels.
    """
    inject_travel_css()
    render_sidebar(show_engine=False)
    _ensure_state(prefix)

    st.markdown(f'<span class="category-pill">{category_pill}</span>', unsafe_allow_html=True)
    st.markdown(f"# {title}")
    st.markdown(subtitle)

    st.markdown("### 🎫 Trip details")
    c1, c2, c3 = st.columns(3)
    with c1:
        origin = st.text_input("From", value=default_from, key=f"{prefix}_from")
    with c2:
        destination = st.text_input("To", value=default_to, key=f"{prefix}_to")
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
            options=[p[0] for p in portal_options],
            index=next((i for i, p in enumerate(portal_options) if p[0] == default_portal), 0),
            key=f"{prefix}_portal",
        )
        portal_url = next(p[1] for p in portal_options if p[0] == portal_label)
    with c5:
        passengers = 1
        if show_passengers:
            passengers = st.number_input("Passengers", 1, 6, 1, key=f"{prefix}_pax")
        budget = st.text_input("Max budget (optional)", placeholder="e.g. 2500", key=f"{prefix}_budget")

    extra_params: dict = {}
    if extra_form:
        extra_params = extra_form() or {}

    use_scripted = render_mode_selector(key=f"{prefix}_mode")

    st.divider()
    b1, b2, b3 = st.columns([2, 1, 1])
    status = st.session_state[f"{prefix}_status"]
    labels = {
        "idle": "⚪ Ready — fill details and click Start",
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
        if st.button("⏹ Stop", use_container_width=True, disabled=not st.session_state[f"{prefix}_running"], key=f"{prefix}_stop"):
            travel_runner.stop(page_id)
            st.session_state[f"{prefix}_running"] = False
            st.session_state[f"{prefix}_status"] = "failed"
            st.rerun()

    if st.session_state.get(f"{prefix}_error"):
        st.error(st.session_state[f"{prefix}_error"])

    if start:
        task = build_task(
            origin=origin,
            destination=destination,
            date=journey_date,
            passengers=passengers,
            budget=budget.strip(),
            portal=portal_label,
            **extra_params,
        )
        travel_params = {
            "origin": origin,
            "destination": destination,
            "date": journey_date,
            "passengers": passengers,
            "portal_label": portal_label,
            "budget": budget.strip(),
        }
        if validate_params:
            merged = validate_params(travel_params, extra_params)
            if merged is None:
                st.stop()
            travel_params = merged
        else:
            travel_params.update(extra_params)
        if use_scripted:
            run_config = build_scripted_config(
                scripted_id=page_id,
                portal_url=portal_url,
                params=travel_params,
                category_label=title,
                task=task,
            )
        else:
            validate_ai_api_or_stop()
            run_config = {
                "task": task,
                "portal_url": portal_url,
                "headless": False,
                "max_steps": 24,
                "category_label": title,
            }
        travel_runner.start(page_id, run_config, st.session_state, prefix)
        st.rerun()

    human_banner_ph = st.empty()

    if st.session_state.get(f"{prefix}_running") or travel_runner.is_running(page_id):
        sync_runner_state(page_id, prefix)

    with human_banner_ph.container():
        render_human_input_panel(page_id=page_id, prefix=prefix)

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
            if "📍" in line or "Step" in line:
                return "step"
            if "✅" in line:
                return "ok"
            if "❌" in line:
                return "err"
            if "HUMAN" in line or "waiting" in line.lower():
                return "wait"
            return "info"

        html = "".join(
            f'<div class="log-line {cls(l)}" style="margin:2px 0;font-family:monospace;font-size:12px;">{l}</div>'
            for l in lines[-80:]
        )
        log_ph.markdown(
            f'<div style="background:#0d1117;border:1px solid #2d3139;border-radius:8px;'
            f'padding:14px;height:420px;overflow-y:auto;color:#e2e8f0;">{html}</div>',
            unsafe_allow_html=True,
        )

    def render_progress(phases: list) -> None:
        rows = ""
        for key, label in progress_steps:
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

    _refresh()

    with st.expander("💡 Tips"):
        st.markdown(
            tips_md
            + "\n\n- When the log shows **HUMAN INPUT NEEDED**, use the **structured form** above the log "
            "(OTP, login, confirm buttons) — same as IRCTC."
        )
