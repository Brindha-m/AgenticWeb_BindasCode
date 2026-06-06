"""
Government & utility quick-launch prompts for the Home page general agent.
Each entry: navigation steps, agent template, selectors, default parameters.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def fill_template(template: str, params: dict) -> str:
    out = template
    for key, value in params.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def build_user_task(item: dict, extra_params: dict | None = None) -> str:
    """Plain English shown in the Home page task box."""
    template = item.get("user_task_template", "").strip()
    if not template:
        return f"Help me with {item['title']} on {item['url']}."
    params = {**item.get("defaults", {}), **(extra_params or {})}
    return fill_template(template, params)


def build_agent_task(item: dict, extra_params: dict | None = None) -> str:
    params = {**item.get("defaults", {}), **(extra_params or {})}
    prompt = fill_template(item["prompt_template"], params)
    steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(item["navigation_steps"]))
    selectors = "\n".join(f"  - {s}" for s in item.get("selectors", []))
    tags = " ".join(item.get("tags", []))
    fallback_line = ""
    if item.get("fallback_url"):
        fallback_line = f"\nFALLBACK URL: {item['fallback_url']}"
    extra_urls = ""
    if item.get("extra_urls"):
        extra_urls = "\n" + "\n".join(f"ALT URL: {u}" for u in item["extra_urls"])
    region_ref = ""
    if item.get("region_codes_ref"):
        region_ref = f"\n\nREGION CODES (dropdown):\n{item['region_codes_ref']}"
    fallback_channels = ""
    if item.get("fallback_channels"):
        fallback_channels = f"\n\nFALLBACK CHANNELS:\n{fill_template(item['fallback_channels'], params)}"
    return f"""SERVICE: {item['title']}
PRIMARY URL: {item['url']}{fallback_line}{extra_urls}
CATEGORY: {item.get('category', 'Government')}{region_ref}{fallback_channels}

NAVIGATION STEPS (follow in order):
{steps}

DETAILED AGENT PROMPT:
{prompt}

KEY SELECTORS (try in order if click/type fails):
{selectors}

AUTOMATION RULES:
- Use ask_user for CAPTCHA, OTP, login username/password, or payment confirmation.
- Do not complete payment unless the user explicitly confirms.
- For autocomplete fields, type slowly (delay ~80ms per key), wait for dropdown, then click first matching option.
- Prefer visible labeled buttons over guessing hidden elements.
- Return structured summary JSON at the end when extraction is requested.

Tags: {tags}
"""


_TODAY = datetime.now().strftime("%d/%m/%Y")
_TODAY_PLUS_7 = (datetime.now() + timedelta(days=7)).strftime("%d/%m/%Y")

GOV_QUICK_LAUNCH: list[dict] = [
    {
        "id": "tirupati",
        "emoji": "🕌",
        "title": "Tirupati booking",
        "desc": "TTD Special Entry Darshan (Sri PAT) — ttdevasthanams.ap.gov.in",
        "url": "https://ttdevasthanams.ap.gov.in/spat/slot-booking?flow=spat&flowIdentifier=spat",
        "category": "Travel & Transport",
        "page": None,
        "navigation_steps": [
            "Open TTD Sri PAT slot booking in Chrome",
            "Login: mobile → Get OTP → SMS OTP (IRCTC-style)",
            "Select green calendar date → pick time slot (radio) → Continue",
            "Enter pilgrim details in Streamlit → auto-filled in Chrome → **Continue**",
            "Confirm amount on payment page → click **Pay Now** in Chrome",
        ],
        "prompt_template": (
            "Open TTD Special Entry Darshan slot booking at ttdevasthanams.ap.gov.in/spat. "
            'Log in with mobile OTP for "{mobile}". '
            'Select "{target_date}" on calendar. '
            'Fill "{pilgrim_count}" pilgrims; aadhaar: "{aadhaar_list}". '
            "Proceed to payment and confirm booking."
        ),
        "selectors": [
            'a[href*="darshan"], .menu-darshan',
            'button.book-now, input[value="Book Now"]',
            "#mobileNo, input[name='mobile']",
            ".date-picker td:not(.disabled)",
            "#pilgrimCount, select[name='noOfPilgrims']",
        ],
        "tags": ["#otp-login", "#date-picker", "#aadhaar", "#payment"],
        "user_task_template": (
            "Book Tirupati Special Entry Darshan (Sri PAT) for {pilgrim_count} pilgrims on {target_date} "
            "at ttdsevaonline.com. Guide me step-by-step: login OTP, slot, details, payment, confirmation."
        ),
        "defaults": {
            "darshan_type": "Special Entry Darshan (Sri PAT)",
            "mobile": "USER_MOBILE",
            "target_date": _TODAY_PLUS_7,
            "pilgrim_count": "2",
            "aadhaar_list": "ASK_USER_PER_PILGRIM",
        },
    },
    {
        "id": "passport",
        "emoji": "🛂",
        "title": "Passport slots",
        "desc": "Passport Seva — appointment",
        "url": "https://www.passportindia.gov.in",
        "category": "Identity & Docs",
        "page": None,
        "navigation_steps": [
            "Go to https://www.passportindia.gov.in",
            'Click "Existing User Login" (or register if new user)',
            'Click "Apply for Fresh Passport / Re-issue"',
            "Choose Normal or Tatkal; fill personal and address details",
            "Upload documents if required",
            "Pay and schedule appointment at nearest PSK",
            "Pick available date and time slot; download ARN receipt",
        ],
        "prompt_template": (
            "Go to https://www.passportindia.gov.in. Click Existing User Login. "
            'Enter username "{username}" and password "{password}" (ask_user if not provided). '
            'Click Login. Click "Apply for Fresh Passport / Re-issue". '
            'Select "{passport_type}" (Normal or Tatkal). Complete form pages with placeholder data or ask_user. '
            "On document upload, note required docs. On payment page use confirm before paying. "
            'Click Schedule Appointment. Select PSK "{psk_location}". '
            "Pick first available date and earliest slot. Screenshot ARN."
        ),
        "selectors": [
            "#userName, input[name='loginId']",
            "#password",
            'a[href*="applyFreshPassport"]',
            'input[name="serviceType"][value="Normal"]',
            "button#scheduleAppointment",
            ".calendar-day:not(.disabled)",
        ],
        "tags": ["#form-wizard", "#file-upload", "#appointment", "#tatkal"],
        "user_task_template": (
            "Book a {passport_type} passport appointment at passportindia.gov.in. "
            "Ask me for login, nearest PSK city, and documents. Stop before payment."
        ),
        "defaults": {
            "username": "ASK_USER",
            "password": "ASK_USER",
            "passport_type": "Normal",
            "psk_location": "ASK_USER_CITY",
            "photo_path": "user provides",
            "aadhaar_path": "user provides",
            "address_proof_path": "user provides",
        },
    },
    {
        "id": "irctc",
        "emoji": "🎫",
        "title": "Tatkal / IRCTC",
        "desc": "Opens dedicated IRCTC agent",
        "url": "https://www.irctc.co.in/nget/train-search",
        "category": "Travel & Transport",
        "page": "pages/2_irctc.py",
        "navigation_steps": [],
        "prompt_template": "",
        "selectors": [],
        "tags": [],
        "defaults": {},
    },
    {
        "id": "indiapost",
        "emoji": "📮",
        "title": "India Post",
        "desc": "Track N Trace on homepage",
        "url": "https://www.indiapost.gov.in/",
        "category": "Travel & Transport",
        "page": None,
        "navigation_steps": [
            "Open https://www.indiapost.gov.in/ (tracker is inline on homepage — no sub-page)",
            'On Track N Trace widget, click "Consignment ID" tab',
            'Click "Consignment Number" sub-tab',
            "Enter 13-character consignment number (e.g. EK403807171IN)",
            'Solve math CAPTCHA (e.g. "3 + 5 = ?") — not an image captcha',
            'Click "Search"',
            "Read status, booking details, and event history table below",
            'Optional: click "Track More" for additional numbers',
        ],
        "prompt_template": (
            "Navigate to https://www.indiapost.gov.in/ ONLY. "
            "Do NOT use /vas/pages/ or /_layouts/15/ URLs — they are dead or blocked externally. "
            "Wait up to 10s for CAPTCHA (.captcha or #captchaAnswer). "
            'On Track N Trace: click Consignment ID tab, then Consignment Number sub-tab. '
            'Enter "{consignment_number}" (13-char e.g. EK403807171IN). '
            "Read math CAPTCHA text (e.g. 3 + 4 =), compute answer, type in captcha field. "
            "Click Search. Extract JSON: consignment_number, article_type, booked_at, booked_on, "
            "destination_pincode, tariff, delivery_location, delivery_confirmed_on, current_status, "
            "events with date, time, office, event. "
            "If homepage widget fails to render: tell user SMS fallback POST TRACK {consignment_number} to 166 or 51969."
        ),
        "selectors": [
            '.track-tab, li[data-tab="consignment"], a:has-text("Consignment ID")',
            '.track-subtab, a:has-text("Consignment Number")',
            'input[name="conNo"], input[id*="consignment" i], input[placeholder*="Consignment" i]',
            ".captcha-question, span#captchaQuestion, label:has-text('='), #captchaText",
            'input[name="captchaAnswer"], input[id*="captcha" i], input#captchaInput',
            'button#btnSearch, input[value="Search"], button:has-text("Search")',
            ".current-status, span:has-text('Current Status'), td.statusText",
            'table.track-result, .event-details-table, table:has(th:has-text("Event"))',
            'a:has-text("Track More"), a[href*="trackmore"]',
        ],
        "fallback_channels": (
            "1. Homepage widget: https://www.indiapost.gov.in/\n"
            "2. SMS: POST TRACK {consignment_number} (uppercase) to 166 or 51969\n"
            "3. Dak Sewa 2.0 app (Play Store / App Store — replaced Postinfo)\n"
            "4. Helpline: 1800-266-6868 (Mon–Sat 9AM–6PM); IVRS 24/7"
        ),
        "tags": [
            "#homepage-widget",
            "#math-captcha",
            "#json-output",
            "#sms-fallback",
            "#dak-sewa",
            "#no-subpage",
        ],
        "user_task_template": (
            "Track my India Post parcel on indiapost.gov.in (use the homepage Track N Trace widget). "
            "Ask me for my 13-character consignment number (e.g. EK403807171IN). "
            "Solve the simple math captcha on the page."
        ),
        "defaults": {
            "consignment_number": "ASK_USER",
        },
    },
    {
        "id": "tneb",
        "emoji": "💡",
        "title": "EB bill",
        "desc": "TNPDCL Quick Pay — tnebnet.org",
        "url": "https://www.tnebnet.org/qwp/qpay",
        "fallback_url": "http://tneb.tnebnet.org/newlt/consbillstatus.html",
        "extra_urls": [
            "https://www.tnebnet.org/awp/login",
            "https://www.tnpdcl.org",
        ],
        "category": "Utilities",
        "page": None,
        "navigation_steps": [
            "Open https://www.tnebnet.org/qwp/qpay (TNPDCL Quick Pay — no login)",
            "Enter Consumer Number or Acknowledgement Number (#userName)",
            "Read image CAPTCHA (#CaptchaImgID) — user types answer in structured form",
            "Click Submit and view bill (amount, due date, consumer name)",
            "Optional: confirm payment → UPI/Card/NetBanking in Chrome (human completes pay)",
        ],
        "prompt_template": (
            "Navigate to https://www.tnebnet.org/qwp/qpay. Wait for Quick Pay form. "
            'Enter consumer or acknowledgement number "{consumer_number}". '
            "Image CAPTCHA on #CaptchaImgID — ask_user via [CAPTCHA] tag; retry once on error. "
            'Click Submit. Extract: consumer_name, bill_amount, due_date, units_consumed. '
            'If user confirms pay: payment_mode "{payment_mode}", UPI "{upi_id}". '
            "Do NOT use dead domains tnebltd.gov.in or tangedco.gov.in."
        ),
        "selectors": [
            "#userName, input[name=j_username]",
            "#CaptchaImgID, img[src*='simpleCaptcha']",
            "#CaptchaID, input[name=CaptchaID]",
            'button:has-text("Submit")',
            'a:has-text("Refresh")',
            ".bill-details, #billInfo, table.bill-table",
            "td.bill-amount, span.amount, .totalAmount",
            "td.due-date, .dueDate",
            'button:has-text("Pay"), a:has-text("Pay Now")',
        ],
        "region_codes_ref": (
            "CH=Chennai, CB=Coimbatore, MDU=Madurai, TR=Trichy, SLM=Salem, "
            "VLR=Vellore, TEN=Tirunelveli, ERD=Erode, TNJ=Thanjavur"
        ),
        "tags": [
            "#tnpdcl",
            "#tnebnet",
            "#region-code",
            "#captcha",
            "#quick-pay",
            "#no-login",
            "#bbps-fallback",
        ],
        "user_task_template": (
            "View my Tamil Nadu electricity (TNPDCL) bill on tnebnet.org Quick Pay. "
            "Ask for Consumer Number or Acknowledgement Number and the image CAPTCHA. "
            "Show amount due and due date. Do not pay unless I confirm."
        ),
        "defaults": {
            "consumer_number": "ASK_USER",
            "payment_mode": "UPI",
            "upi_id": "ASK_USER",
            "pay_bill": "ASK_USER",
        },
    },
    {
        "id": "fastag",
        "emoji": "🚗",
        "title": "FASTag",
        "desc": "Balance & recharge",
        "url": "https://www.onefastag.com",
        "category": "Utilities",
        "page": None,
        "navigation_steps": [
            "Open FASTag issuer portal",
            "Login with mobile OTP",
            "Check wallet balance for vehicle",
            "Recharge if requested (confirm before pay)",
            "Verify updated balance",
        ],
        "prompt_template": (
            'Navigate to "{fastag_portal_url}". Click Login or Check Balance. '
            'Enter mobile "{mobile}"; ask_user for OTP. '
            'Find FASTag for vehicle RC "{vehicle_rc}". Read balance. '
            'To recharge: amount "{recharge_amount}", payment "{payment_mode}". '
            "Confirm before payment. Screenshot new balance."
        ),
        "selectors": [
            "input[name*='mobile' i], #mobileNo",
            ".balance-display, .wallet-balance",
            "button.recharge-btn",
            "input#rechargeAmount",
        ],
        "tags": ["#otp-login", "#balance-read", "#recharge"],
        "user_task_template": (
            "Check FASTag balance for my vehicle and recharge Rs {recharge_amount} if needed. "
            "Ask me for mobile OTP and vehicle details. Confirm before payment."
        ),
        "defaults": {
            "fastag_portal_url": "https://www.onefastag.com",
            "mobile": "ASK_USER",
            "vehicle_rc": "ASK_USER",
            "recharge_amount": "500",
            "payment_mode": "UPI",
        },
    },
    {
        "id": "pan_gst_lpg",
        "emoji": "📋",
        "title": "PAN / GST / LPG",
        "desc": "Income tax · GST · Indane",
        "url": "https://eportal.incometax.gov.in",
        "category": "Finance & Tax",
        "page": None,
        "navigation_steps": [
            "Income Tax: login at eportal.incometax.gov.in",
            "Check ITR / refund status under e-File",
            "GST: login at gst.gov.in — returns / notices",
            "Indane: cx.indianoil.in — LPG booking status",
        ],
        "prompt_template": (
            "[INCOME TAX] Navigate to https://eportal.incometax.gov.in. Login PAN '{pan_number}' "
            "password ask_user. Check e-File → ITR for assessment year '{assessment_year}'. "
            "Extract ITR/refund status. "
            "[GST] Navigate to https://www.gst.gov.in. Login GSTIN '{gstin}' password ask_user. "
            "CAPTCHA ask_user. Track GSTR-1/GSTR-3B for FY '{fy}'. "
            "[INDANE] Navigate to https://cx.indianoil.in. LPG ID or mobile '{lpg_id}'. "
            "Book cylinder if user confirms. Return JSON summary per portal."
        ),
        "selectors": [
            "input#user-id",
            "input#password",
            "input#gstin",
            "input#lpgId",
        ],
        "tags": ["#pan", "#gst", "#lpg", "#captcha", "#multi-portal"],
        "user_task_template": (
            "Check my Income Tax return status (AY {assessment_year}), GST filings (FY {fy}), "
            "and Indane LPG cylinder booking on the official government sites. Ask for login when needed."
        ),
        "defaults": {
            "pan_number": "ASK_USER",
            "assessment_year": "2025-26",
            "gstin": "ASK_USER",
            "fy": "2024-25",
            "lpg_id": "ASK_USER",
            "it_password": "ASK_USER",
            "gst_password": "ASK_USER",
        },
    },
    {
        "id": "exam",
        "emoji": "📚",
        "title": "Exam results",
        "desc": "results.nic.in · CBSE",
        "url": "https://results.nic.in",
        "category": "Exam Results",
        "page": None,
        "navigation_steps": [
            "Open https://results.nic.in and find exam link",
            "Or use https://cbseresults.nic.in for CBSE",
            "Enter roll number, school number, DOB",
            "Solve captcha via ask_user if shown",
            "Extract marks table; download marksheet if available",
        ],
        "prompt_template": (
            "[NIC] Navigate to https://results.nic.in. Find link for exam '{exam_name}' "
            "(use text match if needed). Enter roll '{roll_number}', school '{school_number}', "
            "DOB '{dob}'. Captcha ask_user. Submit. Extract name, subjects, marks, total, pass/fail as JSON. "
            "[CBSE] Or open https://cbseresults.nic.in → '{class_level}' result with same fields."
        ),
        "selectors": [
            "select[name='state']",
            'a:has-text("CBSE")',
            "input[name='rollno'], #rollNumber",
            "input[name='schoolno']",
            "button[type='submit']",
            "table.result-table",
        ],
        "tags": ["#roll-number", "#json-output", "#marksheet", "#cbse"],
        "user_task_template": (
            "Check my {exam_name} exam results on results.nic.in or cbseresults.nic.in. "
            "Ask me for roll number, school number, and date of birth."
        ),
        "defaults": {
            "exam_name": "CBSE Class 12",
            "roll_number": "ASK_USER",
            "school_number": "ASK_USER",
            "dob": "ASK_USER_DD_MM_YYYY",
            "class_level": "Class 12",
            "admit_id": "ASK_USER_IF_REQUIRED",
        },
    },
    {
        "id": "state_transport",
        "emoji": "🛣️",
        "title": "State Transport",
        "desc": "TNSTC · KSRTC · APSRTC — government bus booking",
        "url": "https://www.tnstc.in/OTRSOnline/",
        "category": "Travel & Transport",
        "page": None,
        "navigation_steps": [
            "Open STU portal (TNSTC OTRS / KSRTC / APSRTC)",
            "Fill Source, Destination, and Onward date (scripted — no API key)",
            "Search buses and select an available service",
            "Select seats and enter passenger details",
            "Login OTP / CAPTCHA if required — stop before payment unless you confirm",
        ],
        "prompt_template": (
            "On {portal_label} ({url}), search government buses from {origin} to {destination} "
            "on {journey_date} for {passengers} passenger(s). "
            "Use the official online reservation flow. Select service, seats, and passenger form. "
            "Do not complete payment unless the user confirms."
        ),
        "selectors": [
            "#matchStartPlace",
            "#matchEndPlace",
            "#txtdeptDateOtrip",
            "#searchButton",
            'button:has-text("Search Bus")',
            "#txtUserLoginID",
            "#txtCaptchaCode",
        ],
        "tags": ["#bus", "#tnstc", "#stu", "#otp", "#captcha"],
        "user_task_template": (
            "Book a {portal_label} government bus from {origin} to {destination} on {journey_date} "
            "for {passengers} passenger(s). Use scripted automation on the official STU portal."
        ),
        "defaults": {
            "origin": "Coimbatore",
            "destination": "Chennai",
            "journey_date": _TODAY,
            "passengers": "1",
            "portal_label": "TNSTC",
            "budget": "",
        },
    },
]

# Dedicated Streamlit page (IRCTC-style live log + browser view) for all gov quick launches except IRCTC.
GOV_STREAMLIT_PAGE = "pages/6_government.py"

# Re-export — all gov hub services support scripted mode in the UI.
from agent.scripted_flows import SCRIPTED_GOV_IDS  # noqa: E402

for _gov_item in GOV_QUICK_LAUNCH:
    if _gov_item["id"] != "irctc":
        _gov_item["page"] = GOV_STREAMLIT_PAGE


def gov_items_for_streamlit() -> list[dict]:
    """Services shown on the Government Services page (excludes dedicated IRCTC page)."""
    return [g for g in GOV_QUICK_LAUNCH if g["id"] != "irctc"]


def resolve_agent_task(user_text: str, session: dict) -> str:
    """
    Use the hidden full agent prompt when the user has not edited the
    quick-launch text; otherwise run with whatever they typed.
    """
    user_text = (user_text or "").strip()
    internal = session.get("_ql_internal_task")
    snapshot = (session.get("_ql_user_task_snapshot") or "").strip()
    if internal and snapshot and user_text == snapshot:
        return internal
    return user_text


def quick_launch_for_ui() -> list[dict]:
    """Items for Home page buttons: dedicated pages or plain-English task for Home agent."""
    out = []
    for item in GOV_QUICK_LAUNCH:
        dedicated = item.get("page")
        if dedicated == "pages/2_irctc.py":
            out.append(
                {
                    "emoji": item["emoji"],
                    "title": item["title"],
                    "desc": item["desc"],
                    "page": dedicated,
                    "gov_id": None,
                    "task": "",
                    "agent_task": "",
                    "id": item["id"],
                }
            )
        elif dedicated:
            out.append(
                {
                    "emoji": item["emoji"],
                    "title": item["title"],
                    "desc": item["desc"],
                    "page": dedicated,
                    "gov_id": item["id"],
                    "task": build_user_task(item),
                    "agent_task": build_agent_task(item),
                    "id": item["id"],
                }
            )
        else:
            out.append(
                {
                    "emoji": item["emoji"],
                    "title": item["title"],
                    "desc": item["desc"],
                    "page": None,
                    "gov_id": None,
                    "task": build_user_task(item),
                    "agent_task": build_agent_task(item),
                    "id": item["id"],
                }
            )
    return out


def get_prompt_by_id(prompt_id: str) -> dict | None:
    for item in GOV_QUICK_LAUNCH:
        if item["id"] == prompt_id:
            return item
    return None
