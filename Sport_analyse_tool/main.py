import streamlit as st

from sports import korfball

st.set_page_config(
    page_title="Sport Analyse",
    page_icon=":bar_chart:",
    layout="wide"
)

st.title(":bar_chart: Sport Analyse")
st.write("Analyse games and performance of your favorite sports.")

sport = st.selectbox(
    "Select a sport:",
    ["Korfball", "Soccer", "Basketball", "Volleyball", "Handball"]
)

if sport == "Korfball":
    korfball.show()

else:
    st.info(f"{sport} is not yet implemented.")