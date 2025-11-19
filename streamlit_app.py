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

# --- DATA ENGINE (REVISED FOR ROBUST FFILL) ---
@st.cache_data(ttl=43200) # Cache for 12 hours
def get_liquidity_data(years):
    start_date = pd.Timestamp.now() - pd.DateOffset(years=years)
    start_str = start_date.strftime('%Y-%m-%d')
    
    # 1. CREATE A MASTER MONTHLY INDEX (The Core Fix)
    # This guarantees the DataFrame spans the entire period, even if data is missing.
    today = pd.Timestamp.now()
    master_index = pd.date_range(start=start_date, end=today, freq='M')
    df = pd.DataFrame(index=master_index)

    # 2. FETCH MARKET DATA (YFinance)
    tickers = ["EURUSD=X", "JPY=X", "CNY=X", "BTC-USD"] 
    market_data = yf.download(tickers, start=start_str, progress=False)['Close']
    market_monthly = market_data.resample('M').mean() 

    # Align FX rates to the Master Index and ffill
    fx_eu = market_monthly['EURUSD=X'].reindex(df.index, method='ffill')
    fx_jp = market_monthly['JPY=X'].reindex(df.index, method='ffill')
    fx_cn = market_monthly['CNY=X'].reindex(df.index, method='ffill')

    # 3. FETCH & PRE-PROCESS MACRO DATA (FRED)
    try:
        # All series are fetched, then immediately aligned to the Master Index and ffilled
        
        # M2 Supply (Billions or Local Currency)
        m2_us = fred.get_series('M2SL', observation_start=start_str).reindex(df.index, method='ffill')
        m2_eu = fred.get_series('MANMM101EZM189S', observation_start=start_str).reindex(df.index, method='ffill')
        m2_jp = fred.get_series('MANMM101JPM189S', observation_start=start_str).reindex(df.index, method='ffill')
        m2_cn = fred.get_series('MANMM101CNM189S', observation_start=start_str).reindex(df.index, method='ffill')
        
        # Central Bank Assets (Millions or Local Units)
        cb_fed = fred.get_series('WALCL', observation_start=start_str).resample('M').ffill().reindex(df.index, method='ffill')
        cb_ecb = fred.get_series('ECBASSETSW', observation_start=start_str).resample('M').ffill().reindex(df.index, method='ffill')
        cb_boj = fred.get_series('JPNASSETS', observation_start=start_str).resample('M').ffill().reindex(df.index, method='ffill')
    except Exception as e:
        st.warning(f"Error fetching data from FRED. Check logs or key.")
        return None

    # 4. CALCULATE TOTALS (USD TRILLIONS)

    # --- GLOBAL M2 CALCULATION (WHITE LINE) ---
    # US M2SL is in Billions -> / 1000 (to Trillions)
    us_val = m2_us / 1000
    
    # Non-US M2 (Millions of Local Currency) -> Convert to USD, then / 1,000,000 (to Trillions)
    eu_val = ((m2_eu * fx_eu) / 1_000_000) 
    jp_val = ((m2_jp / fx_jp) / 1_000_000) 
    cn_val = ((m2_cn * fx_cn) / 1_000_000) 

    df['Global_M2'] = us_val.fillna(0) + eu_val.fillna(0) + jp_val.fillna(0) + cn_val.fillna(0)

    # --- CB ASSETS CALCULATION (RED LINE) ---
    # US WALCL is in Millions -> / 1,000,000 (to Trillions)
    fed_assets = cb_fed / 1_000_000
    
    # ECB Assets are in Millions of Local Currency -> Convert to USD, then / 1,000,000 (to Trillions)
    ecb_assets = ((cb_ecb * fx_eu) / 1_000_000)
    
    # BOJ JPNASSETS is in 100 Millions of Yen -> Needs conversion to USD Trillions (this conversion is complex but common)
    boj_assets = (cb_boj * 0.0001) / fx_jp # This conversion (100M Yen to Trillions USD) is often correct for this ticker
    
    df['Global_Assets'] = fed_assets.fillna(0) + ecb_assets.fillna(0) + boj_assets.fillna(0)
    
    # --- BITCOIN DATA (FIXED FOR CURRENT DATE) ---
    
    # 1. Fetch Bitcoin DAILY data from the start date to today
    btc_daily = yf.download("BTC-USD", start=start_str, progress=False)['Close']
    
    # 2. Align this daily data to the master monthly index, filling forward to the present.
    # This forces the line to use the latest price up to the final date in the index.
    df['BTC'] = btc_daily.reindex(df.index, method='ffill')

    # FINAL CLEANUP: Remove any rows at the very start where no data existed yet
    return df.dropna(subset=['Global_M2', 'Global_Assets'], how='all')

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
            
            # --- Y-AXIS 1 (Global M2 - White Line) ---
            yaxis=dict(
                title="Global M2 ($T)", 
                showgrid=False, 
                title_font=dict(color="white"),
                # FIX: Set tickformat to force large numbers without 'M' or 'K'
                # The tickformat will display numbers with a comma separator and no decimal places
                tickformat = ',.0f' 
            ),
            
            # --- Y-AXIS 2 (CB Assets - Red Line) ---
            yaxis2=dict(
                title="CB Assets ($T)", 
                overlaying="y", 
                side="right", 
                showgrid=True,
                gridcolor="#333", 
                title_font=dict(color="#ff4b4b"), 
                tickfont=dict(color="#ff4b4b"),
                # FIX: Apply the same large number formatting
                tickformat = ',.0f'
            ),
            
            # --- Y-AXIS 3 (Bitcoin - Orange Line) ---
            yaxis3=dict(
                title="Bitcoin ($)", 
                overlaying="y", 
                side="right", 
                position=0.95,
                type="log" if log_scale else "linear",
                title_font=dict(color="#ffa500"), 
                tickfont=dict(color="#ffa500"), 
                showgrid=False,
                # Bitcoin uses K (Thousands) and no fixed format
                tickformat = '.3s' 
            ),
            
            xaxis=dict(domain=[0, 0.9]),
            legend=dict(orientation="h", y=1.1, x=0)
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error("Could not load data. Check FRED API key and the 'Raw Data' table for source failures.")

except Exception as e:
    st.error(f"An unexpected error occurred during rendering: {e}")
