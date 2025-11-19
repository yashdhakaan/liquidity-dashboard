import streamlit as st
import pandas as pd
from fredapi import Fred
import yfinance as yf
import plotly.graph_objects as go

# --- PAGE CONFIG ---
st.set_page_config(page_title="Global Liquidity & Bitcoin", layout="wide")
st.title("Global Liquidity vs. Bitcoin")
st.markdown("### The 'Fiscal Dominance' Dashboard (with Lag Fixes)")

# --- API KEY MANAGEMENT ---
# 1. Try to get key from Streamlit Secrets
try:
    api_key = st.secrets["FRED_API_KEY"]
except:
    api_key = None

# 2. If no secret found, ask in sidebar
if not api_key:
    with st.sidebar:
        st.warning("⚠️ FRED API Key Missing in Secrets")
        api_key = st.text_input("Enter FRED API Key", type="password")
        st.markdown("[Get free Key](https://research.stlouisfed.org/useraccount/apikeys)")

if not api_key:
    st.info("Please enter a FRED API Key to load the chart.")
    st.stop()

fred = Fred(api_key=api_key)

# --- SETTINGS SIDEBAR ---
with st.sidebar:
    st.header("Chart Settings")
    lookback_years = st.slider("Timeframe (Years)", 3, 15, 8)
    log_scale = st.checkbox("Log Scale (Bitcoin)", value=True)
    st.markdown("---")
    st.markdown("**Metric Guide:**")
    st.markdown("⬜ **Global M2:** Total Cash Supply")
    st.markdown("🟥 **CB Assets:** Central Bank Balance Sheets")
    st.markdown("🟧 **Bitcoin:** Price in USD")

# --- DATA ENGINE ---
@st.cache_data(ttl=43200) # Cache for 12 hours
def get_liquidity_data(years):
    start_date = pd.Timestamp.now() - pd.DateOffset(years=years)
    start_str = start_date.strftime('%Y-%m-%d')

    # 1. FETCH MARKET DATA (YFinance)
    tickers = ["EURUSD=X", "JPY=X", "CNY=X", "BTC-USD"] 
    market_data = yf.download(tickers, start=start_str, progress=False)['Close']
    # Use mean() for smoothing and consistency with monthly data
    market_monthly = market_data.resample('M').mean() 

    # 2. FETCH MACRO DATA (FRED)
    try:
        # M2 Supply (Billions or Local Currency)
        m2_us = fred.get_series('M2SL', observation_start=start_str)
        m2_eu = fred.get_series('MANMM101EZM189S', observation_start=start_str)
        m2_jp = fred.get_series('MANMM101JPM189S', observation_start=start_str)
        m2_cn = fred.get_series('MANMM101CNM189S', observation_start=start_str)
        
        # Central Bank Assets (Millions or Local Units)
        cb_fed = fred.get_series('WALCL', observation_start=start_str)
        cb_ecb = fred.get_series('ECBASSETSW', observation_start=start_str)
        cb_boj = fred.get_series('JPNASSETS', observation_start=start_str)
    except Exception as e:
        # st.error(f"Error fetching FRED Data: {e}") # Suppress error to avoid clutter
        return None

    # 3. NORMALIZE TO USD TRILLIONS
    df = pd.DataFrame(index=m2_us.index)
    
    # Currencies aligned to monthly data (ffill handles FX data gaps)
    fx_eu = market_monthly['EURUSD=X'].reindex(df.index, method='ffill')
    fx_jp = market_monthly['JPY=X'].reindex(df.index, method='ffill')
    fx_cn = market_monthly['CNY=X'].reindex(df.index, method='ffill')

    # --- GLOBAL M2 CALCULATION (WHITE LINE) ---
    # CRITICAL FIX: Add .ffill() to each component to carry the last known value forward
    us_val = (m2_us / 1000).ffill() 
    eu_val = ((m2_eu * fx_eu) / 1000).ffill()
    jp_val = ((m2_jp / fx_jp) / 1000).ffill() 
    cn_val = ((m2_cn / fx_cn) / 1000).ffill() 

    # Summing the forward-filled components
    df['Global_M2'] = us_val.fillna(0) + eu_val.fillna(0) + jp_val.fillna(0) + cn_val.fillna(0)

    # --- CB ASSETS CALCULATION (RED LINE) ---
    # CRITICAL FIX: Add .ffill() to each component
    fed_assets = (cb_fed.resample('M').ffill() / 1_000_000).ffill() 
    ecb_assets = ((cb_ecb.resample('M').ffill() * fx_eu) / 1_000_000).ffill()
    boj_assets = ((cb_boj.resample('M').ffill() * 0.0001) / fx_jp).ffill() 

    # Summing the forward-filled components
    df['Global_Assets'] = fed_assets.fillna(0) + ecb_assets.fillna(0) + boj_assets.fillna(0)
    
    # --- BITCOIN DATA ---
    df['BTC'] = market_monthly['BTC-USD'].reindex(df.index, method='ffill')

    # FINAL CLEANUP: Only drop rows where both M2 and Assets are missing (very rare)
    return df.dropna(subset=['Global_M2', 'Global_Assets'])

# --- RENDER CHART ---
st.write(f"Fetching live data for the last {lookback_years} years...")

# Use try/except in the main render loop to catch potential errors from data engine
try:
    df = get_liquidity_data(lookback_years)

    if df is not None:
        fig = go.Figure()

        # Trace 1: M2 (White) - Left Axis
        fig.add_trace(go.Scatter(
            x=df.index, y=df['Global_M2'], name="Global M2 ($T)",
            line=dict(color='white', width=2), yaxis="y1"
        ))

        # Trace 2: Assets (Red) - Right Axis 1
        fig.add_trace(go.Scatter(
            x=df.index, y=df['Global_Assets'], name="CB Assets ($T)",
            line=dict(color='#ff4b4b', width=2), yaxis="y2"
        ))

        # Trace 3: Bitcoin (Orange) - Right Axis 2
        fig.add_trace(go.Scatter(
            x=df.index, y=df['BTC'], name="Bitcoin ($)",
            line=dict(color='#ffa500', width=2), yaxis="y3"
        ))

        # Complex Layout for 3 Axes
        fig.update_layout(
            template="plotly_dark", height=600, hovermode="x unified",
            yaxis=dict(
                title="Global M2 ($T)", showgrid=False, title_font=dict(color="white")
            ),
            yaxis2=dict(
                title="CB Assets ($T)", overlaying="y", side="right", showgrid=True,
                gridcolor="#333", title_font=dict(color="#ff4b4b"), tickfont=dict(color="#ff4b4b")
            ),
            yaxis3=dict(
                title="Bitcoin ($)", overlaying="y", side="right", position=0.95,
                type="log" if log_scale else "linear",
                title_font=dict(color="#ffa500"), tickfont=dict(color="#ffa500"), showgrid=False
            ),
            xaxis=dict(domain=[0, 0.9]),
            legend=dict(orientation="h", y=1.1, x=0)
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error("Could not load data. Check FRED API key and the 'Raw Data' table for source failures.")

except Exception as e:
    st.error(f"An unexpected error occurred during rendering: {e}")
