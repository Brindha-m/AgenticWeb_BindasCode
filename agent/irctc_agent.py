"""
agent/irctc_agent.py
--------------------
IRCTC-specific web agent.

Why IRCTC needs special handling:
  1. Detects headless Chrome via navigator.webdriver, canvas fingerprint, etc.
  2. Login requires username + password + CAPTCHA + OTP — all need human handoff
  3. React SPA — DOM indices shift every render, so we use VISION CLICKING
  4. Seat availability race condition — seats vanish between search and book
  5. Payment page needs explicit user confirmation before proceeding

Strategy:
  - Run Chrome NON-headless (visible window) — IRCTC blocks headless hard
  - Use vision clicking (LLM reads screenshot → clicks pixel coords)
  - Pause at every sensitive step for human input
  - Never auto-click payment
"""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Timing — keep steps responsive without breaking IRCTC's React UI
_CLICK_PAUSE = 0.25
_SHORT_PAUSE = 0.15
_TYPE_DELAY = 0.02

import aiohttp
import anthropic
import websockets

from agent.planner import CLAUDE_MODEL

# ─── IRCTC STEP DEFINITIONS ─────────────────────────────────────────────────

IRCTC_STEPS = [
    "open_irctc",
    "login",
    "enter_source",
    "enter_destination",
    "enter_date",
    "search_trains",
    "select_train",
    "select_class",
    "add_passengers",
    "review_booking",
    "payment_handoff",
    "done",
]


@dataclass
class IRCTCSession:
    source: str = "CBE"           # Coimbatore Junction
    destination: str = "MAS"     # Chennai Central
    source_name: str = "Coimbatore Junction"
    dest_name: str = "Chennai Central"
    date: str = field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y"))
    passengers: int = 2
    train_class: str = "SL"       # Sleeper
    preferred_train: str = ""     # e.g. "12676" for Kovai Express
    steps_done: list = field(default_factory=list)
    log: list = field(default_factory=list)
    current_screenshot: str = ""
    status: str = "idle"          # idle | running | waiting | done | failed


class IRCTCAgent:
    """
    Vision-first IRCTC agent.
    Uses Claude Vision to understand the page and click by coordinates.
    All sensitive steps pause for human confirmation.
    """

    def __init__(self, session: IRCTCSession, port: int = 9223):
        self.session = session
        self.port = port
        self.ws = None
        self._cmd_id = 0
        self._pending = {}
        self._events = []
        self._chrome_proc = None
        self._listen_task = None
        self._profile_dir = None
        self._human_response: Optional[str] = None
        self._anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._force_close_browser = False

    # ─── CDP CORE ────────────────────────────────────────────────────────────

    async def launch(self):
        """Launch Chrome NON-headless — IRCTC blocks headless browsers"""
        chrome = self._find_chrome()
        self._profile_dir = tempfile.mkdtemp(prefix="irctc_chrome_")

        cmd = [
            chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-gpu",
            "--window-size=1400,900",
            "--start-maximized",
            # Anti-detection flags
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--flag-switches-begin",
            "--disable-site-isolation-trials",
            "--flag-switches-end",
            # Real user profile feel
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]
        # IMPORTANT: no --headless flag for IRCTC

        self._chrome_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._log("Chrome launched (visible window — required for IRCTC)")

        ws_url = await self._wait_for_chrome()
        self.ws = await websockets.connect(ws_url, max_size=100_000_000, ping_interval=None)
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Enable domains
        await self._cmd("Page.enable")
        await self._cmd("Runtime.enable")
        await self._cmd("Network.enable")
        await self._cmd("DOM.enable")

        # Deep anti-detection patches
        await self._cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                // Remove webdriver flag
                delete Object.getPrototypeOf(navigator).webdriver;
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

                // Fake plugins (real Chrome has plugins)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {name:'Chrome PDF Plugin', filename:'internal-pdf-viewer'},
                        {name:'Chrome PDF Viewer', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                        {name:'Native Client', filename:'internal-nacl-plugin'},
                    ]
                });

                // Fake languages
                Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en', 'hi']});

                // Fake chrome runtime
                window.chrome = {
                    runtime: {id: undefined},
                    loadTimes: function(){},
                    csi: function(){},
                };

                // Fix permissions API
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery(params);

                // Fake canvas fingerprint (prevents bot detection)
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type) {
                    if (type === 'image/png' && this.width === 16 && this.height === 16) {
                        return origToDataURL.apply(this, arguments);
                    }
                    const ctx = this.getContext('2d');
                    if (ctx) {
                        const imgData = ctx.getImageData(0, 0, this.width, this.height);
                        for (let i = 0; i < imgData.data.length; i += 100) {
                            imgData.data[i] = imgData.data[i] ^ 1;
                        }
                        ctx.putImageData(imgData, 0, 0);
                    }
                    return origToDataURL.apply(this, arguments);
                };
            """
        })

        self._log("Anti-detection patches applied")
        return self

    async def disconnect(self):
        """Close CDP connection only — leaves Chrome running for manual CAPTCHA/OTP."""
        if self._listen_task:
            self._listen_task.cancel()
            self._listen_task = None
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def close(self, kill_browser: bool = True):
        """Shut down CDP and optionally terminate Chrome."""
        await self.disconnect()
        if kill_browser and self._chrome_proc:
            self._chrome_proc.terminate()
            self._chrome_proc = None
        if kill_browser and self._profile_dir and os.path.isdir(self._profile_dir):
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None

    async def _listen_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(msg["error"].get("message", "CDP error")))
                        else:
                            fut.set_result(msg.get("result", {}))
                elif "method" in msg:
                    self._events.append(msg)
                    if len(self._events) > 200:
                        self._events.pop(0)
        except Exception:
            pass

    async def _cmd(self, method: str, params: dict = None, timeout: float = 20) -> dict:
        self._cmd_id += 1
        cid = self._cmd_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[cid] = fut
        await self.ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=timeout)

    # ─── VISION ENGINE ───────────────────────────────────────────────────────

    async def screenshot(self) -> str:
        """Take screenshot, store in session, return b64"""
        result = await self._cmd("Page.captureScreenshot", {"format": "jpeg", "quality": 70})
        b64 = result["data"]
        self.session.current_screenshot = b64
        return b64

    async def vision_ask(self, question: str) -> str:
        """Ask Claude Vision a question about the current page"""
        b64 = await self.screenshot()
        response = self._anthropic.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": question},
                ],
            }],
        )
        return response.content[0].text.strip()

    async def vision_click(self, instruction: str) -> dict:
        """
        Core vision click: describe what to click → Claude returns (x,y) → CDP clicks it.
        This beats any DOM-based approach for IRCTC's React SPA.
        """
        b64 = await self.screenshot()
        self._log(f"Vision click: {instruction}")

        response = self._anthropic.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": (
                        f"This is a screenshot of the IRCTC website (1400x900px).\n"
                        f"Return ONLY a JSON object: {{\"x\": N, \"y\": N}}\n"
                        f"with the pixel coordinates of where to click to: {instruction}\n"
                        f"If the element is not visible, return {{\"x\": -1, \"y\": -1}}"
                    )},
                ],
            }],
        )

        raw = response.content[0].text.strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()

        try:
            coords = json.loads(raw)
        except Exception:
            return {"success": False, "error": f"Vision could not parse coords: {raw}"}

        x, y = int(coords.get("x", -1)), int(coords.get("y", -1))

        if x < 0 or y < 0:
            return {"success": False, "error": f"Element not visible: {instruction}"}

        # Human-like mouse movement → press → release
        await self._cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await asyncio.sleep(0.1)
        await self._cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1,
        })
        await asyncio.sleep(0.12)
        await self._cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 0,
        })

        await asyncio.sleep(_CLICK_PAUSE)
        self._log(f"  → clicked at ({x}, {y})")
        return {"success": True, "x": x, "y": y}

    async def vision_type(self, field_instruction: str, text: str) -> dict:
        """Click a field by vision, then type into it (fallback when DOM fill fails)."""
        result = await self.vision_click(field_instruction)
        if not result["success"]:
            return result

        await asyncio.sleep(_SHORT_PAUSE)
        await self._clear_focused_field()
        await self._type_keys(text, delay=_TYPE_DELAY)
        self._log(f"  → typed into: {field_instruction}")
        return {"success": True}

    async def navigate(self, url: str):
        await self._cmd("Page.navigate", {"url": url})
        await self._wait_for_load()

    async def _wait_for_load(self, timeout: float = 12):
        """Wait for page load event"""
        self._events.clear()
        for _ in range(int(timeout / 0.2)):
            await asyncio.sleep(0.2)
            if any(e["method"] == "Page.loadEventFired" for e in self._events[-30:]):
                await asyncio.sleep(0.4)
                return
        await asyncio.sleep(0.8)

    async def wait_for_element_visible(self, description: str, max_wait: float = 15) -> bool:
        """Poll Claude Vision until it sees the described element"""
        self._log(f"Waiting for: {description}")
        for _ in range(int(max_wait / 2)):
            await asyncio.sleep(2)
            answer = await self.vision_ask(
                f"Can you see '{description}' on this page? Reply only YES or NO."
            )
            if "YES" in answer.upper():
                self._log(f"  → found: {description}")
                return True
        self._log(f"  → timeout waiting for: {description}")
        return False

    # ─── HUMAN HANDOFF ───────────────────────────────────────────────────────

    def provide_human_response(self, response: str):
        from agent import irctc_runner
        irctc_runner.provide_human_response(response)

    def _poll_human_response(self) -> Optional[str]:
        """Read response from the cross-thread queue."""
        from agent import irctc_runner
        return irctc_runner.take_human_response()

    @staticmethod
    def _normalize_reply(text: str) -> str:
        t = text.strip()
        if t.upper() in ("DONE", "OK", "YES", "Y", "LOGGED IN", "LOGGEDIN", "COMPLETE"):
            return "DONE"
        if t.upper() in ("CANCEL", "STOP", "ABORT", "QUIT"):
            return "CANCEL"
        return t

    async def ask_human(self, question: str, timeout: float = 300, tag: str = "") -> str:
        """Pause execution and wait for human input."""
        prompt_tag = tag or question[:60]
        if tag:
            self._log(f"HUMAN INPUT NEEDED: [{tag}] {question}")
        else:
            self._log(f"HUMAN INPUT NEEDED: {question}")
        self.session.status = "waiting"
        self._human_response = None

        for _ in range(int(timeout / 0.15)):
            await asyncio.sleep(0.15)
            raw = self._poll_human_response()
            if raw is not None:
                response = self._normalize_reply(raw)
                self.session.status = "running"
                preview = "****" if "password" in question.lower() else response[:40]
                self._log(f"  → human responded: {preview}")
                return response

        self.session.status = "waiting"
        self._log("  ⚠ Still waiting for your answer on this page — click Submit again")
        return ""

    async def ask_human_validated(
        self,
        question: str,
        validator,
        hint: str = "Please try again.",
        timeout: float = 120,
    ) -> str:
        """Ask human until input passes validation."""
        while True:
            answer = await self.ask_human(question)
            if not answer.strip():
                self._log(f"  ⚠ {hint}")
                question = f"{question}\n\n(Previous answer was empty. {hint})"
                continue
            if validator(answer.strip()):
                return answer.strip()
            self._log(f"  ⚠ Invalid input: {hint}")
            question = f"{question}\n\n(Invalid answer '{answer[:30]}'. {hint})"

    def _fail_step(self, msg: str) -> bool:
        self._log(f"❌ {msg}")
        self.session.status = "failed"
        return False

    async def _vision_yes(self, question: str) -> bool:
        answer = await self.vision_ask(f"{question} Reply only YES or NO.")
        return "YES" in answer.upper()

    async def _type_keys(self, text: str, delay: float = _TYPE_DELAY) -> None:
        for char in text:
            await self._cmd("Input.dispatchKeyEvent", {"type": "keyDown", "text": char, "key": char})
            await self._cmd("Input.dispatchKeyEvent", {"type": "keyUp", "text": char, "key": char})
            await asyncio.sleep(delay)

    async def _evaluate(self, js: str):
        result = await self._cmd("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return result.get("result", {}).get("value")

    async def _set_react_input(self, selectors: list[str], value: str, scope: str = "document") -> bool:
        """Fill a React-controlled input; scope limits search to login modal when set."""
        selectors_json = json.dumps(selectors)
        value_json = json.dumps(value)
        ok = await self._evaluate(f"""
        (() => {{
            const selectors = {selectors_json};
            const value = {value_json};
            const visible = (el) => {{
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden';
            }};
            const roots = [];
            if ("{scope}" === "login") {{
                for (const r of document.querySelectorAll(
                    'p-dialog, .ui-dialog, [role="dialog"], .modal, app-login, .login-modal'
                )) {{
                    if (visible(r)) roots.push(r);
                }}
            }}
            if (!roots.length) roots.push(document);
            for (const root of roots) {{
                for (const sel of selectors) {{
                    const el = root.querySelector(sel);
                    if (!visible(el)) continue;
                    el.focus();
                    el.click();
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    )?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                    if (el.value === value) return true;
                }}
            }}
            return false;
        }})()
        """)
        return bool(ok)

    async def _click_dom(self, js_find_click: str) -> bool:
        return bool(await self._evaluate(js_find_click))

    async def _is_login_modal_visible(self) -> bool:
        return bool(await self._evaluate("""
        (() => {
            const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const userSel = 'input[formcontrolname="userid"], #usernameId, input[name="userid"], input[placeholder*="User" i]';
            for (const root of document.querySelectorAll('p-dialog, .ui-dialog, [role="dialog"], .modal, app-login')) {
                if (!visible(root)) continue;
                if (visible(root.querySelector(userSel))) return true;
            }
            return visible(document.querySelector(userSel));
        })()
        """))

    async def _ensure_login_modal(self) -> bool:
        """Open login popup and wait until username field is visible."""
        if await self._is_login_modal_visible():
            return True

        for attempt in range(1, 5):
            self._log(f"  Opening login popup (attempt {attempt}/4)...")
            opened = await self._open_login_modal()
            if not opened:
                await self.vision_click("the LOGIN link or button in the top header bar")

            for _ in range(12):
                await asyncio.sleep(0.4)
                if await self._is_login_modal_visible():
                    self._log("  → login popup is open")
                    return True

        return False

    async def _open_login_modal(self) -> bool:
        return await self._click_dom("""
        (() => {
            const fire = (el) => {
                if (!el) return false;
                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                el.click();
                return true;
            };
            for (const sel of ['a.loginText', 'a.login', 'a[aria-label="Login"]', '.login a']) {
                const el = document.querySelector(sel);
                if (el && fire(el)) return true;
            }
            const match = (el) => /^\\s*login\\s*$/i.test((el.innerText || el.textContent || '').trim());
            const cands = [...document.querySelectorAll('a, button, span, label')].filter(
                (el) => el.offsetParent && match(el)
            );
            const header = cands.find((el) => el.closest(
                'header, nav, .header, .navbar, .topbar, app-header, .h_menu_drop_button'
            ));
            return fire(header || cands[0]);
        })()
        """)

    async def _fill_login_credentials(self, username: str, password: str) -> bool:
        user_ok = await self._set_react_input(
            [
                'input[formcontrolname="userid"]',
                '#usernameId',
                'input[name="userid"]',
                'input[placeholder*="User" i]',
                'input[placeholder*="user" i]',
                '#userId',
            ],
            username,
            scope="login",
        )
        pass_ok = await self._set_react_input(
            [
                'input[formcontrolname="password"]',
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="Password" i]',
            ],
            password,
            scope="login",
        )
        if user_ok:
            self._log("  → username filled (DOM)")
        if pass_ok:
            self._log("  → password filled (DOM)")
        return user_ok and pass_ok

    async def _click_sign_in(self) -> bool:
        return await self._click_dom("""
        (() => {
            const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2;
            };
            const roots = [...document.querySelectorAll('p-dialog, .ui-dialog, [role="dialog"], .modal')];
            if (!roots.length) roots.push(document);
            for (const root of roots) {
                for (const el of root.querySelectorAll('button, a, span, input[type="submit"]')) {
                    if (!visible(el)) continue;
                    const t = (el.innerText || el.value || '').trim();
                    if (/^sign\\s*in$/i.test(t) || /^login$/i.test(t) || /submit/i.test(t)) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """)

    async def _is_logged_in(self) -> bool:
        return bool(await self._evaluate("""
        (() => {
            for (const el of document.querySelectorAll('a, span, button, label')) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || '').trim();
                if (/^logout$/i.test(t) || /^my\\s*account$/i.test(t)) return true;
            }
            const loginVisible = [...document.querySelectorAll('a, button, span')].some((el) => {
                if (!el.offsetParent) return false;
                return /^login$/i.test((el.innerText || '').trim());
            });
            const bookingForm = document.querySelector(
                'input[formcontrolname="origin"], input[placeholder*="From" i]'
            );
            return !loginVisible && !!bookingForm;
        })()
        """))

    async def _is_logged_in_safe(self) -> bool:
        try:
            if not self.ws:
                return False
            return await self._is_logged_in()
        except Exception:
            return False

    async def _wait_for_login_complete(self) -> bool:
        """Let user finish CAPTCHA/OTP in Chrome; single button click is enough."""
        self._log("ℹ️ Chrome stays open — complete CAPTCHA/OTP, then click **I'm logged in** once.")
        reply = await self.ask_human(
            "Click **I'm logged in** once when MY ACCOUNT or Logout shows in Chrome.",
            tag="LOGIN_DONE",
        )
        if reply.strip().upper() == "CANCEL":
            self._log("  Login cancelled by user")
            return False
        if reply.strip().upper() == "DONE":
            if await self._is_logged_in_safe():
                self._log("✅ Login successful")
            else:
                self._log("✅ Login confirmed — continuing")
            return True
        return False

    async def ask_human_login(self) -> tuple[str, str]:
        """Collect username + password in one form (avoids login modal timing out)."""
        self._log("HUMAN INPUT NEEDED: [LOGIN_FORM] Enter IRCTC username and password below")
        self.session.status = "waiting"
        self._human_response = None

        for _ in range(int(180 / 0.15)):
            await asyncio.sleep(0.15)
            raw = self._poll_human_response()
            if raw is None:
                continue
            self.session.status = "running"
            if "|" in raw:
                user, pwd = raw.split("|", 1)
                user, pwd = user.strip(), pwd.strip()
                if user and pwd:
                    self._log(f"  → credentials received for user: {user[:20]}")
                    return user, pwd
            self._log("  ⚠ Invalid login form — need username and password")
            self.session.status = "waiting"
            self._human_response = None

        self.session.status = "running"
        return "", ""

    async def _fill_journey_date(self, date_str: str) -> bool:
        return await self._set_react_input(
            [
                'input[formcontrolname="journeyDate"]',
                'input[placeholder*="Date" i]',
                'input[placeholder*="DD/MM" i]',
                '.ui-calendar input',
            ],
            date_str,
        )

    async def _press_key(self, key: str) -> None:
        await self._cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        await self._cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})

    async def _clear_focused_field(self) -> None:
        await self._cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Control", "modifiers": 2})
        await self._cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2, "text": "a"})
        await self._cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a"})
        await self._cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Control"})
        await self._press_key("Backspace")

    _STATION_INPUT_JS = """
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        // offsetParent is more reliable on IRCTC than display/visibility alone
        const inFlow = el.offsetParent !== null || s.position === 'fixed' || s.position === 'sticky';
        return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden' && inFlow;
    };
    function stationInput(control) {
        const names = control === 'origin'
            ? ['origin', 'fromStation', 'source', 'from', 'originStation', 'fromStn']
            : ['destination', 'toStation', 'dest', 'to', 'destinationStation', 'toStn'];
        for (const n of names) {
            let el = document.querySelector(`input[formcontrolname="${n}"]`);
            if (visible(el)) return el;
            const ac = document.querySelector(`p-autocomplete[formcontrolname="${n}"]`);
            if (ac) {
                el = ac.querySelector('input.p-autocomplete-input, input.p-inputtext, input.ui-inputtext, input[type="text"], input');
                if (visible(el)) return el;
            }
        }
        // Common IRCTC/PrimeNG autocomplete inputs
        const autos = [...document.querySelectorAll('p-autocomplete input, .ui-autocomplete-input, .p-autocomplete-input')]
            .filter(visible);
        if (control === 'origin' && autos[0]) return autos[0];
        if (control === 'destination' && autos[1]) return autos[1];
        // Placeholder fallbacks
        if (control === 'origin') {
            const from = document.querySelector('input[placeholder*="From" i], input[aria-label*="From" i]');
            if (visible(from)) return from;
        } else {
            const to = document.querySelector('input[placeholder*="To" i], input[aria-label*="To" i]');
            if (visible(to)) return to;
        }
        const texts = [...document.querySelectorAll(
            'app-search-train input[type="text"], .search_train input[type="text"], form input[type="text"]'
        )].filter(visible);
        if (control === 'origin' && texts[0]) return texts[0];
        if (control === 'destination' && texts[1]) return texts[1];
        return null;
    }
    """

    async def _insert_text(self, text: str) -> None:
        """CDP insertText works reliably with Angular/PrimeNG inputs."""
        try:
            await self._cmd("Input.insertText", {"text": text})
        except Exception:
            await self._type_keys(text)

    async def _wait_for_search_form(self, timeout: float = 20) -> bool:
        for _ in range(int(timeout / 0.5)):
            ready = await self._evaluate(
                self._STATION_INPUT_JS
                + """
            (() => !!stationInput('origin'))()
            """
            )
            if ready:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _dismiss_blocking_overlays(self) -> None:
        await self._click_dom("""
        (() => {
            for (const el of document.querySelectorAll('button, a, span, i')) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
                if (/^(close|×|✕|ok|accept|dismiss)$/i.test(t) && t.length < 20) {
                    el.click();
                    return true;
                }
            }
            return false;
        })()
        """)

    async def _log_search_form_debug(self) -> None:
        info = await self._evaluate(
            self._STATION_INPUT_JS
            + """
        (() => {
            const rows = [];
            for (const el of document.querySelectorAll('input')) {
                if (!el.offsetParent) continue;
                rows.push({
                    fc: el.getAttribute('formcontrolname') || '',
                    ph: (el.placeholder || '').slice(0, 20),
                    val: (el.value || '').slice(0, 25),
                });
                if (rows.length >= 8) break;
            }
            return JSON.stringify(rows);
        })()
        """
        )
        if info:
            self._log(f"  Visible inputs: {info}")

    async def _focus_input(self, selectors: list[str]) -> bool:
        selectors_json = json.dumps(selectors)
        return bool(await self._evaluate(f"""
        (() => {{
            {self._STATION_INPUT_JS}
            const selectors = {selectors_json};
            const visible = (el) => {{
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden';
            }};
            for (const sel of selectors) {{
                const el = document.querySelector(sel);
                if (!visible(el)) continue;
                el.scrollIntoView({{ block: 'center' }});
                el.focus();
                el.click();
                return true;
            }}
            return false;
        }})()
        """))

    async def _focus_station(self, control: str) -> bool:
        return bool(
            await self._evaluate(
                self._STATION_INPUT_JS
                + f"""
            (() => {{
                const el = stationInput('{control}');
                if (!el) return false;
                el.scrollIntoView({{ block: 'center' }});
                el.focus();
                el.click();
                return true;
            }})()
            """
            )
        )

    def _station_keywords(self, name: str, code: str) -> list[str]:
        words = [
            w for w in name.upper().replace("(", " ").replace(")", " ").replace("-", " ").split()
            if len(w) > 2
        ]
        return [code.upper(), *words[:4]]

    async def _station_verified(self, control: str, name: str, code: str) -> bool:
        keywords = self._station_keywords(name, code)
        keywords_json = json.dumps(keywords)
        return bool(
            await self._evaluate(
                self._STATION_INPUT_JS
                + f"""
            (() => {{
                const keywords = {keywords_json};
                const el = stationInput('{control}');
                if (!el) return false;
                const val = (el.value || '').toUpperCase();
                if (val.length < 3) return false;
                if (keywords.some((k) => val.includes(k))) return true;
                return val.length >= 6;
            }})()
            """
            )
        )

    async def _pick_autocomplete(self, code: str, name: str) -> bool:
        code_json = json.dumps(code.upper())
        keywords_json = json.dumps(self._station_keywords(name, code))
        return bool(
            await self._evaluate(
                f"""
        (() => {{
            const code = {code_json};
            const keywords = {keywords_json};
            const items = document.querySelectorAll(
                'p-autocomplete-panel li, p-autocomplete-items li, .ui-autocomplete-list-item, '
                + '.ui-autocomplete-item, [role="listbox"] [role="option"], '
                + '.cdk-overlay-pane li, ul.ui-autocomplete-items li, .ng-star-inserted li'
            );
            for (const li of items) {{
                const r = li.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) continue;
                const t = (li.innerText || '').trim().toUpperCase();
                if (!t) continue;
                if (t.includes(code) || keywords.some((k) => t.includes(k))) {{
                    li.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true }}));
                    li.dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
                    li.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
            )
        )

    async def _clear_station_input(self, control: str) -> None:
        await self._evaluate(
            self._STATION_INPUT_JS
            + f"""
        (() => {{
            const el = stationInput('{control}');
            if (!el) return false;
            el.focus();
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            )?.set;
            if (setter) setter.call(el, '');
            else el.value = '';
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """
        )

    async def _fill_station_dom(self, control: str, name: str, code: str) -> bool:
        """Fill From/To using p-autocomplete + CDP insertText."""
        label = "From" if control == "origin" else "To"

        if not await self._focus_station(control):
            self._log(f"  ⚠ Could not find {label} station field")
            await self._log_search_form_debug()
            # Vision fallback: click the correct field in the Book Ticket form and type code.
            field_hint = (
                "the 'From' station input field in the Book Ticket form (not the date field)"
                if control == "origin"
                else "the 'To' station input field in the Book Ticket form (not the date field)"
            )
            clicked = await self.vision_click(field_hint)
            if not clicked.get("success"):
                return False
            await asyncio.sleep(_SHORT_PAUSE)
            await self._clear_focused_field()
            await self._insert_text(code.upper())
            await asyncio.sleep(1.6)
            await self._press_key("ArrowDown")
            await asyncio.sleep(0.25)
            await self._press_key("Enter")
            await asyncio.sleep(0.6)
            # Best-effort vision verification (DOM selectors may still be unavailable)
            if await self._vision_yes(f"Does the {label} field now show a selected station for {code} or {name}?"):
                self._log(f"  → {label} set ({code}) [vision]")
                return True
            return False

        await asyncio.sleep(_SHORT_PAUSE)
        await self._clear_station_input(control)
        await asyncio.sleep(0.1)

        for text in (code.upper(), name[:22].upper()):
            if not await self._focus_station(control):
                break
            await self._insert_text(text)
            await asyncio.sleep(1.6)
            if await self._pick_autocomplete(code, name):
                await asyncio.sleep(0.5)
                if await self._station_verified(control, name, code):
                    self._log(f"  → {label} set ({code})")
                    return True
            await self._press_key("ArrowDown")
            await asyncio.sleep(0.25)
            await self._press_key("Enter")
            await asyncio.sleep(0.5)
            if await self._station_verified(control, name, code):
                self._log(f"  → {label} set ({code})")
                return True

        self._log(f"  ⚠ {label} station not verified after typing {code}")
        return False

    async def _select_class_dom(self, class_code: str, class_label: str) -> bool:
        code_json = json.dumps(class_code)
        label_json = json.dumps(class_label)
        opened = await self._click_dom(f"""
        (() => {{
            const triggers = document.querySelectorAll(
                'p-dropdown[formcontrolname*="class" i] .p-dropdown-trigger, '
                + 'p-dropdown[formcontrolname*="Class" i] .p-dropdown-trigger, '
                + '.ui-dropdown-trigger, p-dropdown .p-dropdown-label'
            );
            for (const t of triggers) {{
                const r = t.getBoundingClientRect();
                if (r.width > 2) {{ t.click(); return true; }}
            }}
            const labels = [...document.querySelectorAll('label, span')].filter(
                (el) => /class/i.test((el.innerText || '').trim()) && el.offsetParent
            );
            if (labels[0]) {{ labels[0].click(); return true; }}
            return false;
        }})()
        """)
        if not opened:
            return False
        await asyncio.sleep(0.5)
        return await self._click_dom(f"""
        (() => {{
            const code = {code_json};
            const label = {label_json};
            const items = document.querySelectorAll(
                'p-dropdown-panel li, .ui-dropdown-item, [role="option"], .cdk-overlay-pane li'
            );
            for (const li of items) {{
                const t = (li.innerText || '').trim();
                if (t.includes(code) || t.includes(label) || t.toUpperCase().includes(code)) {{
                    li.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """)

    async def _click_search_trains_dom(self) -> bool:
        return await self._click_dom("""
        (() => {
            for (const el of document.querySelectorAll('button, a, span, label')) {
                const r = el.getBoundingClientRect();
                if (r.width < 2) continue;
                const t = (el.innerText || '').trim();
                if (/search\\s*trains?/i.test(t) || t === 'Search') {
                    el.click();
                    return true;
                }
            }
            const btn = document.querySelector('button.search_btn, button[type="submit"]');
            if (btn) { btn.click(); return true; }
            return false;
        })()
        """)

    async def _has_train_results_dom(self) -> bool:
        return bool(await self._evaluate("""
        (() => {
            const rows = document.querySelectorAll(
                '.train-heading, .train-list-item, app-train-avl-enq, '
                + '[class*="train"][class*="list"], .ng-star-inserted h3'
            );
            if (rows.length >= 2) return true;
            const body = document.body.innerText || '';
            return /\\b\\d{5}\\b/.test(body) && /depart|arrival|available|book now/i.test(body);
        })()
        """))

    async def ask_human_confirm(self, message: str, tag: str) -> bool:
        """Wait for user to click confirm button (DONE) in Streamlit."""
        reply = await self.ask_human(message, tag=tag)
        return self._normalize_reply(reply).upper() == "DONE"

    async def _has_train_results(self) -> bool:
        return await self._vision_yes(
            "Is a list of train search results visible with train numbers and departure times?"
        )

    async def _is_booking_form_empty(self) -> bool:
        return await self._vision_yes(
            "Is the page showing an empty Book Ticket search form with blank From/To fields and no train list?"
        )

    # ─── MAIN BOOKING FLOW ───────────────────────────────────────────────────

    async def run_booking(self):
        """Full IRCTC booking flow."""
        self.session.status = "running"

        steps = [
            ("open_irctc", self._step_open_irctc),
            ("login", self._step_login),
            ("search", self._step_search_trains),
            ("select_train", self._step_select_train),
            ("passengers", self._step_add_passengers),
            ("payment", self._step_payment_handoff),
        ]

        try:
            for name, step_fn in steps:
                await step_fn()
                self.session.steps_done.append(name)
                if self.session.status == "failed":
                    break
        except Exception as e:
            self._log(f"❌ Agent error: {e}")
            self.session.status = "failed"

        return self.session.status

    # ── STEP 1: Open IRCTC ──────────────────────────────────────────────────

    async def _step_open_irctc(self):
        self._log("📍 Step 1: Opening IRCTC website")
        await self.navigate("https://www.irctc.co.in/nget/train-search")
        await asyncio.sleep(1.5)

        # Dismiss popup via DOM first (faster than vision round-trip)
        dismissed = await self._click_dom("""
        (() => {
            for (const el of document.querySelectorAll('button, a, span, i')) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
                if (/close|dismiss|accept|ok|×|✕/i.test(t) && t.length < 20) {
                    el.click();
                    return true;
                }
            }
            return false;
        })()
        """)
        if not dismissed:
            has_popup = await self.vision_ask(
                "Is there a popup, modal, or cookie consent visible? YES or NO"
            )
            if "YES" in has_popup.upper():
                self._log("  Closing popup...")
                await self.vision_click("the close/dismiss button on the popup or modal")

        self._log("✅ IRCTC loaded")
        return True

    # ── STEP 2: Login ───────────────────────────────────────────────────────

    async def _step_login(self):
        self._log("📍 Step 2: Login")

        if await self._is_logged_in():
            self._log("  Already logged in, skipping")
            return True

        # Collect credentials FIRST — modal closes if user takes too long while typing
        username, password = await self.ask_human_login()
        if not username or not password:
            return self._fail_step("No username/password provided")
        if username == password:
            self._log("  ⚠ Password looks same as username — double-check your password")

        if not await self._ensure_login_modal():
            manual = await self.ask_human(
                "Login popup did not open automatically.\n"
                "Click **LOGIN** in the Chrome window, then type **READY** here:"
            )
            if manual.strip().upper() != "READY" or not await self._ensure_login_modal():
                return self._fail_step("Login popup not open — click LOGIN in Chrome and restart")

        if not await self._fill_login_credentials(username, password):
            self._log("  DOM fill failed — trying vision fallback")
            await self.vision_type("the User Name input in the login popup dialog", username)
            await self.vision_type("the Password input in the login popup dialog", password)

        filled = await self._evaluate("""
        (() => {
            const u = document.querySelector('input[formcontrolname="userid"], #usernameId, input[placeholder*="User" i]');
            const p = document.querySelector('input[formcontrolname="password"], input[type="password"]');
            return !!(u && u.value && p && p.value);
        })()
        """)
        if not filled:
            self._log("  ⚠ Auto-fill failed — complete login manually in Chrome")
            done = await self.ask_human(
                "In Chrome: fill username, password, CAPTCHA if shown, click SIGN IN.\n"
                "When you see **MY ACCOUNT** or **Logout** in the header, type **DONE** here:"
            )
            if done.strip().upper() == "DONE" and await self._is_logged_in():
                self._log("✅ Login successful (manual)")
                return True
            if await self._is_logged_in():
                self._log("✅ Login successful")
                return True
            return self._fail_step("Login fields not filled — complete login in Chrome")

        if await self._is_login_modal_visible():
            has_captcha = await self._evaluate("""
            !!document.querySelector(
                'input[formcontrolname="captcha"], input[placeholder*="captcha" i], .captcha-img'
            )
            """)
            if has_captcha:
                self._log("  CAPTCHA detected — enter it below")
                captcha_text = await self.ask_human(
                    "Type the CAPTCHA exactly as shown in the Chrome login popup:"
                )
                if captcha_text.strip():
                    await self._set_react_input(
                        ['input[formcontrolname="captcha"]', 'input[placeholder*="Captcha" i]'],
                        captcha_text.strip(),
                        scope="login",
                    ) or await self.vision_type("the CAPTCHA input in the login popup", captcha_text)

            if not await self._click_sign_in():
                await self.vision_click("the SIGN IN button in the login popup")
            await asyncio.sleep(4)

        if await self._is_logged_in():
            self._log("✅ Login successful")
            return True

        has_otp = await self._evaluate("""
        !!document.querySelector('input[formcontrolname="otp"], input[placeholder*="OTP" i]')
        """)
        if has_otp:
            otp = await self.ask_human("Enter the OTP sent to your registered mobile number:")
            if otp.strip():
                await self._set_react_input(
                    ['input[formcontrolname="otp"]', 'input[placeholder*="OTP" i]'],
                    otp.strip(),
                    scope="login",
                ) or await self.vision_type("the OTP input field", otp)
                await self._click_sign_in() or await self.vision_click("the Submit or Verify OTP button")
                await asyncio.sleep(4)

        if await self._is_logged_in():
            self._log("✅ Login successful")
            return True

        self._log("  ⚠ Login not confirmed yet — finish in Chrome (CAPTCHA/OTP)")
        if await self._wait_for_login_complete():
            return True

        return self._fail_step("Login not completed — Chrome left open for you to retry")

    # ── STEP 3: Search trains ───────────────────────────────────────────────

    async def _step_search_trains(self):
        self._log("📍 Step 3: Searching trains")
        # Always use today's date for search (avoids stale/past dates from UI defaults)
        self.session.date = datetime.now().strftime("%d/%m/%Y")
        self._log(
            f"  Route: {self.session.source_name} ({self.session.source}) → "
            f"{self.session.dest_name} ({self.session.destination})"
        )
        self._log(f"  Date: {self.session.date} (today) · Class: {self.session.train_class}")

        await self.navigate("https://www.irctc.co.in/nget/train-search")
        await self._dismiss_blocking_overlays()
        if not await self._wait_for_search_form():
            self._log("  ⚠ Waiting for booking form to load...")
            await asyncio.sleep(3)
        await self._dismiss_blocking_overlays()
        await asyncio.sleep(0.8)

        class_labels = {
            "SL": "Sleeper (SL)",
            "3A": "AC 3 Tier (3A)",
            "2A": "AC 2 Tier (2A)",
            "1A": "AC First Class (1A)",
            "CC": "AC Chair Car (CC)",
            "EC": "Executive Class (EC)",
            "2S": "Second Sitting (2S)",
        }
        class_label = class_labels.get(self.session.train_class, self.session.train_class)

        for attempt in range(1, 4):
            self._log(f"  Search attempt {attempt}/3 (DOM)")

            if not await self._fill_station_dom("origin", self.session.source_name, self.session.source):
                self._log("  ⚠ From station not set — retrying")
                await self._dismiss_blocking_overlays()
                continue

            if not await self._fill_station_dom(
                "destination", self.session.dest_name, self.session.destination
            ):
                self._log("  ⚠ To station not set — retrying")
                continue

            if not await self._fill_journey_date(self.session.date):
                if await self._focus_input([
                    'input[formcontrolname="journeyDate"]',
                    'input[placeholder*="Date" i]',
                    'p-calendar input',
                ]):
                    await self._clear_focused_field()
                    await self._type_keys(self.session.date)
                    await self._press_key("Tab")
                    await asyncio.sleep(0.3)

            if not await self._select_class_dom(self.session.train_class, class_label):
                self._log(f"  ⚠ Class dropdown — using default if already set")

            if not await self._click_search_trains_dom():
                self._log("  ⚠ Search button not found via DOM")
                continue

            await asyncio.sleep(4)

            if await self._has_train_results_dom() or await self._has_train_results():
                self._log("✅ Train results found")
                return True

            self._log("  ⚠ No train list yet — retrying")

        self._log("  ⚠ Automatic search failed — fill form in Chrome, then confirm")
        confirmed = await self.ask_human_confirm(
            f"Fill the booking form in Chrome:\n"
            f"• From: {self.session.source_name} ({self.session.source})\n"
            f"• To: {self.session.dest_name} ({self.session.destination})\n"
            f"• Date: {self.session.date}\n"
            f"• Class: {class_label}\n"
            "Click **Search Trains**, then press the green button below.",
            tag="SEARCH_DONE",
        )

        if confirmed and (
            await self._has_train_results_dom() or await self._has_train_results()
        ):
            self._log("✅ Train results visible after manual search")
            return True

        if await self._has_train_results_dom():
            self._log("✅ Train results visible")
            return True

        return self._fail_step(
            "Train search failed — no results visible. Check stations/date in Chrome and try again."
        )

    # ── STEP 4: Select train ────────────────────────────────────────────────

    async def _step_select_train(self):
        self._log("📍 Step 4: Selecting train")

        if not await self._has_train_results():
            return self._fail_step(
                "No train results on screen — search must succeed before selecting a train."
            )

        train_info = await self.vision_ask(
            "List the trains visible with their numbers, names, and departure times. Be concise."
        )
        self._log(f"  Trains found:\n{train_info}")

        chosen = await self.ask_human(
            f"Trains available for {self.session.date}:\n\n{train_info}\n\n"
            f"Type the train number to book (e.g. 12676), or press Enter for the first available train:"
        )

        if not chosen.strip():
            await self.vision_click("the first available Book Now or Available button on a train row")
        else:
            await self.vision_click(
                f"the Book Now or Available button for train number {chosen.strip()}"
            )

        await asyncio.sleep(3)

        if await self._vision_yes("Is a class/quota selection page showing with SL, 3A, 2A options?"):
            self._log(f"  Selecting class {self.session.train_class}")
            await self.vision_click(
                f"the {self.session.train_class} class option showing availability"
            )
            await asyncio.sleep(2)
            self._log("✅ Train and class selected")
            return True

        if await self._is_booking_form_empty():
            return self._fail_step("Still on empty search form — train was not selected.")

        status = await self.vision_ask("What is shown on screen now? One sentence.")
        self._log(f"  Current state: {status}")
        return True

    # ── STEP 5: Add passengers ──────────────────────────────────────────────

    async def _step_add_passengers(self):
        self._log("📍 Step 5: Adding passenger details")

        # Check if on passenger form
        on_form = await self.vision_ask(
            "Is a passenger details form visible with fields for name, age, gender? YES or NO"
        )

        if "NO" in on_form.upper():
            # Try clicking book now
            await self.vision_click("the BOOK NOW or BOOK TICKET button")
            await asyncio.sleep(3)

        # Get passenger details from human
        self._log(f"  Need details for {self.session.passengers} passengers")

        for i in range(self.session.passengers):
            self._log(f"  Passenger {i+1}:")

            name = await self.ask_human(f"Passenger {i+1} — Full name (as in ID proof):")
            age = await self.ask_human_validated(
                f"Passenger {i+1} — Age:",
                validator=lambda v: v.isdigit() and 1 <= int(v) <= 120,
                hint="Enter a number between 1 and 120.",
            )
            gender = await self.ask_human_validated(
                f"Passenger {i+1} — Gender (M/F):",
                validator=lambda v: v.upper().startswith(("M", "F")),
                hint="Enter M for Male or F for Female.",
            )

            if i > 0:
                await self.vision_click("the ADD PASSENGER button")
                await asyncio.sleep(1)

            await self.vision_type(f"the Passenger Name field for row {i + 1}", name)
            await asyncio.sleep(0.3)
            await self.vision_type(f"the Age field for row {i + 1}", age)
            await asyncio.sleep(0.3)
            await self.vision_click(f"the Gender dropdown for row {i + 1}")
            await asyncio.sleep(0.5)
            gender_label = "Male" if gender.upper().startswith("M") else "Female"
            await self.vision_click(f"{gender_label} in the gender dropdown")
            await asyncio.sleep(0.3)

        berth_pref = await self.ask_human(
            "Berth preference? (LB/MB/UB/SL/SU, or press Enter to skip):"
        )
        if berth_pref.strip():
            await self.vision_click("the Berth Preference dropdown")
            await asyncio.sleep(0.5)
            await self.vision_click(f"{berth_pref.strip().upper()} in the berth dropdown")

        mobile = await self.ask_human_validated(
            "Your mobile number for booking confirmation (10 digits):",
            validator=lambda v: v.isdigit() and len(v) == 10,
            hint="Enter exactly 10 digits, e.g. 9876543210.",
        )
        await self.vision_type("the mobile number or contact number field", mobile)
        await asyncio.sleep(0.3)

        if await self._is_booking_form_empty():
            return self._fail_step("Passenger form not reached — still on empty search page.")

        self._log("✅ Passenger details filled")

        # Show summary for confirmation
        form_summary = await self.vision_ask(
            "Describe the booking summary shown — total fare, passenger names, train details. Be specific."
        )
        self._log(f"  Booking summary:\n{form_summary}")

        # Confirm before proceeding to payment
        confirm = await self.ask_human(
            f"BOOKING SUMMARY:\n{form_summary}\n\n"
            f"Type YES to proceed to payment, or NO to cancel:"
        )

        if confirm.strip().upper() != "YES":
            return self._fail_step("User cancelled booking")

        if await self._is_booking_form_empty():
            return self._fail_step("Cannot proceed — booking summary not visible.")

        await self.vision_click("the CONTINUE or PROCEED TO PAYMENT button")
        await asyncio.sleep(3)
        self._log("✅ Proceeding to payment")
        return True

    # ── STEP 6: Payment handoff ─────────────────────────────────────────────

    async def _step_payment_handoff(self):
        self._log("📍 Step 6: Payment")
        self._log("  ⚠️  PAYMENT STEP — Agent will NOT auto-complete payment")
        self._log("  Please complete payment manually in the browser window")

        payment_info = await self.vision_ask(
            "What payment options are shown? List them briefly."
        )
        self._log(f"  Payment options: {payment_info}")

        total = await self.vision_ask(
            "What is the total amount to be paid? Look for fare/amount. Reply with just the amount."
        )
        self._log(f"  Total amount: {total}")

        # Hand control to human for payment
        await self.ask_human(
            f"💳 PAYMENT REQUIRED\n\n"
            f"Total: {total}\n"
            f"Payment options: {payment_info}\n\n"
            f"Complete payment in the Chrome window.\n"
            f"After payment succeeds and you see PNR, type **DONE** here:"
        )

        if await self._vision_yes("Is a PNR number or booking confirmation visible?"):
            pnr_info = await self.vision_ask("What is the PNR number? Reply with just the PNR.")
            self._log(f"✅ Booking confirmed — PNR: {pnr_info}")
            self.session.status = "done"
            return True

        if await self._is_booking_form_empty():
            return self._fail_step(
                "Payment not completed — page still shows empty search form, not a confirmation."
            )

        pnr_info = await self.vision_ask(
            "Is the booking confirmed? If yes, what is the PNR number?"
        )
        if "PNR" in pnr_info.upper() or any(c.isdigit() for c in pnr_info):
            self._log(f"✅ Booking result: {pnr_info}")
            self.session.status = "done"
            return True

        return self._fail_step(f"Booking not confirmed: {pnr_info[:120]}")

    # ─── HELPERS ─────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        self.session.log.append(line)
        print(line)

    async def _wait_for_chrome(self, retries: int = 25) -> str:
        for _ in range(retries):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        f"http://localhost:{self.port}/json",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as r:
                        tabs = await r.json()
                        for tab in tabs:
                            if (
                                tab.get("type") == "page"
                                and tab.get("webSocketDebuggerUrl")
                                and not tab.get("url", "").startswith("chrome-extension://")
                            ):
                                return tab["webSocketDebuggerUrl"]
                        for tab in tabs:
                            if tab.get("webSocketDebuggerUrl"):
                                return tab["webSocketDebuggerUrl"]
            except Exception:
                pass
            await asyncio.sleep(0.6)
        raise RuntimeError(f"Chrome not ready on port {self.port}")

    def _find_chrome(self) -> str:
        import sys
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ]
        elif sys.platform == "darwin":
            candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        else:
            candidates = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
                "/snap/bin/chromium",
            ]

        for p in candidates:
            if os.path.exists(p):
                return p

        # Try which
        for name in ["google-chrome", "chromium-browser", "chromium"]:
            r = subprocess.run(["which", name], capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout.strip()

        raise RuntimeError(
            "Chrome not found!\n"
            "Windows: Install from https://google.com/chrome\n"
            "Ubuntu:  sudo apt install google-chrome-stable"
        )
