"""Shared UI: choose Scripted (Playwright) vs AI (Claude/OpenAI)."""

from __future__ import annotations

import os

import streamlit as st

MODE_SCRIPTED_LABEL = "⚡ Scripted (Playwright only — no API)"
MODE_AI_LABEL = "🤖 AI (Playwright + Claude/OpenAI)"


def default_is_scripted() -> bool:
    v = os.getenv("DEFAULT_AGENT_MODE", os.getenv("GOV_USE_SCRIPTED", "scripted")).lower()
    return v in ("scripted", "playwright", "1", "true", "yes")


def render_mode_selector(*, key: str, horizontal: bool = True) -> bool:
    """
    Returns True if user chose scripted mode.
    """
    options = [MODE_SCRIPTED_LABEL, MODE_AI_LABEL]
    default_idx = 0 if default_is_scripted() else 1
    choice = st.radio(
        "Automation mode",
        options=options,
        index=default_idx,
        key=key,
        horizontal=horizontal,
        help=(
            "**Scripted**: fixed Playwright steps, OTP/CAPTCHA via yellow box — no Anthropic/OpenAI. "
            "**AI**: Claude or OpenAI decides each click (needs API credits)."
        ),
    )
    if choice == MODE_SCRIPTED_LABEL:
        st.caption("No API key required. Complete OTP/CAPTCHA/login in Chrome when prompted.")
    else:
        st.caption("Requires `ANTHROPIC_API_KEY` or `LLM_PROVIDER=openai` + `OPENAI_API_KEY` in `.env`.")
    return choice == MODE_SCRIPTED_LABEL


def validate_ai_api_or_stop() -> None:
    """Call from Streamlit when AI mode is selected; stops the app if keys missing."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY", "").strip():
            st.error("AI mode: set **OPENAI_API_KEY** in `.env` or switch to Scripted mode.")
            st.stop()
    elif not os.getenv("ANTHROPIC_API_KEY", "").strip():
        st.error(
            "AI mode: set **ANTHROPIC_API_KEY** (with credits) in `.env`, "
            "or set `LLM_PROVIDER=openai`, or switch to Scripted mode."
        )
        st.stop()


def build_scripted_config(
    *,
    scripted_id: str,
    portal_url: str,
    params: dict | None = None,
    category_label: str = "",
    task: str = "",
    headless: bool = False,
) -> dict:
    return {
        "scripted_id": scripted_id,
        "portal_url": portal_url,
        "params": params or {},
        "category_label": category_label,
        "task": task,
        "headless": headless,
        "max_steps": 28,
        "keep_browser_open": True,
        **(params or {}),
    }
