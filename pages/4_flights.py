"""Flights — MakeMyTrip, Goibibo, IndiGo."""

import streamlit as st
from dotenv import load_dotenv

from ui.travel_page import render_travel_category_page

load_dotenv()

st.set_page_config(page_title="Flights", page_icon="✈️", layout="wide")

PORTALS = [
    ("MakeMyTrip", "https://www.makemytrip.com/flights/"),
    ("Goibibo", "https://www.goibibo.com/flights/"),
    ("IndiGo", "https://www.goindigo.in/"),
]

PROGRESS = [
    ("launch", "🚀 Launch browser"),
    ("open_portal", "🌐 Open flight portal"),
    ("search", "🔍 Search flights"),
    ("select", "✈️ Select flight"),
    ("checkout", "👤 Traveller details"),
    ("done", "✅ Summary / payment handoff"),
]


def build_task(origin, destination, date, passengers, budget, portal) -> str:
    budget_line = f" Target fare under ₹{budget} total." if budget else ""
    return (
        f"On {portal}, search one-way flights from {origin} to {destination} "
        f"on {date} for {passengers} adult(s).{budget_line} "
        "Pick economy unless user budget is very low. "
        "Show cheapest reasonable options and proceed to traveller details. "
        "Do not pay — report best 2–3 flights with price and time."
    )


render_travel_category_page(
    page_id="flights",
    prefix="flights",
    title="✈️ Flights Agent",
    subtitle="Compare and book flights on **MakeMyTrip**, **Goibibo**, or **IndiGo**.",
    category_pill="FLIGHTS",
    portal_options=PORTALS,
    default_portal="MakeMyTrip",
    progress_steps=PROGRESS,
    build_task=build_task,
    default_from="Coimbatore",
    default_to="Bangalore",
    tips_md="""
- Flight sites often need login for best fares — answer prompts in the yellow box.
- For **tonight** trips, mention departure after current time in the From/To cities.
- Agent compares options; you confirm before payment.
""",
)
