"""Bus travel booking — RedBus, AbhiBus."""

import streamlit as st
from dotenv import load_dotenv

from ui.bus_page import render_bus_page

load_dotenv()

st.set_page_config(page_title="Bus Travel Booking", page_icon="🚌", layout="wide")

PORTALS = [
    ("RedBus", "https://www.redbus.in"),
    ("AbhiBus", "https://www.abhibus.com"),
]

PROGRESS = [
    ("launch", "🚀 Launch browser"),
    ("open_portal", "🌐 Open bus portal"),
    ("search", "🔍 Search route & date"),
    ("select", "🚌 Select bus"),
    ("seats", "💺 Select seats (you pick in Chrome)"),
    ("board_drop", "📍 Board / drop points"),
    ("passengers", "👤 Passenger info"),
    ("payment", "💳 Payment"),
    ("done", "✅ Ticket confirmed"),
]


def build_task(origin, destination, date, passengers, budget, portal, **kwargs) -> str:
    budget_line = f" Keep total fare under ₹{budget}." if budget else ""
    bus_type = kwargs.get("bus_type", "any")
    preferred = kwargs.get("preferred_bus", "")
    type_line = f" Prefer **{bus_type}** buses." if bus_type and bus_type != "any" else ""
    name_line = f" Prefer operator/bus name containing **{preferred}**." if preferred else ""
    return (
        f"On {portal}, search buses from {origin} to {destination} "
        f"on {date} for {passengers} passenger(s).{budget_line}{type_line}{name_line} "
        "Select a matching bus and proceed until payment handoff."
    )


render_bus_page(
    page_id="bus",
    prefix="bus",
    title="🚌 Bus Travel Booking",
    subtitle=(
        "Fill **trip**, **bus preference**, and **passenger details** below, then Start. "
        "The agent fills RedBus in Chrome; you only pick **seats** and **pay**."
    ),
    category_pill="BUS TRAVEL BOOKING",
    portal_options=PORTALS,
    default_portal="RedBus",
    progress_steps=PROGRESS,
    build_task=build_task,
    default_from="Coimbatore",
    default_to="Bangalore",
    tips_md="""
- **Passenger details** and **bus type** are taken from the form above — fill them before Start.
- RedBus flow in Chrome: **Select seats** → **Board/Drop point** → **Passenger Info**.
- Pick green seat(s), click **✅ Seats picked** — agent clicks Continue to step 2.
- **Boarding** and **dropping** points come from the form above — agent selects them in Chrome automatically.
- Payment (UPI OTP) is completed in Chrome.
""",
)
