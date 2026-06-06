"""Government & utility services — live agent UI (Tirupati, Passport, India Post, etc.)."""

import streamlit as st
from dotenv import load_dotenv

from ui.gov_service_page import render_gov_service_hub

load_dotenv()

st.set_page_config(page_title="Government Services", page_icon="🏛️", layout="wide")

initial = st.session_state.get("gov_selected_id")
render_gov_service_hub(initial_service_id=initial)
