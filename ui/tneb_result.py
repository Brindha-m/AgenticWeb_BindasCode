"""Streamlit panel for TNPDCL Quick Pay bill lookup results."""

from __future__ import annotations

import streamlit as st


def render_tneb_bill_result(*, prefix: str) -> None:
    """Show bill status banner when the agent has parsed TNEB Quick Pay results."""
    result = st.session_state.get(f"{prefix}_result") or {}
    if not result:
        return

    status = result.get("status", "")
    headline = result.get("headline", "TNPDCL bill status")
    message = result.get("message", "")
    due = result.get("due_date", "-")
    info = result.get("info", "")
    amount = result.get("bill_amount", "")
    consumer = result.get("consumer_name", "")

    st.markdown("### 💡 Bill status")

    if status == "no_pending":
        st.success(f"**{headline}** — {message or 'NO PENDING BILL'}")
    elif status == "pending" and amount:
        st.warning(f"**{headline}** — {message}")
    else:
        st.info(f"**{headline}**" + (f" — {message}" if message else ""))

    cols = st.columns(3)
    with cols[0]:
        st.metric("Due date", due if due else "-")
    with cols[1]:
        st.metric("Amount due", f"₹{amount}" if amount else "—")
    with cols[2]:
        st.metric("Info", info[:40] + ("…" if len(info) > 40 else "") if info else "—")

    if consumer:
        st.caption(f"Consumer: **{consumer}**")

    if status == "no_pending":
        st.caption("You do not need to pay anything at this time. Chrome shows the same message from TNPDCL.")
