"""
Sidebar navigation grouped by travel category.
"""

from __future__ import annotations

import os

import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROJECT_TITLE = "Made It"
PROJECT_TAGLINE = "Say it once — we get it done."
PROJECT_SUBTITLE = (
    "From IRCTC and Tirupati to FASTag, dedicated agents for the services that matter the most. 🚀"
)


def page_link(page: str, *, label: str) -> None:
    href = "/" if page == "app.py" else f"/{os.path.splitext(os.path.basename(page))[0]}"

    if hasattr(st, "page_link"):
        try:
            st.page_link(page, label=label)
            return
        except KeyError:
            pass

    st.markdown(
        f'<p style="margin:0.15rem 0;">'
        f'<a href="{href}" target="_self" style="color:#e2e8f0;text-decoration:none;">{label}</a>'
        f"</p>",
        unsafe_allow_html=True,
    )


TRAVEL_PAGES = [
    ("app.py", "🏠 Home", "General agent — any website"),
    ("pages/2_irctc.py", "🚂 IRCTC Railways", "Login, CAPTCHA, train booking"),
    ("pages/3_bus.py", "🚌 Bus Travel Booking", "RedBus · AbhiBus"),
    ("pages/4_flights.py", "✈️ Flights", "MakeMyTrip · Goibibo · IndiGo"),
    (
        "pages/6_government.py",
        "🏛️ Government Services",
        "Tirupati · Passport · TNSTC · EB · more",
    ),
]

TOOLS_PAGES = [
    ("pages/7_cdp_inspector.py", "🔌 CDP Inspector"),
    ("pages/8_compare.py", "⚖️ Compare engines"),
    ("pages/9_history.py", "📜 Task history"),
]


def inject_travel_css() -> None:
    st.markdown(
        """
<style>
.main { background: #0e1117; }
.category-pill {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    background: #1e293b;
    color: #94a3b8;
    margin-bottom: 8px;
}
</style>
""",
        unsafe_allow_html=True,
    )


def render_sidebar(*, show_engine: bool = False) -> None:
    """Category-grouped sidebar used on all travel agent pages."""
    with st.sidebar:
        st.markdown(f"## 🎯 {PROJECT_TITLE}")
        st.caption(PROJECT_TAGLINE)
        st.divider()

        st.markdown("### 🧭 Travel")
        for path, label, _hint in TRAVEL_PAGES:
            page_link(path, label=label)

        st.divider()
        st.markdown("### 🛠 Tools")
        for path, label in TOOLS_PAGES:
            page_link(path, label=label)

        if show_engine:
            st.divider()
            st.markdown("### ⚙️ Engine")
            engine = st.radio(
                "Backend",
                ["Playwright", "Raw CDP"],
                label_visibility="collapsed",
            )
            st.session_state.engine_type = "playwright" if engine == "Playwright" else "cdp"
