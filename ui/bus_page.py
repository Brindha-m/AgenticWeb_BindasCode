"""Bus travel booking page — extended form (passengers + bus prefs) before agent starts."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

import streamlit as st

from ui.travel_page import render_travel_category_page


def _default_contact(field: str, fallback: str = "") -> str:
    env_map = {
        "name": ("BUS_CONTACT_NAME", "IRCTC_P1_NAME"),
        "email": ("BUS_CONTACT_EMAIL", ""),
        "mobile": ("BUS_CONTACT_MOBILE", "IRCTC_MOBILE"),
        "state": ("BUS_CONTACT_STATE", ""),
    }
    keys = env_map.get(field, ("",))
    for k in keys:
        if k and os.getenv(k, "").strip():
            return os.getenv(k, "").strip()
    return fallback


def render_bus_page(
    *,
    page_id: str,
    prefix: str,
    title: str,
    subtitle: str,
    category_pill: str,
    portal_options: list[tuple[str, str]],
    default_portal: str,
    progress_steps: list[tuple[str, str]],
    build_task: Callable[..., str],
    tips_md: str,
    default_from: str = "Coimbatore",
    default_to: str = "Bangalore",
) -> None:
    """Bus page with passenger + bus preference fields collected before Start."""

    def extra_form() -> dict:
        st.markdown("### 🚌 Bus preference")
        bc1, bc2 = st.columns(2)
        with bc1:
            preferred_bus = st.text_input(
                "Preferred bus / operator (optional)",
                placeholder="e.g. SRS, KPN, Orange Tours",
                key=f"{prefix}_preferred_bus",
            )
        with bc2:
            bus_type = st.selectbox(
                "Bus type",
                options=["Any", "Sleeper", "Seater"],
                index=0,
                key=f"{prefix}_bus_type",
            )

        st.markdown("### 📍 Boarding & dropping points *(required — agent selects these in Chrome)*")
        bd1, bd2 = st.columns(2)
        with bd1:
            boarding_point = st.text_input(
                "Boarding point",
                placeholder="e.g. Gandhipuram or 22:30, Gandhipuram",
                key=f"{prefix}_boarding",
            )
        with bd2:
            dropping_point = st.text_input(
                "Dropping point",
                placeholder="e.g. Majestic or 05:50, Bangalore",
                key=f"{prefix}_dropping",
            )
        st.caption(
            "Use the **location name** and/or **time** shown on RedBus step 2. "
            "The agent picks matching points automatically — you do not select them in Chrome."
        )

        st.markdown("### 👤 Passenger details *(required before booking)*")
        st.caption(
            "Used for RedBus **login OTP** and passenger form in Chrome — enter here before Start. "
            "Ignore Passkey / Face ID on RedBus; the agent uses **mobile + OTP** only."
        )

        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            contact_name = st.text_input(
                "Contact name",
                value=_default_contact("name"),
                key=f"{prefix}_contact_name",
            )
        with pc2:
            contact_email = st.text_input(
                "Email",
                value=_default_contact("email"),
                key=f"{prefix}_contact_email",
            )
        with pc3:
            contact_mobile = st.text_input(
                "Mobile (10 digits)",
                value=_default_contact("mobile"),
                key=f"{prefix}_contact_mobile",
            )
        _indian_states = [
            "Andhra Pradesh",
            "Arunachal Pradesh",
            "Assam",
            "Bihar",
            "Chhattisgarh",
            "Goa",
            "Gujarat",
            "Haryana",
            "Himachal Pradesh",
            "Jharkhand",
            "Karnataka",
            "Kerala",
            "Madhya Pradesh",
            "Maharashtra",
            "Manipur",
            "Meghalaya",
            "Mizoram",
            "Nagaland",
            "Odisha",
            "Punjab",
            "Rajasthan",
            "Sikkim",
            "Tamil Nadu",
            "Telangana",
            "Tripura",
            "Uttar Pradesh",
            "Uttarakhand",
            "West Bengal",
            "Delhi",
            "Jammu and Kashmir",
            "Ladakh",
            "Puducherry",
        ]
        _state_default = _default_contact("state") or "Tamil Nadu"
        _state_index = (
            _indian_states.index(_state_default)
            if _state_default in _indian_states
            else _indian_states.index("Tamil Nadu")
        )
        contact_state = st.selectbox(
            "State of residence (GST)",
            options=_indian_states,
            index=_state_index,
            key=f"{prefix}_contact_state",
            help="Required on RedBus passenger step for GST invoicing.",
        )

        passengers_detail: list[dict[str, str]] = []
        pax_count = st.session_state.get(f"{prefix}_pax", 1)
        for i in range(int(pax_count)):
            st.markdown(f"**Passenger {i + 1}**")
            r1, r2, r3 = st.columns(3)
            with r1:
                pname = st.text_input(
                    "Full name",
                    value=_default_contact("name") if i == 0 else "",
                    key=f"{prefix}_p{i}_name",
                )
            with r2:
                page = st.text_input(
                    "Age",
                    value=os.getenv(f"BUS_P{i + 1}_AGE", os.getenv("IRCTC_P1_AGE", "30")),
                    key=f"{prefix}_p{i}_age",
                )
            with r3:
                pgender = st.selectbox(
                    "Gender",
                    ["Male", "Female"],
                    index=1 if os.getenv(f"BUS_P{i + 1}_GENDER", os.getenv("IRCTC_P1_GENDER", "Male")).lower().startswith("f") else 0,
                    key=f"{prefix}_p{i}_gender",
                )
            passengers_detail.append({"name": pname.strip(), "age": page.strip(), "gender": pgender})

        return {
            "preferred_bus": preferred_bus.strip(),
            "bus_type": bus_type.lower(),
            "boarding_point": boarding_point.strip(),
            "dropping_point": dropping_point.strip(),
            "contact_name": contact_name.strip(),
            "contact_email": contact_email.strip(),
            "contact_mobile": contact_mobile.strip(),
            "contact_state": (
                os.getenv("BUS_CONTACT_STATE", "").strip() or contact_state.strip()
            ),
            "passengers_detail": passengers_detail,
        }

    def validate_and_merge(base_params: dict, extra: dict) -> dict | None:
        if not extra["contact_name"]:
            st.error("Enter **contact name** before starting.")
            return None
        if not extra["contact_email"] or "@" not in extra["contact_email"]:
            st.error("Enter a valid **email** before starting.")
            return None
        mobile = extra["contact_mobile"]
        if not mobile or len(mobile.replace(" ", "")) < 10:
            st.error("Enter a valid **10-digit mobile** before starting.")
            return None
        if not extra.get("boarding_point"):
            st.error("Enter **boarding point** (location or time + location) before starting.")
            return None
        if not extra.get("dropping_point"):
            st.error("Enter **dropping point** (location or time + location) before starting.")
            return None
        for i, p in enumerate(extra["passengers_detail"], 1):
            if not p.get("name"):
                st.error(f"Enter **name** for passenger {i}.")
                return None
            if not p.get("age") or not p["age"].isdigit():
                st.error(f"Enter valid **age** for passenger {i}.")
                return None
        return {**base_params, **extra}

    render_travel_category_page(
        page_id=page_id,
        prefix=prefix,
        title=title,
        subtitle=subtitle,
        category_pill=category_pill,
        portal_options=portal_options,
        default_portal=default_portal,
        progress_steps=progress_steps,
        build_task=build_task,
        tips_md=tips_md,
        default_from=default_from,
        default_to=default_to,
        extra_form=extra_form,
        validate_params=validate_and_merge,
    )
