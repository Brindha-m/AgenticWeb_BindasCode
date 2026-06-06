"""
agent/cdp_engine.py
-------------------
Path B: Raw Chrome DevTools Protocol over WebSocket.
No Playwright. No Puppeteer. Direct browser control.

Advantages over Playwright:
  - No automation fingerprint from Playwright's JS patches
  - Full network interception (modify requests/responses mid-flight)
  - Access to browser internals (memory, V8 runtime, coverage)
  - Vision-based clicking by pixel coordinate (LLM says where, we click there)
  - Can hook into events Playwright doesn't expose

How it works:
  1. Launch Chrome with --remote-debugging-port=9222
  2. Connect via WebSocket to Chrome's JSON/RPC protocol
  3. Send CDP commands (Page.navigate, Input.click, Runtime.evaluate, ...)
  4. Receive responses and events
"""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import aiohttp
import websockets


class CDPEngine:
    """
    Direct Chrome DevTools Protocol automation engine.
    """

    def __init__(self, headless: bool = True, port: int = 9222):
        self.port = port
        self.headless = headless
        self.ws = None
        self._cmd_id = 0
        self._pending: dict = {}      # id -> asyncio.Future
        self._events: list = []       # incoming CDP events buffer
        self._chrome_proc = None
        self._listen_task = None
        self._profile_dir = None

    # ─── LIFECYCLE ──────────────────────────────────────────────────────────

    async def launch(self):
        chrome_path = self._find_chrome()
        headless_flag = "--headless=new" if self.headless else ""
        self._profile_dir = tempfile.mkdtemp(prefix="agent_chrome_")

        cmd = [
            chrome_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1366,768",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            (
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        ]
        if headless_flag:
            cmd.append(headless_flag)

        self._chrome_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for Chrome to boot and open its debug socket
        ws_url = await self._wait_for_chrome()

        self.ws = await websockets.connect(
            ws_url,
            max_size=100_000_000,
            ping_interval=None,
        )

        self._listen_task = asyncio.create_task(self._listen_loop())

        # Enable required CDP domains
        await self._cmd("Page.enable")
        await self._cmd("DOM.enable")
        await self._cmd("Runtime.enable")
        await self._cmd("Network.enable")

        # Patch out automation signals at browser level
        await self._cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            """
        })

        await self._cmd("Page.navigate", {"url": "about:blank"}, timeout=10)
        await asyncio.sleep(0.5)

        return self

    async def close(self):
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()
        if self._chrome_proc:
            self._chrome_proc.terminate()
        if self._profile_dir and os.path.isdir(self._profile_dir):
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None

    # ─── CDP CORE ───────────────────────────────────────────────────────────

    async def _listen_loop(self):
        """Background task: routes CDP messages to waiting futures"""
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                msg_id = msg.get("id")
                if msg_id and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(msg["error"].get("message", "CDP error")))
                        else:
                            fut.set_result(msg.get("result", {}))
                elif "method" in msg:
                    # Keep last 100 events
                    self._events.append(msg)
                    if len(self._events) > 100:
                        self._events.pop(0)
        except Exception:
            pass

    async def _cmd(self, method: str, params: dict = None, timeout: float = 15) -> dict:
        """Send a CDP command and await its response"""
        self._cmd_id += 1
        cmd_id = self._cmd_id
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[cmd_id] = fut

        await self.ws.send(json.dumps({
            "id": cmd_id,
            "method": method,
            "params": params or {},
        }))

        return await asyncio.wait_for(fut, timeout=timeout)

    # ─── OBSERVATION ────────────────────────────────────────────────────────

    async def observe(self) -> dict:
        """Full page state for the planner: screenshot + elements + URL"""
        screenshot_b64 = await self.get_screenshot_b64()
        url = await self.get_url()
        title = await self.evaluate("document.title") or ""
        elements = await self._get_interactive_elements()

        return {
            "url": url,
            "page_title": title,
            "screenshot_b64": screenshot_b64,
            "interactive_elements": elements,
        }

    async def get_screenshot_b64(self) -> str:
        result = await self._cmd("Page.captureScreenshot", {
            "format": "jpeg",
            "quality": 65,
            "clip": {"x": 0, "y": 0, "width": 1366, "height": 768, "scale": 1},
        })
        return result["data"]

    async def get_url(self) -> str:
        result = await self.evaluate("window.location.href")
        return result or "unknown"

    async def _get_interactive_elements(self) -> list:
        result = await self._cmd("Runtime.evaluate", {
            "expression": """(() => {
                const sel = 'button,input,select,textarea,a[href],[role="button"],[role="link"],[onclick]';
                const all = document.querySelectorAll(sel);
                const out = [];
                let idx = 0;
                for (const el of all) {
                    if (!el.offsetParent) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const text = (
                        el.innerText || el.value || el.placeholder ||
                        el.getAttribute('aria-label') || el.getAttribute('title') || ''
                    ).trim().replace(/\\s+/g,' ').slice(0,80);
                    if (!text) continue;
                    out.push({
                        index: idx++,
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        text,
                        name: el.name || el.id || '',
                        x: Math.round(r.x + r.width/2),
                        y: Math.round(r.y + r.height/2),
                    });
                    if (idx >= 60) break;
                }
                return JSON.stringify(out);
            })()""",
            "returnByValue": True,
        })
        raw = result.get("result", {}).get("value", "[]")
        try:
            return json.loads(raw)
        except Exception:
            return []

    # ─── ACTIONS ────────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict:
        try:
            from agent.url_utils import urls_equivalent

            current = await self.get_url()
            if current and urls_equivalent(url, current):
                await asyncio.sleep(0.3)
                return {"success": True, "url": current, "skipped": True}

            await self._cmd("Page.navigate", {"url": url}, timeout=20)
            # Wait for load event
            for _ in range(40):
                await asyncio.sleep(0.25)
                if any(e["method"] == "Page.loadEventFired" for e in self._events[-20:]):
                    break
            await asyncio.sleep(0.5)
            return {"success": True, "url": await self.get_url()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def click(self, index: int) -> dict:
        """
        Click element by index.
        CDP Path: get element coordinates from DOM → dispatch mouse events at pixel level.
        This is what makes us undetectable — real mouse events at real coordinates.
        """
        try:
            elements = await self._get_interactive_elements()
            if index >= len(elements):
                return {"success": False, "error": f"Index {index} out of range"}

            el = elements[index]
            x, y = el["x"], el["y"]

            # Scroll element into view first
            await self.evaluate(f"""
                (() => {{
                    const sel = 'button,input,select,textarea,a[href],[role="button"],[onclick]';
                    const visible = [...document.querySelectorAll(sel)].filter(e => e.offsetParent);
                    if (visible[{index}]) visible[{index}].scrollIntoView({{block:'center'}});
                }})()
            """)
            await asyncio.sleep(0.3)

            # Real mouse press + release at pixel coordinates
            await self._cmd("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y,
                "buttons": 0, "modifiers": 0,
            })
            await asyncio.sleep(0.05)
            await self._cmd("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1, "buttons": 1,
            })
            await asyncio.sleep(0.08)
            await self._cmd("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1, "buttons": 0,
            })

            # Wait for possible navigation
            await asyncio.sleep(1.5)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def vision_click(self, screenshot_b64: str, instruction: str) -> dict:
        """
        UNIQUE TO CDP PATH: Ask the vision LLM WHERE to click (x,y coordinates),
        then click those exact pixels via CDP Input events.
        Makes us completely immune to DOM structure changes.
        """
        import anthropic as ant
        import os
        from agent.planner import CLAUDE_MODEL

        cli = ant.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = cli.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=64,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": screenshot_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Return ONLY a JSON object with x and y pixel coordinates "
                            f"of where to click to: {instruction}\n"
                            f"Example: {{\"x\": 342, \"y\": 187}}\n"
                            f"The image is 1366x768 pixels."
                        ),
                    },
                ],
            }],
        )
        raw = response.content[0].text.strip().strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        coords = json.loads(raw)
        x, y = int(coords["x"]), int(coords["y"])

        await self._cmd("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })
        await self._cmd("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1, "buttons": 1,
        })
        await asyncio.sleep(0.08)
        await self._cmd("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1, "buttons": 0,
        })
        await asyncio.sleep(1.0)
        return {"success": True, "clicked_at": {"x": x, "y": y}}

    async def type_text(self, index: int, text: str) -> dict:
        """Type into an input field character-by-character via CDP key events"""
        try:
            # Focus the element first
            await self.evaluate(f"""
                (() => {{
                    const inputs = [...document.querySelectorAll('input,textarea,select')]
                        .filter(e => e.offsetParent);
                    if (inputs[{index}]) {{
                        inputs[{index}].focus();
                        inputs[{index}].value = '';
                    }}
                }})()
            """)
            await asyncio.sleep(0.2)

            for char in text:
                await self._cmd("Input.dispatchKeyEvent", {
                    "type": "keyDown", "text": char, "key": char,
                })
                await self._cmd("Input.dispatchKeyEvent", {
                    "type": "keyUp", "text": char, "key": char,
                })
                # Also fire input event so React/Angular state updates
                await self.evaluate(f"""
                    (() => {{
                        const el = document.activeElement;
                        if (el) {{
                            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            nativeInputValueSetter.call(el, el.value + {json.dumps(char)});
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }}
                    }})()
                """)
                await asyncio.sleep(0.04)

            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def scroll(self, direction: str = "down", amount: int = 400) -> dict:
        try:
            y = amount if direction == "down" else -amount
            await self.evaluate(f"window.scrollBy(0, {y})")
            await asyncio.sleep(0.5)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def intercept_network(self, url_pattern: str = "*"):
        """
        UNIQUE CDP POWER: Intercept any network request before it's sent.
        Use to: block ads, inject auth headers, modify POST bodies.
        """
        await self._cmd("Fetch.enable", {
            "patterns": [{"urlPattern": url_pattern, "requestStage": "Request"}]
        })
        # Events will appear in self._events as Fetch.requestPaused
        # Call continue_request() to let them through

    async def continue_request(self, request_id: str):
        await self._cmd("Fetch.continueRequest", {"requestId": request_id})

    async def evaluate(self, js: str):
        """Run JS in page context, return the value"""
        try:
            result = await self._cmd("Runtime.evaluate", {
                "expression": js,
                "returnByValue": True,
                "awaitPromise": True,
            })
            return result.get("result", {}).get("value")
        except Exception:
            return None

    async def wait(self, seconds: float = 2) -> dict:
        await asyncio.sleep(seconds)
        return {"success": True}

    # ─── SETUP HELPERS ──────────────────────────────────────────────────────

    async def _wait_for_chrome(self, retries: int = 20) -> str:
        for i in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://localhost:{self.port}/json",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        tabs = await resp.json()
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
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Chrome did not start on port {self.port} after {retries} attempts")

    def _find_chrome(self) -> str:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path

        # Try which
        result = subprocess.run(["which", "google-chrome"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()

        result = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()

        raise RuntimeError(
            "Chrome/Chromium not found. Install with: "
            "sudo apt install chromium-browser  OR  brew install --cask google-chrome"
        )
