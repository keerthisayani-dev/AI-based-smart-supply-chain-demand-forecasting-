import sys
from datetime import date, timedelta

sys.dont_write_bytecode = True

import pandas as pd
import requests
try:
    import streamlit as st
except ModuleNotFoundError:
    print("Missing dependency: streamlit. Install it to run the dashboard.")
    raise SystemExit(1)
try:
    import plotly.graph_objects as go
except ModuleNotFoundError:
    go = None

st.set_page_config(page_title="Supply Chain Dashboard", layout="wide")
st.title("DemandIQ Smart Supply Chain Demand Forecasting")
st.caption("Uses Backend/forecast_api.py endpoints to display city-level or category-level demand forecasts.")

if go is None:
    st.error("Missing dependency: plotly. Install it to view charts in the dashboard.")
    st.stop()


def _get_json(url: str, timeout: int = 15):
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=60)
def load_categories(api_base: str):
    data = _get_json(f"{api_base}/categories")
    categories = data.get("categories", [])
    cities = data.get("cities", [])
    options = [*categories, *cities]
    cleaned_options = sorted(set([str(x).strip() for x in options if str(x).strip()]))
    return cleaned_options


@st.cache_data(ttl=30)
def load_forecast(
    api_base: str,
    category: str,
    horizon: int,
    lookback: int,
    anchor_date: str,
):
    url = (
        f"{api_base}/forecast/{category}"
        f"?horizon={horizon}&history_lookback_days={lookback}&anchor_date={anchor_date}"
    )
    return _get_json(url)


with st.sidebar:
    st.header("Settings")
    api_base = st.text_input("API Base URL", value="http://127.0.0.1:8000").strip().rstrip("/")
    horizon = st.slider("Forecast Horizon (days)", min_value=1, max_value=60, value=14)
    from_date = st.date_input("From Date", value=date.today() - timedelta(days=90))
    to_date = st.date_input("To Date", value=date.today())

try:
    health = _get_json(f"{api_base}/health")
    st.success(f"Backend connected: {health.get('status', 'ok')} | {health.get('model_info', '')}")
except Exception as exc:
    st.error(f"Could not connect to backend at {api_base}. Details: {exc}")
    st.stop()

try:
    category_options = load_categories(api_base)
except Exception as exc:
    st.error(f"Failed to load categories: {exc}")
    st.stop()

if not category_options:
    st.warning("No cities or categories found in dataset.")
    st.stop()

if from_date > to_date:
    st.error("From Date must be earlier than or equal to To Date.")
    st.stop()

category_with_default = ["Select city or category"] + category_options
category = st.selectbox("Choose City or Category", options=category_with_default, index=0)

if st.button("Generate Forecast", type="primary"):
    if category == "Select city or category":
        st.warning("Please select a city or category before generating the forecast.")
        st.stop()

    lookback = max(30, (to_date - from_date).days + 1)
    try:
        payload = load_forecast(
            api_base,
            category,
            horizon,
            lookback,
            to_date.isoformat(),
        )
    except Exception as exc:
        st.error(f"Failed to fetch forecast: {exc}")
        st.stop()

    history = payload.get("history", [])
    forecast = payload.get("forecast", [])

    history_df = pd.DataFrame(history)
    forecast_df = pd.DataFrame(forecast)

    if not history_df.empty:
        history_df["date"] = pd.to_datetime(history_df["date"], errors="coerce")
        history_df = history_df[
            (history_df["date"] >= pd.to_datetime(from_date))
            & (history_df["date"] <= pd.to_datetime(to_date))
        ]
    if not forecast_df.empty:
        forecast_df["date"] = pd.to_datetime(forecast_df["date"], errors="coerce")

    c1, c2, c3 = st.columns(3)
    total_fc = float(forecast_df["forecast_units_sold"].sum()) if "forecast_units_sold" in forecast_df else 0.0
    avg_fc = float(forecast_df["forecast_units_sold"].mean()) if "forecast_units_sold" in forecast_df else 0.0
    last_actual = (
        float(history_df["actual_units_sold"].iloc[-1])
        if (not history_df.empty and "actual_units_sold" in history_df)
        else 0.0
    )

    c1.metric("Total Forecast Units", f"{total_fc:,.1f}")
    c2.metric("Average Daily Forecast", f"{avg_fc:,.2f}")
    c3.metric("Latest Actual Units", f"{last_actual:,.1f}")

    fig = go.Figure()
    if not history_df.empty and {"date", "actual_units_sold"}.issubset(history_df.columns):
        fig.add_trace(
            go.Scatter(
                x=history_df["date"],
                y=history_df["actual_units_sold"],
                mode="lines+markers",
                name="Actual",
            )
        )
    if not forecast_df.empty and {"date", "forecast_units_sold"}.issubset(forecast_df.columns):
        fig.add_trace(
            go.Scatter(
                x=forecast_df["date"],
                y=forecast_df["forecast_units_sold"],
                mode="lines+markers",
                name="Forecast",
            )
        )

    fig.update_layout(
        title=f"Demand Trend and Forecast: {category}",
        xaxis_title="Date",
        yaxis_title="Units Sold",
        legend_title="Series",
        template="plotly_white",
    )

    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Recent History")
        if history_df.empty:
            st.info("No history returned.")
        else:
            st.dataframe(history_df.sort_values("date", ascending=False), use_container_width=True)
    with right:
        st.subheader("Forecast Output")
        if forecast_df.empty:
            st.info("No forecast returned.")
        else:
            st.dataframe(forecast_df, use_container_width=True)
else:
    st.info("Select a city or category and click 'Generate Forecast'.")
