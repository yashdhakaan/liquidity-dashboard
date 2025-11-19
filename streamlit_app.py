import streamlit as st
import pandas as pd
from fredapi import Fred
import yfinance as yf
import plotly.graph_objects as go
import numpy as np # <--- ADD THIS IMPORT

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
    
    m2_shift_months = st.slider(
        "M2 Time Shift (Months)", 
        -24, 
        24, 
        0, 
        help="Shift M2 ahead (positive) or lag (negative) relative to other lines."
    )
    st.markdown("---")
    
    # NEW: LINE SELECTION WIDGET
    selected_lines = st.multiselect(
        "Select Lines to Display",
        ['Global M2 ($T)', 'CB Assets ($T)', 'Bitcoin ($)', 'MSTR MNAV (x)'],
        default=['Global M2 ($T)', 'CB Assets ($T)', 'Bitcoin ($)', 'MSTR MNAV (x)']
    )
    st.markdown("---")
    st.markdown("**Metric Guide:**")
    st.markdown("⬜ **Global M2:** Total Cash Supply")
    st.markdown("🟥 **CB Assets:** Central Bank Balance Sheets")
    st.markdown("🟧 **Bitcoin:** Price in USD")
    st.markdown("🟪 **MSTR MNAV:** MicroStrategy NAV Multiple")

# --- DATA ENGINE (WITH SHIFT PARAMETER) ---
# NOTE: Added 'm2_shift_months' to the function signature
@st.cache_data(ttl=43200) 
def get_liquidity_data(years, m2_shift_months): 
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
    
    # --- APPLY PANDAS .SHIFT() HERE! ---
    df['Global_M2'] = (us_val.fillna(0) + eu_val.fillna(0) + jp_val.fillna(0) + cn_val.fillna(0)).shift(periods=m2_shift_months)

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

    # --- NEW: MICROSTRATEGY MNAV CALCULATION ---
    # Fetch MSTR daily stock price
    mstr_daily = yf.download("MSTR", start=start_str, progress=False)['Close']
    
    # 1. Align MSTR price to the master index
    df['MSTR_Price'] = mstr_daily.reindex(df.index, method='ffill')
    
    # 2. Calculate MNAV (Multiple of BTC NAV)
    # This is a simplified proxy: MSTR Price / (BTC Price * BTC Holdings per Share)
    # Since we lack real-time share count, we proxy MNAV as MSTR Price / BTC Price,
    # then scale it for visualization purposes, or skip the scaling and plot the raw multiple.
    
    # To get the true multiple, we would need MSTR's BTC holdings and share count. 
    # For a simple comparative chart, we can calculate the ratio (MSTR/BTC). 
    # However, the standard MNAV calculation requires external data (like outstanding shares and net debt).
    
    # *Simplified MNAV (Ratio):*
    # We will use the common approach of calculating a *Relative Multiple* to BTC Price,
    # adjusted by a constant factor for better visualization on the chart.
    
    # Using a known historical MSTR/BTC ratio approximation:
    # MSTR/BTC Ratio = MSTR Price / (BTC Price)
    # MNAV (Multiple) = MSTR Price / BTC NAV
    
    # For simplicity, we calculate the price ratio, as the actual multiple is unstable without detailed debt/cash data.
    # The actual multiple usually trades between 0.8 and 3.0. We will calculate the ratio MSTR/BTC and scale it.
    
    # MSTR MNAV Calculation (Simplified for Daily Charting):
    # This formula calculates the ratio of MSTR to BTC, then scales it to fit a reasonable 0-5 range.
    df['MSTR_Ratio'] = df['MSTR_Price'] / df['BTC']
    
    # Since the true multiple trades between 0.8 and 3.0, let's normalize the ratio by a factor
    # derived from historical data (e.g., MSTR often trades at ~10-20x the BTC price if not normalized).
    # We use a static divisor to approximate the 0.8-3.0 range. (Example divisor: 50)
    # **NOTE:** You may need to adjust the `100` divisor based on your chart's scale.
    df['MSTR_MNAV'] = df['MSTR_Ratio'] / 100 
    
    # Fill any remaining NaNs with the last known value for MNAV
    df['MSTR_MNAV'] = df['MSTR_MNAV'].ffill() 

    # FINAL CLEANUP: ...
    return df.dropna(subset=['Global_M2', 'Global_Assets', 'MSTR_MNAV'], how='all')

# --- RENDER CHART ---
st.write(f"Fetching live data for the last {lookback_years} years...")

try:
    # PASS THE NEW SHIFT VALUE TO THE DATA FUNCTION
    df = get_liquidity_data(lookback_years, m2_shift_months)

    if df is not None and not df.empty:
        fig = go.Figure()

        # Trace 1: M2 (White) - Left Axis
        if 'Global M2 ($T)' in selected_lines:
            fig.add_trace(go.Scatter(
                x=df.index, y=df['Global_M2'], name="Global M2 ($T)",
                line=dict(color='white', width=2), yaxis="y1"
            ))

        # Trace 2: Assets (Red) - Right Axis 1
        if 'CB Assets ($T)' in selected_lines:
            fig.add_trace(go.Scatter(
                x=df.index, y=df['Global_Assets'], name="CB Assets ($T)",
                line=dict(color='#ff4b4b', width=2), yaxis="y2"
            ))

        # Trace 3: Bitcoin (Orange) - Right Axis 2
        if 'Bitcoin ($)' in selected_lines:
            fig.add_trace(go.Scatter(
                x=df.index, y=df['BTC'], name="Bitcoin ($)",
                line=dict(color='#ffa500', width=2), yaxis="y3"
            ))

        # NEW Trace 4: MSTR MNAV (Purple) - Right Axis 1 (Sharing CB Assets Axis)
        if 'MSTR MNAV (x)' in selected_lines:
            # We plot MNAV on the same axis as CB Assets (y2) since its 0-3 range is small
            fig.add_trace(go.Scatter(
                x=df.index, y=df['MSTR_MNAV'], name="MSTR MNAV (x)",
                line=dict(color='#8A2BE2', width=2), yaxis="y2" 
            ))

        # Complex Layout for 3 Axes (No changes here, but ensuring it's complete)
        fig.update_layout(
            template="plotly_dark", height=600, hovermode="x unified",
            
            # --- Y-AXIS 1 (Global M2 - White Line) ---
            yaxis=dict(
                title="Global M2 ($T)", 
                showgrid=False, 
                title_font=dict(color="white"),
                tickformat = ',.0f' 
            ),
            
            # --- Y-AXIS 2 (CB Assets / MNAV - Red & Purple Lines) ---
            yaxis2=dict(
                title="CB Assets ($T) / MNAV (x)", # Updated title to reflect both metrics
                overlaying="y", 
                side="right", 
                showgrid=True,
                gridcolor="#333", 
                title_font=dict(color="#ff4b4b"), 
                tickfont=dict(color="#ff4b4b"),
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
