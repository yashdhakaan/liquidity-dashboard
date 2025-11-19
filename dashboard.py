import streamlit as st
import pandas as pd
from fredapi import Fred
import yfinance as yf
import plotly.graph_objects as go

# --- PAGE CONFIG ---
st.set_page_config(page_title="Global Liquidity Monitor", layout="wide")
st.title("Global Liquidity vs. Bitcoin")
st.markdown("### The 'Fiscal Dominance' Dashboard")

# --- API KEY MANAGEMENT ---
# Try to get key from Streamlit Secrets (Best for deployment)
try:
    api_key = st.secrets["FRED_API_KEY"]
except:
    api_key = None

# If no secret found, ask in sidebar (Best for local testing)
if not api_key:
    with st.sidebar:
        st.warning("⚠️ API Key Missing in Secrets")
        api_key = st.text_input("Enter FRED API Key", type="password")
        st.markdown("[Get free Key](https://fred.stlouisfed.org/docs/api/api_key.html)")

if not api_key:
    st.info("Please enter a FRED API Key to load the chart.")
    st.stop()

fred = Fred(api_key=api_key)

# --- SETTINGS SIDEBAR ---
with st.sidebar:
    st.header("Chart Settings")
    lookback_years = st.slider("Timeframe (Years)", 3, 15, 6)
    log_scale = st.checkbox("Log Scale (Bitcoin)", value=True)
    st.markdown("---")
    st.markdown("**Metric Guide:**")
    st.markdown("⬜ **White (M2):** Global Cash Supply")
    st.markdown("🟥 **Red (Assets):** Central Bank Printing")
    st.markdown("🟧 **Orange (BTC):** Bitcoin Price")

# --- DATA ENGINE ---
@st.cache_data(ttl=43200) # Cache for 12 hours
def get_liquidity_data(years):
    start_date = pd.Timestamp.now() - pd.DateOffset(years=years)
    start_str = start_date.strftime('%Y-%m-%d')

    # 1. FETCH MARKET DATA (YFinance)
    tickers = ["EURUSD=X", "JPY=X", "CNY=X", "BTC-USD"] 
    market_data = yf.download(tickers, start=start_str, progress=False)['Close']
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
        st.error(f"Error fetching FRED Data. Check API Key. Error: {e}")
        return None

    # 3. NORMALIZE TO USD TRILLIONS
    df = pd.DataFrame(index=m2_us.index)
    
    # Currencies aligned to monthly data
    fx_eu = market_monthly['EURUSD=X'].reindex(df.index, method='ffill')
    fx_jp = market_monthly['JPY=X'].reindex(df.index, method='ffill')
    fx_cn = market_monthly['CNY=X'].reindex(df.index, method='ffill')

    # M2 Calculation (Convert all to USD Trillions)
    # US: Billions -> Trillions (/1000)
    # EU: Euros -> USD -> Trillions
    # JP: Yen -> USD (Divide by USDJPY) -> Trillions
    # CN: Yuan -> USD -> Trillions
    df['Global_M2'] = (
        (m2_us / 1000).fillna(0) + 
        ((m2_eu * fx_eu) / 1000).fillna(0) + 
        ((m2_jp / fx_jp) / 1000).fillna(0) + 
        ((m2_cn / fx_cn) / 1000).fillna(0)
    )

    # Assets Calculation (Convert all to USD Trillions)
    # Fed: Millions -> Trillions (/1,000,000)
    # ECB: Millions EUR -> USD -> Trillions
    # BOJ: 100 Million Yen Units -> USD -> Trillions
    df['Global_Assets'] = (
        (cb_fed.resample('M').ffill() / 1_000_000).fillna(0) + 
        ((cb_ecb.resample('M').ffill() * fx_eu) / 1_000_000).fillna(0) + 
        ((cb_boj.resample('M').ffill() * 0.0001) / fx_jp).fillna(0)
    )
    
    # Bitcoin Data
    df['BTC'] = market_monthly['BTC-USD'].reindex(df.index, method='ffill')

    return df.dropna()

# --- RENDER CHART ---
st.write(f"fetching live data for the last {lookback_years} years...")
df = get_liquidity_data(lookback_years)

if df is not None:
    fig = go.Figure()

    # Trace 1: M2 (White)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Global_M2'], name="Global M2 ($T)",
        line=dict(color='white', width=2), yaxis="y1"
    ))

    # Trace 2: Assets (Red)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Global_Assets'], name="CB Assets ($T)",
        line=dict(color='#ff4b4b', width=2), yaxis="y2"
    ))

    # Trace 3: Bitcoin (Orange)
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
