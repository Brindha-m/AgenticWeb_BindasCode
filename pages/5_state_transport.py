"""Redirect legacy State Transport page → Government Services hub."""

import streamlit as st

st.session_state.gov_selected_id = "state_transport"
st.switch_page("pages/6_government.py")
