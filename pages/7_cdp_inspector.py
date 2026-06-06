"""
pages/1_cdp_inspector.py
------------------------
CDP Inspector — demonstrates raw Chrome DevTools Protocol capabilities.
Changes: added error handling banner, better layout, Windows chrome path fix.
"""

import asyncio, base64, json, os
import streamlit as st
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=True)

from ui.streamlit_nav import inject_travel_css, render_sidebar

st.set_page_config(page_title="CDP Inspector", page_icon="🔌", layout="wide")
inject_travel_css()
render_sidebar(show_engine=False)

st.markdown("""
<style>
.cdp-code {
    background:#0d1117; border:1px solid #2d3139; border-radius:8px;
    padding:12px; font-family:monospace; font-size:12px; color:#e2e8f0;
    white-space:pre-wrap; max-height:300px; overflow-y:auto;
}
.net-req  { color:#60a5fa; }
.net-resp-ok  { color:#22c55e; }
.net-resp-err { color:#ef4444; }
</style>
""", unsafe_allow_html=True)

st.markdown("# 🔌 CDP Inspector")
st.markdown(
    "Directly control Chrome via the DevTools Protocol — "
    "no Playwright, no abstraction. Demonstrates capabilities unavailable through any library."
)

if not os.getenv("ANTHROPIC_API_KEY"):
    st.error("Set ANTHROPIC_API_KEY in .env or via the sidebar on the Home page.")
    st.stop()

tab1, tab2, tab3 = st.tabs(["🖱️ Vision Click", "🌐 Network Inspector", "⚡ JS Console"])

# ── TAB 1 ────────────────────────────────────────────────────────────────────
with tab1:
    st.markdown("### Vision-based pixel clicking")
    st.markdown(
        "Describe **what** to click in plain English. "
        "Claude Vision identifies exact pixel coordinates. CDP dispatches a real mouse event. "
        "Zero CSS selectors — completely immune to DOM restructuring."
    )

    col1, col2 = st.columns(2)
    with col1:
        vc_url = st.text_input("URL", value="https://example.com", key="vc_url")
    with col2:
        vc_instruction = st.text_input(
            "What to click", value="the 'More information...' link", key="vc_instr"
        )

    if st.button("🚀 Vision Click", type="primary", key="vc_run"):
        async def do_vc():
            from agent.cdp_engine import CDPEngine
            engine = CDPEngine(headless=True)
            try:
                with st.spinner("Launching Chrome..."):
                    await engine.launch()
                with st.spinner(f"Navigating..."):
                    await engine.navigate(vc_url)
                b64 = await engine.get_screenshot_b64()
                st.image(base64.b64decode(b64), caption="Before click", use_container_width=True)
                with st.spinner("Asking Claude Vision where to click..."):
                    result = await engine.vision_click(vc_instruction)
                if result["success"]:
                    st.success(f"✅ Clicked at {result.get('clicked_at')}")
                    await asyncio.sleep(1.2)
                    b64_after = await engine.get_screenshot_b64()
                    st.image(base64.b64decode(b64_after), caption="After click", use_container_width=True)
                else:
                    st.error(f"❌ {result.get('error')}")
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                await engine.close()
        asyncio.run(do_vc())

# ── TAB 2 ────────────────────────────────────────────────────────────────────
with tab2:
    st.markdown("### Network request monitor")
    st.markdown(
        "CDP's `Network` domain exposes every request and response before Playwright even sees them. "
        "You can block, modify, or log anything."
    )

    net_url = st.text_input("URL to monitor", value="https://httpbin.org/get", key="net_url")

    if st.button("🔍 Capture network", type="primary", key="net_run"):
        async def do_net():
            from agent.cdp_engine import CDPEngine
            engine = CDPEngine(headless=True)
            try:
                await engine.launch()
                await engine._cmd("Network.enable")
                with st.spinner("Loading page..."):
                    await engine.navigate(net_url)
                    await asyncio.sleep(2)

                events = [
                    e for e in engine._events
                    if e["method"] in ("Network.requestWillBeSent", "Network.responseReceived")
                ]
                st.markdown(f"**{len(events)} network events captured:**")

                rows = ""
                for ev in events[:25]:
                    m = ev["method"]
                    p = ev.get("params", {})
                    if m == "Network.requestWillBeSent":
                        req = p.get("request", {})
                        rows += f'<div class="net-req">▶ {req.get("method","?")}  {req.get("url","")[:90]}</div>'
                    else:
                        resp = p.get("response", {})
                        s    = resp.get("status", "?")
                        cls  = "net-resp-ok" if str(s).startswith("2") else "net-resp-err"
                        rows += f'<div class="{cls}">◀ {s}  {resp.get("url","")[:80]}</div>'

                st.markdown(f'<div class="cdp-code">{rows}</div>', unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                await engine.close()
        asyncio.run(do_net())

# ── TAB 3 ────────────────────────────────────────────────────────────────────
with tab3:
    st.markdown("### Runtime JS evaluation")
    st.markdown("Execute arbitrary JavaScript in the live page context via `Runtime.evaluate`.")

    js_url  = st.text_input("URL", value="https://example.com", key="js_url")
    js_code = st.text_area(
        "JavaScript",
        value="// Extract all text\ndocument.body.innerText.trim().slice(0, 600)",
        height=100, key="js_code",
    )

    if st.button("▶ Run JS", type="primary", key="js_run"):
        async def do_js():
            from agent.cdp_engine import CDPEngine
            engine = CDPEngine(headless=True)
            try:
                await engine.launch()
                with st.spinner("Loading page..."):
                    await engine.navigate(js_url)
                result = await engine.evaluate(js_code)
                st.markdown("**Result:**")
                if isinstance(result, (dict, list)):
                    st.json(result)
                else:
                    st.code(str(result))
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                await engine.close()
        asyncio.run(do_js())