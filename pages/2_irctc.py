"""
pages/2_irctc.py — IRCTC Railways (dedicated vision + human-in-the-loop booking).
"""

import base64
import os
import sys
import time
from datetime import datetime, timedelta

import streamlit as st
from dotenv import load_dotenv

from agent import irctc_runner
from ui.streamlit_nav import inject_travel_css, render_sidebar

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=True)

if sys.platform == "win32":
    try:
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

st.set_page_config(page_title="IRCTC Railways", page_icon="🚂", layout="wide")
inject_travel_css()
render_sidebar(show_engine=False)

st.markdown("""
<style>
.log-box {
    background: #0d1117;
    border: 1px solid #2d3139;
    border-radius: 8px;
    padding: 14px;
    font-family: 'Courier New', monospace;
    font-size: 12px;
    height: 420px;
    overflow-y: auto;
    color: #e2e8f0;
}
.log-line { margin: 2px 0; line-height: 1.5; }
.log-line.step   { color: #60a5fa; font-weight: bold; }
.log-line.ok     { color: #22c55e; }
.log-line.err    { color: #ef4444; }
.log-line.wait   { color: #f59e0b; }
.log-line.info   { color: #94a3b8; }

.human-box {
    background: #1c1508;
    border: 1px solid #92400e;
    border-radius: 10px;
    padding: 16px;
    margin: 12px 0;
}
.status-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
}
.badge-running { background:#1e3a5f; color:#60a5fa; }
.badge-waiting { background:#3b2008; color:#f59e0b; }
.badge-done    { background:#052e16; color:#22c55e; }
.badge-failed  { background:#2d0e0e; color:#ef4444; }
.badge-idle    { background:#1a1d23; color:#64748b; }

.warning-box {
    background: #1c1508;
    border-left: 4px solid #f59e0b;
    border-radius: 4px;
    padding: 12px 16px;
    margin: 12px 0;
    color: #d97706;
    font-size: 13px;
}
</style>
""", unsafe_allow_html=True)

# ─── HEADER ──────────────────────────────────────────────────────────────────

st.markdown("# 🚂 IRCTC Booking Agent")
st.markdown(
    "Playwright automation (Python) with credentials and passengers from `.env`. "
    "Claude API is only used when `CAPTCHA_MODE=claude`."
)

st.markdown("""
<div class="warning-box">
⚠️ <strong>Important before you start:</strong><br>
• Chrome will open as a <strong>visible window</strong> — IRCTC blocks headless browsers<br>
• You will need your <strong>IRCTC username, password, and mobile</strong> for OTP<br>
• If IRCTC shows the new <strong>Alert / preferred language</strong> dialog, the agent selects <strong>English</strong><br>
• Login happens <strong>before</strong> train search — answer each prompt on this page<br>
• When the agent asks a question, a <strong>yellow input box appears below</strong> — type your answer and click Submit<br>
• <strong>Payment is NEVER automated</strong> — you complete it manually in the browser
</div>
""", unsafe_allow_html=True)

# ─── BOOKING FORM ────────────────────────────────────────────────────────────

st.markdown("### 🎫 Booking details")

col1, col2, col3 = st.columns(3)

with col1:
    source = st.text_input("From station", value="CBE", help="Station code e.g. CBE, MAS, SBC")
    source_name = st.text_input("From name", value="Coimbatore Junction")

with col2:
    dest = st.text_input("To station", value="MAS", help="Station code")
    dest_name = st.text_input("To name", value="Chennai Central")

with col3:
    journey_date = st.text_input(
        "Date (DD/MM/YYYY)",
        value=datetime.now().strftime("%d/%m/%Y"),
        help="Used for search and for picking the matching date card on train results.",
    )
    passengers = st.number_input("Passengers", min_value=1, max_value=6, value=2)

col4, col5, col6 = st.columns(3)
with col4:
    train_class = st.selectbox("Class", ["SL", "3A", "2A", "1A", "CC", "EC", "2S"], index=0)
with col5:
    quota_label = st.selectbox(
        "Quota",
        ["General", "Tatkal", "Premium Tatkal", "Ladies", "Lower Berth / Sr. Citizen"],
        index=0,
        help="Selected before Search Trains on IRCTC.",
    )
    quota_map = {
        "General": "GN",
        "Tatkal": "TQ",
        "Premium Tatkal": "PT",
        "Ladies": "LD",
        "Lower Berth / Sr. Citizen": "SS",
    }
    journey_quota = quota_map[quota_label]
with col6:
    preferred_train = st.text_input(
        "Preferred train number (optional)",
        placeholder="e.g. 12676 for Kovai Express",
    )

if journey_quota in ("TQ", "PT"):
    st.warning(
        "Aadhaar authentication of the IRCTC user profile is mandatory to book "
        "Tatkal and Premium Tatkal tickets. The agent opens MY ACCOUNT → Authenticate User "
        "after login; complete Aadhaar/OTP in Chrome when it pauses.",
        icon="⚠️",
    )

# ─── SESSION STATE ───────────────────────────────────────────────────────────

if "irctc_running" not in st.session_state:
    st.session_state.irctc_running = False
if "irctc_log" not in st.session_state:
    st.session_state.irctc_log = []
if "irctc_status" not in st.session_state:
    st.session_state.irctc_status = "idle"
if "irctc_human_q" not in st.session_state:
    st.session_state.irctc_human_q = None
if "irctc_screenshot" not in st.session_state:
    st.session_state.irctc_screenshot = ""
if "irctc_agent" not in st.session_state:
    st.session_state.irctc_agent = None
if "irctc_error" not in st.session_state:
    st.session_state.irctc_error = ""
if "_prev_status" not in st.session_state:
    st.session_state._prev_status = "idle"
if "_human_sent" not in st.session_state:
    st.session_state._human_sent = False


def _submit_human(text: str) -> None:
    if irctc_runner.provide_human_response(text):
        st.session_state._human_sent = True
        st.session_state._human_sent_at = time.time()
        st.session_state.irctc_status = "running"
    else:
        st.error("Could not send — type something and try again.")

# ─── CONTROL BUTTONS ────────────────────────────────────────────────────────

st.divider()
bcol1, bcol2, bcol3 = st.columns([2, 1, 1])

status = st.session_state.irctc_status
status_labels = {
    "idle": "⚪ Ready — fill in details and click Start",
    "running": "🔵 Agent running — Chrome should open on your screen",
    "waiting": "🟡 Waiting for your input — check the yellow box below",
    "done": "🟢 Booking complete",
    "failed": "🔴 Failed — see the log below for details",
}

with bcol1:
    if status == "running":
        st.info(status_labels["running"])
    elif status == "waiting":
        st.warning(status_labels["waiting"])
    elif status == "done":
        st.success(status_labels["done"])
    elif status == "failed":
        st.error(status_labels["failed"])
    else:
        st.caption(status_labels["idle"])

with bcol2:
    start_btn = st.button(
        "🚀 Start booking agent",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.irctc_running,
    )

with bcol3:
    stop_btn = st.button(
        "⏹ Stop agent",
        use_container_width=True,
        disabled=not st.session_state.irctc_running,
        help="Stops the agent but keeps Chrome open so you can finish login manually.",
    )
    if stop_btn:
        irctc_runner.stop(close_browser=False)
        st.session_state.irctc_running = False
        st.session_state.irctc_status = "failed"
        st.warning("Agent stopped. Chrome should still be open — complete login there if needed.")
        st.rerun()

if st.session_state.irctc_running:
    st.caption("Chrome stays open on errors so you can complete CAPTCHA/OTP. Use **Stop agent** only when finished.")

st.divider()
_, clear_col = st.columns([3, 1])
with clear_col:
    clear_btn = st.button("🗑 Clear log", use_container_width=True)
    if clear_btn:
        st.session_state.irctc_log = []
        st.session_state.irctc_status = "idle"
        st.session_state.irctc_human_q = None
        st.session_state.irctc_screenshot = ""
        st.session_state.irctc_error = ""
        st.rerun()

if st.session_state.irctc_error:
    st.error(f"**Error:** {st.session_state.irctc_error}")

# ─── START AGENT ─────────────────────────────────────────────────────────────

if start_btn:
    from agent.irctc_config import IRCTCConfig

    cfg = IRCTCConfig.from_env()
    cfg_errors = cfg.validate()
    if cfg_errors:
        st.error("Fix your `.env` file first:\n\n" + "\n".join(f"• {e}" for e in cfg_errors))
        st.stop()

    st.session_state.irctc_log = ["Starting IRCTC booking agent..."]
    st.session_state.irctc_journey_date = journey_date
    st.session_state.irctc_error = ""
    st.session_state.irctc_human_q = None
    st.session_state._irctc_steps_done = []
    st.session_state._prev_status = "idle"

    irctc_runner.start(
        {
            "source": source,
            "destination": dest,
            "source_name": source_name,
            "dest_name": dest_name,
            "date": journey_date,
            "passengers": passengers,
            "train_class": train_class,
            "journey_quota": journey_quota,
            "preferred_train": preferred_train,
        },
        st.session_state,
    )
    st.rerun()

# ─── MAIN LAYOUT ─────────────────────────────────────────────────────────────

st.markdown("---")
left_col, right_col = st.columns([1.2, 1])

with left_col:
    st.markdown("### 📋 Agent log")
    log_placeholder = st.empty()
    human_placeholder = st.empty()

with right_col:
    st.markdown("### 📸 Browser view")
    screenshot_placeholder = st.empty()
    st.markdown("### 📊 Progress")
    progress_placeholder = st.empty()


def render_log(log_lines: list):
    def classify(line: str) -> str:
        if "Step" in line and "📍" in line:
            return "step"
        if "✅" in line:
            return "ok"
        if "❌" in line:
            return "err"
        if "HUMAN INPUT" in line or "waiting" in line.lower():
            return "wait"
        return "info"

    rows = "".join(
        f'<div class="log-line {classify(l)}">{l}</div>'
        for l in log_lines[-80:]
    )
    log_placeholder.markdown(
        f'<div class="log-box">{rows}</div>',
        unsafe_allow_html=True,
    )


def render_progress(steps_done: list):
    all_steps = [
        ("open_irctc", "🌐 Open IRCTC"),
        ("login", "🔐 Login"),
        ("search", "🔍 Search trains"),
        ("select_train", "🚂 Select train"),
        ("passengers", "👥 Add passengers"),
        ("payment", "💳 Payment"),
    ]
    rows = ""
    for key, label in all_steps:
        done = key in steps_done
        icon = "✅" if done else "⏳"
        color = "#22c55e" if done else "#475569"
        rows += (
            f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;'
            f'border-bottom:1px solid #2d3139;color:{color}">'
            f'<span>{icon}</span><span style="font-size:13px">{label}</span></div>'
        )
    progress_placeholder.markdown(
        f'<div style="background:#1a1d23;border:1px solid #2d3139;border-radius:8px;padding:12px">{rows}</div>',
        unsafe_allow_html=True,
    )


# ─── HUMAN INPUT (on this Streamlit page) ─────────────────────────────────────

agent_alive = irctc_runner.is_running()

if st.session_state.irctc_human_q and not agent_alive and st.session_state.irctc_running:
    st.error(
        "Agent thread stopped but the page still shows a prompt. "
        "Click **Start booking agent** again (Chrome may still be open)."
    )
    st.session_state.irctc_human_q = None
    st.session_state._human_sent = False
    st.session_state.irctc_running = False

if st.session_state.irctc_human_q:
    question = st.session_state.irctc_human_q
    is_login_form = question == "[LOGIN_FORM]" or "[LOGIN_FORM]" in question
    is_login_done = question == "[LOGIN_DONE]" or "[LOGIN_DONE]" in question
    is_search_done = question == "[SEARCH_DONE]" or "[SEARCH_DONE]" in question
    is_aadhaar_done = question == "[AADHAAR_DONE]" or "[AADHAAR_DONE]" in question
    is_date_card = question == "[DATE_CARD]" or "[DATE_CARD]" in question
    is_confirm_done = question in ("[CONFIRM_DONE]", "[SEARCH_DONE]", "[LOGIN_DONE]", "[AADHAAR_DONE]")

    with human_placeholder.container():
        if st.session_state._human_sent:
            st.success("✅ Sent to agent — processing… (click the button again if nothing happens in 5s)")
        elif not agent_alive:
            st.error("Agent is not running. Click **Start booking agent** to continue.")
        else:
            st.warning("🟡 **Agent is waiting for you**")

        if is_confirm_done and agent_alive:
            if is_login_done:
                st.markdown("Finish **CAPTCHA / OTP** in Chrome, then confirm:")
                done_label = "✅ I'm logged in"
                done_caption = "Look for **MY ACCOUNT** or **Logout** in the Chrome header."
            elif is_search_done:
                st.markdown("After you click **Search Trains** in Chrome and see the train list:")
                done_label = "✅ Search done — trains visible"
                done_caption = "Only click when train numbers and times appear on screen."
            elif is_aadhaar_done:
                st.markdown("Complete **Aadhaar authentication** in Chrome, then confirm:")
                done_label = "✅ Aadhaar done — continue"
                done_caption = "If IRCTC says the profile is already authenticated, the agent should continue automatically."
            else:
                st.markdown("When the step is complete in Chrome:")
                done_label = "✅ Done — continue"
                done_caption = ""
            b1, b2 = st.columns(2)
            with b1:
                if st.button(
                    done_label,
                    type="primary",
                    use_container_width=True,
                    key=f"irctc_done_{question}",
                ):
                    _submit_human("DONE")
                    st.rerun()
            with b2:
                if st.button("Cancel", use_container_width=True, key=f"irctc_cancel_{question}"):
                    _submit_human("CANCEL")
                    st.rerun()
            if done_caption:
                st.caption(done_caption)

        elif is_date_card and agent_alive:
            with st.form("irctc_date_card_form", clear_on_submit=True):
                st.markdown("**Train date card** — automatic pick failed.")
                st.caption(
                    "Enter **DD/MM/YYYY** (same as above) or the exact label from Chrome "
                    "(e.g. `Mon, 1 Jun`). Or click the date in Chrome and press **DONE**."
                )
                date_val = st.text_input(
                    "Date",
                    value=st.session_state.get("irctc_journey_date", ""),
                    placeholder="01/06/2026 or Mon, 1 Jun",
                )
                b1, b2 = st.columns(2)
                with b1:
                    submitted = st.form_submit_button(
                        "Submit date ↵", type="primary", use_container_width=True
                    )
                with b2:
                    manual_done = st.form_submit_button(
                        "✅ Clicked in Chrome", use_container_width=True
                    )
                if submitted:
                    if date_val.strip():
                        _submit_human(date_val.strip())
                        st.rerun()
                    else:
                        st.error("Enter a date or use **Clicked in Chrome**.")
                elif manual_done:
                    _submit_human("DONE")
                    st.rerun()

        elif is_login_form and agent_alive:
            with st.form("irctc_login_form", clear_on_submit=True):
                st.markdown("**IRCTC login** — enter both fields, then Submit.")
                login_user = st.text_input("Username", placeholder="IRCTC user id")
                login_pass = st.text_input("Password", type="password", placeholder="IRCTC password")
                if st.form_submit_button("Submit login ↵", type="primary", use_container_width=True):
                    if login_user.strip() and login_pass.strip():
                        _submit_human(f"{login_user.strip()}|{login_pass.strip()}")
                        st.rerun()
                    else:
                        st.error("Enter both username and password.")

        elif agent_alive:
            is_password = "password" in question.lower()
            with st.form(f"irctc_human_{hash(question) % 10_000}", clear_on_submit=True):
                st.markdown(f"**{question}**")
                answer = st.text_input(
                    "Your answer",
                    type="password" if is_password else "default",
                    label_visibility="collapsed",
                    placeholder="Type your answer…",
                )
                if st.form_submit_button("Submit ↵", type="primary", use_container_width=True):
                    if answer.strip():
                        _submit_human(answer.strip())
                        st.rerun()
                    else:
                        st.error("Please enter a value before submitting.")


@st.fragment(run_every=timedelta(seconds=1))
def refresh_live_ui():
    """Poll the background agent and refresh log, screenshot, and progress."""
    if st.session_state.irctc_running or irctc_runner.is_running():
        prev = st.session_state.get("_prev_status", "idle")
        irctc_runner.sync_ui(st.session_state)

        if st.session_state.irctc_status == "running" and st.session_state._human_sent:
            st.session_state._human_sent = False

        if (
            st.session_state._human_sent
            and st.session_state.irctc_status == "waiting"
            and time.time() - st.session_state.get("_human_sent_at", 0) > 4
        ):
            st.session_state._human_sent = False

        if (
            st.session_state.irctc_status == "waiting"
            and prev != "waiting"
            and not st.session_state._human_sent
        ):
            st.session_state._prev_status = "waiting"
            st.rerun(scope="app")

        st.session_state._prev_status = st.session_state.irctc_status

        if st.session_state.get("_irctc_steps_done") is not None:
            agent = irctc_runner.get_active_agent()
            if agent:
                st.session_state._irctc_steps_done = list(agent.session.steps_done)

        if st.session_state.irctc_running and not irctc_runner.is_running():
            st.session_state.irctc_running = False
            if st.session_state._human_sent or st.session_state.irctc_status == "waiting":
                st.session_state.irctc_error = (
                    "Agent stopped unexpectedly. If Chrome is open, finish login there, "
                    "then click **Start booking agent** again."
                )

    if st.session_state.irctc_log:
        render_log(st.session_state.irctc_log)

    steps_done = st.session_state.get("_irctc_steps_done", [])
    if steps_done or st.session_state.irctc_running:
        render_progress(steps_done)

    if st.session_state.irctc_screenshot:
        with screenshot_placeholder.container():
            st.image(
                base64.b64decode(st.session_state.irctc_screenshot),
                use_container_width=True,
                caption="Live browser view",
            )

refresh_live_ui()

# ─── TIPS ────────────────────────────────────────────────────────────────────

with st.expander("💡 Tips for best results"):
    st.markdown("""
**Before starting:**
- Make sure Google Chrome is installed (not just Chromium)
- Have your IRCTC username and password ready
- Keep your mobile phone nearby for OTP
- Don't move your mouse while the agent is working

**Night trains from Coimbatore to Chennai (28 Jul):**
| Train | Name | Departs | Arrives |
|-------|------|---------|---------|
| 12676 | Kovai Express | 18:30 | 23:45 |
| 12674 | Cheran Express | 21:15 | 05:30 |
| 22625 | Chennai Mail | 23:00 | 06:30 |

**If the agent fails:**
- IRCTC sometimes rejects headless-looking browsers — try again
- If CAPTCHA fails, click 'Refresh CAPTCHA' manually in browser
- `CNF route` / `All route info` means IRCTC found confirmed split or connecting journeys; direct train booking still uses the selected train row
- The agent auto-recovers from most errors via vision re-observation
""")
