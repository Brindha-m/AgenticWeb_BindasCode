"""Streamlit panel for scripted/AI agent human-in-the-loop responses (IRCTC-style)."""

from __future__ import annotations

import re
import time

import streamlit as st

from agent import travel_runner
from agent.human_prompts import (
    TAG_CAPTCHA,
    TAG_CONFIRM_DONE,
    TAG_LOGIN_FORM,
    TAG_OTP,
    TAG_PAYMENT_CONFIRM,
    TAG_PILGRIM_FORM,
    TAG_TEXT,
    parse_human_prompt,
    pilgrim_count_from_param,
)


def sync_runner_state(page_id: str, prefix: str) -> None:
    if st.session_state.get(f"{prefix}_running") or travel_runner.is_running(page_id):
        travel_runner.sync_ui(st.session_state, page_id, prefix)


def _submit(page_id: str, prefix: str, payload: str) -> None:
    if travel_runner.provide_human_response(page_id, payload):
        st.session_state._human_sent = True
        st.session_state._human_sent_at = time.time()
        st.session_state[f"{prefix}_status"] = "running"
        st.session_state[f"{prefix}_human_q"] = None
        st.rerun()


def _render_header(alive: bool, status: str) -> None:
    st.markdown("---")
    st.markdown("### 🙋 **Agent needs your input**")
    if st.session_state.get("_human_sent"):
        st.success("✅ Sent — agent is processing…")
    elif alive or status == "waiting":
        st.warning("🟡 **Agent is waiting** — fill the form below (no need to type DONE in a text box).")
    else:
        st.info("Agent stopped. Click **Start agent** again if Chrome is still open.")


def _render_confirm_done(prefix: str, page_id: str, parsed, alive: bool) -> None:
    label = parsed.param or parsed.message or "✅ Continue"
    message = parsed.message if parsed.param else ""
    st.caption(
        "**Streamlit trip fields do not control Chrome.** Fill or verify the form in the "
        "**Chrome window**, then click the button below (do not type DONE in a text box)."
    )
    if message:
        st.markdown(message)
    b1, b2 = st.columns(2)
    with b1:
        if st.button(
            label if label.startswith("✅") else f"✅ {label}",
            type="primary",
            use_container_width=True,
            disabled=not alive,
            key=f"{prefix}_confirm_done",
        ):
            _submit(page_id, prefix, "DONE")
    with b2:
        if st.button(
            "Cancel",
            use_container_width=True,
            disabled=not alive,
            key=f"{prefix}_confirm_cancel",
        ):
            _submit(page_id, prefix, "CANCEL")


def _render_payment_confirm(prefix: str, page_id: str, parsed, alive: bool) -> None:
    st.markdown(parsed.message or "Review the payment amount in Chrome, then confirm.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "✅ Confirm & proceed to pay",
            type="primary",
            use_container_width=True,
            disabled=not alive,
            key=f"{prefix}_pay_yes",
        ):
            _submit(page_id, prefix, "YES")
    with c2:
        if st.button(
            "Cancel booking",
            use_container_width=True,
            disabled=not alive,
            key=f"{prefix}_pay_no",
        ):
            _submit(page_id, prefix, "NO")


def _render_otp(prefix: str, page_id: str, parsed, alive: bool) -> None:
    with st.form(f"{prefix}_otp_form"):
        st.markdown(parsed.message or "Enter the OTP sent to your mobile.")
        st.caption(
            "Enter the **6-digit OTP from SMS**. For optional steps, type **SKIP** if no OTP is required."
        )
        otp = st.text_input(
            "OTP from SMS",
            placeholder="6-digit OTP",
            max_chars=8,
            label_visibility="visible",
        )
        if st.form_submit_button("Submit OTP ↵", type="primary", use_container_width=True):
            if otp.strip():
                _submit(page_id, prefix, otp.strip())
            else:
                st.error("Enter the OTP.")


def _render_login_form(prefix: str, page_id: str, parsed, alive: bool) -> None:
    param = (parsed.param or "").lower()
    mobile_only = param == "mobile"
    otp_mode = "otp" in param and not mobile_only
    with st.form(f"{prefix}_login_form"):
        st.markdown(parsed.message or ("TTD login" if otp_mode or mobile_only else "Portal login"))
        if mobile_only:
            st.caption(
                "**Step 1:** Enter your registered mobile. The agent types it in **Chrome** "
                "and clicks **Get OTP**. You will be asked for the SMS OTP next."
            )
            mobile = st.text_input("Mobile number", placeholder="10-digit TTD registered mobile")
            if st.form_submit_button("Submit mobile & get OTP ↵", type="primary", use_container_width=True):
                if mobile.strip():
                    _submit(page_id, prefix, mobile.strip())
                else:
                    st.error("Enter your registered mobile number.")
        elif otp_mode:
            mobile = st.text_input("Mobile number", placeholder="10-digit mobile")
            otp = st.text_input("OTP", placeholder="OTP from SMS")
            if st.form_submit_button("Submit login ↵", type="primary", use_container_width=True):
                if mobile.strip() and otp.strip():
                    _submit(page_id, prefix, f"{mobile.strip()}|{otp.strip()}")
                else:
                    st.error("Enter mobile and OTP.")
        else:
            user = st.text_input("Username / mobile", placeholder="Login id")
            password = st.text_input("Password", type="password", placeholder="Password")
            if st.form_submit_button("Submit login ↵", type="primary", use_container_width=True):
                if user.strip() and password.strip():
                    _submit(page_id, prefix, f"{user.strip()}|{password.strip()}")
                else:
                    st.error("Enter both fields.")


def _render_pilgrim_form(prefix: str, page_id: str, parsed, alive: bool) -> None:
    count = pilgrim_count_from_param(parsed.param, default=2)
    id_proof_options = ["Aadhaar Card", "Aadhaar", "PAN", "Passport", "Driving License", "Voter ID"]
    gender_options = ["Female", "Male", "Transgender"]
    with st.form(f"{prefix}_pilgrim_form"):
        st.markdown(parsed.message or f"Enter details for **{count} pilgrim(s)**")
        st.caption(
            "TTD requires **Gender** and **Photo ID Proof** dropdowns plus **Photo ID number**. "
            "Use **Aadhaar** / **Aadhaar Card** for Photo ID proof when booking darshan."
        )
        blocks: list[tuple[str, str, int, str, str]] = []
        for i in range(count):
            st.markdown(f"**Pilgrim {i + 1}**")
            c1, c2, c3 = st.columns(3)
            with c1:
                name = st.text_input("Full name", key=f"{prefix}_pname_{i}")
            with c2:
                age = st.number_input("Age", min_value=1, max_value=99, value=30, key=f"{prefix}_page_{i}")
            with c3:
                gender = st.selectbox("Gender", gender_options, key=f"{prefix}_pgender_{i}")
            c4, c5 = st.columns(2)
            with c4:
                id_proof = st.selectbox(
                    "Photo ID proof",
                    id_proof_options,
                    key=f"{prefix}_pidproof_{i}",
                )
            with c5:
                id_number = st.text_input(
                    "Photo ID number",
                    key=f"{prefix}_pidnum_{i}",
                    max_chars=12,
                    placeholder="12-digit Aadhaar",
                )
            blocks.append((name, str(id_number), int(age), gender, id_proof))
        if st.form_submit_button("Submit pilgrim details ↵", type="primary", use_container_width=True):
            payload_parts = []
            for name, id_number, age, gender, id_proof in blocks:
                if not name.strip() or not id_number.strip():
                    st.error("Fill **name** and **photo ID number** for every pilgrim.")
                    break
                if id_proof in ("Aadhaar", "Aadhaar Card") and (
                    len(re.sub(r"\D", "", id_number.strip())) != 12
                ):
                    st.error("Aadhaar must be **12 digits**.")
                    break
                clean_id = re.sub(r"\D", "", id_number.strip())
                proof = "Aadhaar Card" if id_proof in ("Aadhaar", "Aadhaar Card") else id_proof
                payload_parts.append(
                    f"{name.strip()}|{clean_id}|{age}|{gender}|{proof}"
                )
            else:
                _submit(page_id, prefix, "||".join(payload_parts))


def _render_captcha_form(prefix: str, page_id: str, parsed, alive: bool) -> None:
    st.caption(
        "Read the CAPTCHA from the **live browser** or the cropped image below. "
        "Click **Refresh** on the TNPDCL page if the image is unclear."
    )
    shot = st.session_state.get(f"{prefix}_screenshot", "")
    if shot:
        try:
            st.image(f"data:image/png;base64,{shot}", caption="CAPTCHA (from browser)")
        except Exception:
            pass
    with st.form(f"{prefix}_captcha_form"):
        st.markdown(parsed.message or "Enter the CAPTCHA exactly as shown.")
        answer = st.text_input(
            "CAPTCHA",
            placeholder="e.g. Ab3X9",
            max_chars=12,
            label_visibility="collapsed",
        )
        if st.form_submit_button("Submit CAPTCHA ↵", type="primary", use_container_width=True):
            if answer.strip():
                _submit(page_id, prefix, answer.strip())
            else:
                st.error("Enter the CAPTCHA text.")


def _render_text_form(prefix: str, page_id: str, parsed, alive: bool) -> None:
    is_password = "password" in (parsed.message or "").lower()
    with st.form(f"{prefix}_text_form_{hash(parsed.raw) % 10000}"):
        st.markdown(parsed.message or parsed.raw)
        answer = st.text_input(
            "Your answer",
            type="password" if is_password else "default",
            label_visibility="collapsed",
            placeholder="Type your answer…",
        )
        if st.form_submit_button("Submit ↵", type="primary", use_container_width=True):
            if answer.strip():
                _submit(page_id, prefix, answer.strip())
            else:
                st.error("Please enter a value.")


def render_human_input_panel(
    *,
    page_id: str,
    prefix: str,
) -> None:
    """
    IRCTC-style structured input when the background agent is waiting.
    Render outside @st.fragment (main page body).
    """
    hq = st.session_state.get(f"{prefix}_human_q")
    status = st.session_state.get(f"{prefix}_status", "idle")
    alive = travel_runner.is_running(page_id)

    if not hq and status == "waiting":
        for line in reversed(st.session_state.get(f"{prefix}_log", [])):
            if "HUMAN INPUT NEEDED:" in line:
                hq = line.split("HUMAN INPUT NEEDED:", 1)[1].strip()
                st.session_state[f"{prefix}_human_q"] = hq
                break

    if not hq:
        return

    parsed = parse_human_prompt(hq)
    _render_header(alive, status)

    if parsed.tag == TAG_CONFIRM_DONE:
        _render_confirm_done(prefix, page_id, parsed, alive)
    elif parsed.tag == TAG_PAYMENT_CONFIRM:
        _render_payment_confirm(prefix, page_id, parsed, alive)
    elif parsed.tag == TAG_OTP:
        _render_otp(prefix, page_id, parsed, alive)
    elif parsed.tag == TAG_LOGIN_FORM:
        _render_login_form(prefix, page_id, parsed, alive)
    elif parsed.tag == TAG_PILGRIM_FORM:
        _render_pilgrim_form(prefix, page_id, parsed, alive)
    elif parsed.tag == TAG_CAPTCHA:
        _render_captcha_form(prefix, page_id, parsed, alive)
    else:
        _render_text_form(prefix, page_id, parsed, alive)
