"""
agent/planner.py
----------------
LLM brain. Given the current page state (screenshot + DOM elements),
returns the single next action the agent should take.
"""

import json
import os
import urllib.error
import urllib.request

import anthropic

# anthropic | openai (OpenAI-compatible: Groq, OpenRouter, etc.)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

SYSTEM_PROMPT = """You are an expert web automation agent. You control a real browser.

Given:
- The current screenshot of the browser
- A list of interactive elements on the page (with index numbers)
- The task to complete
- History of actions taken so far

Your job: output the SINGLE best next action as a JSON object.

Available action types:
  {"type": "navigate",   "url": "https://..."}
  {"type": "click",      "index": 3,  "reason": "click login button"}
  {"type": "type",       "index": 1,  "text": "Chennai Central", "reason": "enter source station"}
  {"type": "select",     "index": 2,  "value": "SL",  "reason": "choose sleeper class"}
  {"type": "scroll",     "direction": "down", "amount": 400}
  {"type": "wait",       "seconds": 2, "reason": "waiting for results to load"}
  {"type": "ask_user",   "question": "Please solve the CAPTCHA shown and type the text"}
  {"type": "ask_user",   "question": "Please enter the OTP sent to your mobile"}
  {"type": "confirm",    "summary": "About to pay ₹450 for ticket. Confirm?"}
  {"type": "done",       "result": "Task complete. PNR: 1234567890. Screenshot saved."}
  {"type": "failed",     "reason": "Could not find available seats after 5 attempts"}

Rules:
- Return ONLY the JSON object. No markdown. No explanation.
- For financial actions (payment/booking), always use {"type": "confirm"} first.
- If you see a CAPTCHA, use {"type": "ask_user"}.
- If you see an OTP field, use {"type": "ask_user"}.
- Index numbers refer to the interactive elements list provided.
- If the same action fails twice, try a different approach.
- Prefer clicking visible, labeled buttons over guessing hidden elements.
- NAVIGATION IS EXPENSIVE: the browser already opened the task's start URL for you.
  Do NOT use {"type": "navigate"} if you are already on the correct website — click links instead.
  Never navigate to a different path on the same domain (e.g. /quickpay) — click the matching menu/link.
  Only navigate when the current page host is completely wrong (different website).
- When the goal is met (form visible, bill details shown, status text readable), use {"type": "done"} with a summary.
- If you need data from the user (consumer number, OTP, login), use {"type": "ask_user"} instead of navigating away.
"""


def _friendly_api_error(exc: Exception) -> str:
    msg = str(exc)
    if "credit balance is too low" in msg or "insufficient" in msg.lower():
        return (
            "Anthropic API credits exhausted. Options: (1) Add credits at console.anthropic.com, "
            "(2) Set LLM_PROVIDER=openai + OPENAI_API_KEY in .env, "
            "(3) For India Post tracking, enable **Scripted mode (no Claude)** on Government Services."
        )
    if "invalid_request_error" in msg and "api_key" in msg.lower():
        return "Invalid API key. Check ANTHROPIC_API_KEY or OPENAI_API_KEY in .env."
    return msg[:400]


def _decide_openai(
    task: str,
    page_state: dict,
    history: list[dict],
    stuck_warning: str,
    start_url: str,
    start_host: str,
    user_text: str,
) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {
            "type": "failed",
            "reason": "OPENAI_API_KEY missing. Set LLM_PROVIDER=openai in .env or use scripted mode for India Post.",
        }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[
            {"role": m["role"], "content": m["content"] if isinstance(m["content"], str) else str(m["content"])}
            for m in history[-12:]
        ],
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{page_state['screenshot_b64']}",
                    },
                },
                {"type": "text", "text": user_text},
            ],
        },
    ]

    body = json.dumps({"model": OPENAI_MODEL, "max_tokens": 300, "messages": messages}).encode()
    req = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
        raw = data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else str(e)
        return {"type": "failed", "reason": _friendly_api_error(Exception(err_body))}
    except Exception as e:
        return {"type": "failed", "reason": _friendly_api_error(e)}

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "wait", "seconds": 2, "reason": f"Parse error: {raw[:80]}"}


def decide_next_action(
    task: str,
    page_state: dict,
    history: list[dict],
    stuck_warning: str = "",
    start_url: str = "",
    start_host: str = "",
) -> dict:
    """
    Synchronous call to Claude to get the next action.

    page_state = {
        "url": str,
        "screenshot_b64": str,          # JPEG base64
        "interactive_elements": list,   # [{index, tag, text, type, name}, ...]
        "page_title": str,
    }
    history = list of {"role": "user"|"assistant", "content": str}
    """

    # Format elements into readable text for the LLM
    elements_text = "\n".join(
        f"  [{el['index']}] <{el['tag']}> {el.get('type','')} — \"{el['text']}\""
        + (f" (name={el['name']})" if el.get("name") else "")
        for el in page_state.get("interactive_elements", [])
    ) or "  (no interactive elements detected)"

    user_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": page_state["screenshot_b64"],
            },
        },
        {
            "type": "text",
            "text": f"""TASK: {task}

Browser start URL (already loaded — do not re-navigate on this site): {start_url or 'see current URL'}
Current URL: {page_state.get('url', 'unknown')}
Page title:  {page_state.get('page_title', 'unknown')}
Target host: {start_host or 'unknown'}
{stuck_warning}

Interactive elements on screen:
{elements_text}

Steps taken so far: {len(history) // 2}

What is the single next action? Prefer click/type/ask_user/done over navigate.""",
        },
    ]

    messages = [
        *history[-12:],  # keep last 6 turns to manage context
        {"role": "user", "content": user_content},
    ]

    if LLM_PROVIDER == "openai":
        return _decide_openai(
            task, page_state, history, stuck_warning, start_url, start_host, user_content[1]["text"]
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {
            "type": "failed",
            "reason": (
                "ANTHROPIC_API_KEY is missing. Add credits/key, set LLM_PROVIDER=openai, "
                "or use scripted mode (India Post) on Government Services."
            ),
        }

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        raw = response.content[0].text.strip()
    except Exception as e:
        return {"type": "failed", "reason": _friendly_api_error(e)}

    # Strip markdown fences if model wraps in them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: if LLM returns malformed JSON, wait and retry
        return {"type": "wait", "seconds": 2, "reason": f"Parse error: {raw[:80]}"}
