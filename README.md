## Made It — AI Agentic Browser Automation

> **From IRCTC and Tirupati to FASTag, dedicated agents for the services that matter the most. 🚀**

> **Team Bindas Code** | Microsoft Hackathon Submission

**Made It** is an AI-powered browser automation platform that completes real-world tasks on Indian government portals, railways, buses, flights, and utility sites. Users describe what they need in plain English; intelligent agents navigate, fill forms, handle CAPTCHAs and OTPs with human-in-the-loop safety, and return structured results.

---

## Problem

Citizens spend hours navigating fragmented `.gov.in` portals, IRCTC, and travel sites — each with different layouts, CAPTCHAs, OTP flows, and payment gates. Manual form-filling is error-prone; existing RPA tools break when sites change and cannot reason about new pages.

## Solution

**Made It** combines **LLM planning** (Claude / OpenAI) with **Playwright** and optional **raw Chrome DevTools Protocol (CDP)** to observe pages, decide the next action, act in a real browser, and recover from failures. A **scripted mode** runs fixed Playwright flows with zero API cost for demos and production reliability.

<img width="1870" height="1445" alt="03_solution" src="https://github.com/user-attachments/assets/8f7f5f9b-184b-4581-9762-a41d27334a3d" />


### Supported services

| Category | Services |
|----------|----------|
| **Travel** | IRCTC railways, RedBus/AbhiBus, flights (MakeMyTrip), state transport |
| **Government** | Tirupati TTD, Passport Seva, India Post tracking, TNEB bill pay, FASTag, PAN/GST/LPG, exam portals |
| **Tools** | CDP Inspector, Playwright vs CDP compare, task history |

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Streamlit UI (app.py + pages/)               │
│   Home · IRCTC · Bus · Flights · Gov Services · CDP Inspector   │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │     travel_runner.py        │
              └──────────────┬──────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐
  │ Orchestrator│    │ scripted_    │    │ irctc_playwright  │
  │ observe→plan│    │ flows.py     │    │ (dedicated rail)  │
  │ →act→verify │    │ (no LLM)     │    └───────────────────┘
  └──────┬──────┘    └──────┬───────┘
         │                  │
         ▼                  ▼
  ┌─────────────┐    ┌───────────────────────────┐
  │ planner.py  │    │ playwright_engine.py      │
  │ Claude/GPT  │    │ OR cdp_engine.py (raw CDP)│
  └─────────────┘    └───────────────────────────┘
         │                  │
         └────────┬─────────┘
                  ▼
         ┌─────────────────┐
         │  Chrome Browser  │  ← Human-in-the-loop (CAPTCHA, OTP, payment)
         └─────────────────┘
```

**Pattern:** Customer-support orchestration — Orchestrator delegates to AI Agent; Human Reviewer approves payments and solves CAPTCHAs; policy violations loop back for re-planning.

---

## AI tools used

| Tool | Role |
|------|------|
| **Anthropic Claude** (`claude-sonnet-4-6`) | Default LLM — screenshot + DOM → next action JSON |
| **OpenAI-compatible APIs** (Groq, OpenRouter) | Fallback via `LLM_PROVIDER=openai` |
| **Playwright** | Primary browser engine — auto-wait, selectors, reliability |
| **Raw CDP** | Zero-abstraction Chrome control — vision clicks, network intercept |
| **Streamlit** | Multipage web UI with live step streaming and screenshots |

Action types the planner emits: `navigate`, `click`, `type`, `select`, `scroll`, `wait`, `ask_user`, `confirm`, `done`, `failed`.

---

## Setup instructions

### Prerequisites

- Python 3.9+
- Google Chrome (for Playwright/CDP)
- Windows: Proactor event loop is configured automatically in `app.py`

### Install

```bash
git clone https://github.com/Brindha-m/AgenticWeb_BindasCode.git
cd AgenticWeb_BindasCode

python -m venv .venv
.venv\Scripts\activate          

pip install -r requirements.txt

playwright install chromium
```

### Configure Edit `.env`:

- **Scripted mode (no API key):** `DEFAULT_AGENT_MODE=scripted`
- **AI mode:** set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` + `LLM_PROVIDER=openai`
- **IRCTC:** `IRCTC_USERNAME`, `IRCTC_PASSWORD`, journey stations/dates

### Run

```bash
streamlit run app.py
```

Open `http://localhost:8501`. Use the sidebar to switch pages (IRCTC, Bus, Government Services, etc.).

### Smoke tests

```bash
python scripts/smoke_orchestrator.py
python scripts/smoke_playwright.py
python scripts/run_irctc.py
```

---

## Dependencies

See `requirements.txt`:

- `streamlit>=1.35.0` — UI
- `anthropic>=0.30.0` — Claude API
- `playwright>=1.44.0` — browser automation
- `aiohttp`, `websockets` — CDP WebSocket
- `python-dotenv`, `pillow`

---

## Deployment & live demo

| Option | Notes |
|--------|-------|
| **Local + ngrok** | Best for full Playwright demo: `ngrok http 8501` |
| **Streamlit Cloud** | UI only — Playwright cannot launch Chrome in cloud sandbox |
| **VM / Azure** | Run `streamlit run app.py --server.port 8501 --server.address 0.0.0.0` |

**Judge access:** No app login required. Use **Scripted mode** on any service page. OTP/CAPTCHA appear in the yellow human-input panel when automating real portals locally.

>  **Live URL:** [https://agenticweb-bindascode.streamlit.app/](https://agenticweb-bindascode.streamlit.app/)

>  **Demo video:** [https://youtu.be/IlBGhVNE2Ms](https://youtu.be/IlBGhVNE2Ms)

---

## Project structure

```
made-it/
├── app.py                 # Main Streamlit entry (Made It home)
├── requirements.txt
├── .env.example
├── agent/
│   ├── orchestrator.py    # Observe → plan → act loop
│   ├── planner.py         # LLM brain (Claude/OpenAI)
│   ├── playwright_engine.py
│   ├── cdp_engine.py      # Raw Chrome DevTools Protocol
│   ├── scripted_flows.py  # Deterministic gov/travel flows
│   ├── scripted_common.py # Shared Playwright helpers
│   ├── irctc_*.py         # IRCTC-specific automation
│   └── travel_runner.py   # Background agent threads
├── pages/                 # Streamlit multipage routes
├── ui/                    # Shared components & gov prompts
├── scripts/               # CLI smoke tests
└── docs/                  # Project deck PDF
```

---

## Team — Bindas Code

| Name | Role |
|------|------|
| **Brindha Manickavasakan** | Team Lead 

---

## License

MIT — see repository for details.

**Project deck:** `docs/BindasCode_AgenticWeb_ProjectDeck.pdf`
