from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from regulatory_costs import (
    ets_emissions_factor_tco2e_per_t,
    fueleu_penalty_eur,
    portfolio_segment_cost_values,
)


# =============================================================================
# Product and regulatory baseline
# =============================================================================
APP_NAME = "Maritime Carbon Cost Optimizer"
APP_VERSION = "4.0"
APP_OWNER = "Nikitas Eleftheriou"
REGULATORY_CHECK_DATE = "2026-05-21"
REFERENCE_INTENSITY_GCO2E_MJ = 91.16
APP_DIR = Path(__file__).resolve().parent
SCENARIO_PATH = APP_DIR / ".carbon_optimizer_scenarios.json"

YEARS = list(range(2025, 2051))
FUELEU_REDUCTION_STEPS = {
    2025: 0.020,
    2030: 0.060,
    2035: 0.145,
    2040: 0.310,
    2045: 0.620,
    2050: 0.800,
}

ROUTE_SCOPES = {
    "Intra EU/EEA voyage": {"fueleu": 1.0, "ets": 1.0, "description": "100% FuelEU and 100% ETS."},
    "EU berth / port stay": {"fueleu": 1.0, "ets": 1.0, "description": "100% in scope while at berth or moving inside port."},
    "EU to non-EU voyage": {"fueleu": 0.5, "ets": 0.5, "description": "50% cross-border FuelEU and ETS."},
    "non-EU to EU voyage": {"fueleu": 0.5, "ets": 0.5, "description": "50% cross-border FuelEU and ETS."},
    "Out of EU scope": {"fueleu": 0.0, "ets": 0.0, "description": "Excluded from EU FuelEU and ETS model scope."},
    "Derogated / excluded call": {"fueleu": 0.0, "ets": 0.0, "description": "For small-island, outermost-region, emergency, or other checked exclusions."},
}

REGULATORY_SOURCES = {
    "FuelEU Maritime": "https://transport.ec.europa.eu/transport-modes/maritime/decarbonising-maritime-transport-fueleu-maritime_en",
    "FuelEU Q&A": "https://transport.ec.europa.eu/transport-modes/maritime/decarbonising-maritime-transport-fueleu-maritime/questions-and-answers-regulation-eu-20231805-use-renewable-and-low-carbon-fuels-maritime-transport_en",
    "FuelEU Regulation (EU) 2023/1805": "https://eur-lex.europa.eu/eli/reg/2023/1805/oj",
    "FuelEU database Implementing Regulation (EU) 2026/394": "https://eur-lex.europa.eu/eli/reg_impl/2026/394/oj/eng",
    "EU ETS maritime": "https://climate.ec.europa.eu/eu-action/transport-decarbonisation/reducing-emissions-shipping-sector_en",
    "EMSA ETS FAQ": "https://emsa.europa.eu/reducing-emissions/extension-ets/faq-extension-ets.html",
    "MRV Regulation (EU) 2023/957": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32023R0957",
    "Alternative Fuels Infrastructure": "https://transport.ec.europa.eu/transport-themes/clean-transport/alternative-fuels-sustainable-mobility-europe/alternative-fuels-infrastructure_en",
}


# =============================================================================
# Page setup
# =============================================================================
st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    :root {
        --ink: #172033;
        --muted: #64748b;
        --line: #d8dee8;
        --surface: #ffffff;
        --band: #f5f7fb;
        --accent: #0f766e;
        --warn: #b45309;
        --bad: #b91c1c;
        --good: #047857;
    }
    .block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1440px; }
    h1, h2, h3 { color: var(--ink); letter-spacing: 0; }
    div[data-testid="stMetric"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.8rem 0.9rem;
        min-height: 108px;
    }
    div[data-testid="stMetricValue"] { font-size: 1.35rem; color: var(--ink); }
    div[data-testid="stMetricLabel"] { color: var(--muted); }
    .reg-card {
        border: 1px solid var(--line);
        border-left: 4px solid var(--accent);
        border-radius: 8px;
        padding: 0.85rem 0.95rem;
        background: var(--surface);
        min-height: 148px;
        margin-bottom: 0.7rem;
    }
    .reg-card h4 { margin: 0 0 0.35rem 0; font-size: 1rem; color: var(--ink); }
    .reg-card p { margin: 0; color: #334155; line-height: 1.42; }
    .small-muted { color: var(--muted); font-size: 0.86rem; line-height: 1.35; }
    .status-pill {
        display: inline-block;
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 0.18rem 0.6rem;
        background: #eef6f5;
        color: #115e59;
        font-size: 0.78rem;
        font-weight: 650;
        margin-right: 0.35rem;
    }
    .decision-box {
        border: 1px solid #b6d7d2;
        border-radius: 8px;
        padding: 0.9rem 1rem;
        background: #f1faf8;
        color: #123631;
    }
    .risk-box {
        border: 1px solid #f2c7a0;
        border-radius: 8px;
        padding: 0.9rem 1rem;
        background: #fff7ed;
        color: #5f2f08;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 0.35rem; }
    .stTabs [data-baseweb="tab"] {
        border: 1px solid var(--line);
        border-radius: 8px 8px 0 0;
        padding: 0.55rem 0.9rem;
        background: #f8fafc;
    }
</style>
""",
    unsafe_allow_html=True,
)


# =============================================================================
# Defaults
# =============================================================================
def default_fuels() -> pd.DataFrame:
    """Planning defaults. Values must be checked against the ship monitoring plan."""
    return pd.DataFrame(
        [
            {
                "Code": "HSFO",
                "Fuel": "HSFO",
                "Family": "Fossil oil",
                "LCV_MJ_t": 40_200.0,
                "WtW_gCO2e_MJ": 93.3,
                "ETS_CO2_t_t": 3.114,
                "ETS_CH4_t_t": 0.00005,
                "ETS_N2O_t_t": 0.00018,
                "Price_EUR_t": 470.0,
                "Supply_cap_t": 0.0,
                "RFNBO": False,
                "ETS_zero_if_certified": False,
                "Color": "#1f2937",
            },
            {
                "Code": "VLSFO",
                "Fuel": "VLSFO / HFO blend",
                "Family": "Fossil oil",
                "LCV_MJ_t": 41_000.0,
                "WtW_gCO2e_MJ": 91.6,
                "ETS_CO2_t_t": 3.114,
                "ETS_CH4_t_t": 0.00005,
                "ETS_N2O_t_t": 0.00018,
                "Price_EUR_t": 575.0,
                "Supply_cap_t": 0.0,
                "RFNBO": False,
                "ETS_zero_if_certified": False,
                "Color": "#334155",
            },
            {
                "Code": "MGO",
                "Fuel": "Marine gas oil",
                "Family": "Fossil oil",
                "LCV_MJ_t": 42_700.0,
                "WtW_gCO2e_MJ": 90.7,
                "ETS_CO2_t_t": 3.206,
                "ETS_CH4_t_t": 0.00005,
                "ETS_N2O_t_t": 0.00018,
                "Price_EUR_t": 720.0,
                "Supply_cap_t": 0.0,
                "RFNBO": False,
                "ETS_zero_if_certified": False,
                "Color": "#64748b",
            },
            {
                "Code": "LNG",
                "Fuel": "LNG",
                "Family": "Fossil gas",
                "LCV_MJ_t": 48_000.0,
                "WtW_gCO2e_MJ": 76.4,
                "ETS_CO2_t_t": 2.750,
                "ETS_CH4_t_t": 0.00120,
                "ETS_N2O_t_t": 0.00003,
                "Price_EUR_t": 660.0,
                "Supply_cap_t": 0.0,
                "RFNBO": False,
                "ETS_zero_if_certified": False,
                "Color": "#7c3aed",
            },
            {
                "Code": "METHANOL",
                "Fuel": "Fossil methanol",
                "Family": "Fossil alcohol",
                "LCV_MJ_t": 19_900.0,
                "WtW_gCO2e_MJ": 92.0,
                "ETS_CO2_t_t": 1.375,
                "ETS_CH4_t_t": 0.00002,
                "ETS_N2O_t_t": 0.00003,
                "Price_EUR_t": 420.0,
                "Supply_cap_t": 0.0,
                "RFNBO": False,
                "ETS_zero_if_certified": False,
                "Color": "#a16207",
            },
            {
                "Code": "HVO",
                "Fuel": "HVO renewable diesel",
                "Family": "Certified biofuel",
                "LCV_MJ_t": 37_000.0,
                "WtW_gCO2e_MJ": 14.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 1_580.0,
                "Supply_cap_t": 6_000.0,
                "RFNBO": False,
                "ETS_zero_if_certified": True,
                "Color": "#16a34a",
            },
            {
                "Code": "FAME",
                "Fuel": "FAME biodiesel",
                "Family": "Certified biofuel",
                "LCV_MJ_t": 37_200.0,
                "WtW_gCO2e_MJ": 28.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 1_250.0,
                "Supply_cap_t": 5_000.0,
                "RFNBO": False,
                "ETS_zero_if_certified": True,
                "Color": "#65a30d",
            },
            {
                "Code": "UCOME",
                "Fuel": "UCOME biodiesel",
                "Family": "Certified biofuel",
                "LCV_MJ_t": 37_000.0,
                "WtW_gCO2e_MJ": 16.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 1_420.0,
                "Supply_cap_t": 3_500.0,
                "RFNBO": False,
                "ETS_zero_if_certified": True,
                "Color": "#4d7c0f",
            },
            {
                "Code": "BIO_METHANOL",
                "Fuel": "Bio-methanol",
                "Family": "Certified biofuel",
                "LCV_MJ_t": 19_900.0,
                "WtW_gCO2e_MJ": 12.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 950.0,
                "Supply_cap_t": 3_000.0,
                "RFNBO": False,
                "ETS_zero_if_certified": True,
                "Color": "#15803d",
            },
            {
                "Code": "BIO_OIL",
                "Fuel": "Advanced bio-oil",
                "Family": "Certified biofuel",
                "LCV_MJ_t": 38_000.0,
                "WtW_gCO2e_MJ": 22.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 1_050.0,
                "Supply_cap_t": 2_500.0,
                "RFNBO": False,
                "ETS_zero_if_certified": True,
                "Color": "#84cc16",
            },
            {
                "Code": "LBM",
                "Fuel": "Liquefied biomethane",
                "Family": "Certified biofuel",
                "LCV_MJ_t": 50_000.0,
                "WtW_gCO2e_MJ": 18.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.00015,
                "ETS_N2O_t_t": 0.00002,
                "Price_EUR_t": 1_450.0,
                "Supply_cap_t": 5_000.0,
                "RFNBO": False,
                "ETS_zero_if_certified": True,
                "Color": "#22c55e",
            },
            {
                "Code": "E_METHANOL",
                "Fuel": "RFNBO e-methanol",
                "Family": "RFNBO",
                "LCV_MJ_t": 19_900.0,
                "WtW_gCO2e_MJ": 8.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 1_350.0,
                "Supply_cap_t": 3_500.0,
                "RFNBO": True,
                "ETS_zero_if_certified": True,
                "Color": "#0891b2",
            },
            {
                "Code": "E_AMMONIA",
                "Fuel": "RFNBO e-ammonia",
                "Family": "RFNBO",
                "LCV_MJ_t": 18_600.0,
                "WtW_gCO2e_MJ": 5.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.00008,
                "Price_EUR_t": 1_050.0,
                "Supply_cap_t": 2_000.0,
                "RFNBO": True,
                "ETS_zero_if_certified": True,
                "Color": "#0f766e",
            },
            {
                "Code": "H2_RFNB",
                "Fuel": "RFNBO hydrogen",
                "Family": "RFNBO",
                "LCV_MJ_t": 120_000.0,
                "WtW_gCO2e_MJ": 4.0,
                "ETS_CO2_t_t": 0.0,
                "ETS_CH4_t_t": 0.0,
                "ETS_N2O_t_t": 0.0,
                "Price_EUR_t": 4_200.0,
                "Supply_cap_t": 700.0,
                "RFNBO": True,
                "ETS_zero_if_certified": True,
                "Color": "#0284c7",
            },
        ]
    )


def default_segments(fuel_codes: Iterable[str]) -> pd.DataFrame:
    rows = [
        {
            "Segment": "Asia-Europe inbound leg",
            "Route scope": "non-EU to EU voyage",
            "Annual trips": 18,
            "Low-carbon first": True,
            "OPS_MWh_trip": 0.0,
            "HSFO_t_trip": 165.0,
            "VLSFO_t_trip": 0.0,
            "MGO_t_trip": 6.0,
            "LNG_t_trip": 0.0,
        },
        {
            "Segment": "EU port stay",
            "Route scope": "EU berth / port stay",
            "Annual trips": 18,
            "Low-carbon first": False,
            "OPS_MWh_trip": 0.0,
            "HSFO_t_trip": 8.0,
            "VLSFO_t_trip": 0.0,
            "MGO_t_trip": 3.0,
            "LNG_t_trip": 0.0,
        },
        {
            "Segment": "Intra EU repositioning",
            "Route scope": "Intra EU/EEA voyage",
            "Annual trips": 8,
            "Low-carbon first": False,
            "OPS_MWh_trip": 0.0,
            "HSFO_t_trip": 38.0,
            "VLSFO_t_trip": 0.0,
            "MGO_t_trip": 4.0,
            "LNG_t_trip": 0.0,
        },
        {
            "Segment": "Europe-Asia outbound leg",
            "Route scope": "EU to non-EU voyage",
            "Annual trips": 18,
            "Low-carbon first": True,
            "OPS_MWh_trip": 0.0,
            "HSFO_t_trip": 155.0,
            "VLSFO_t_trip": 0.0,
            "MGO_t_trip": 6.0,
            "LNG_t_trip": 0.0,
        },
    ]
    df = pd.DataFrame(rows)
    for code in fuel_codes:
        col = f"{code}_t_trip"
        if col not in df.columns:
            df[col] = 0.0
    return df


# =============================================================================
# Utility
# =============================================================================
def money(value: float, decimals: int = 0) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}€{abs(value):,.{decimals}f}"


def number(value: float, decimals: int = 1) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value:,.{decimals}f}"


def percent(value: float, decimals: int = 1) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{100 * value:,.{decimals}f}%"


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, payload: Dict[str, Any]) -> bool:
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def get_secret_section(name: str) -> Dict[str, Any]:
    try:
        return dict(st.secrets.get(name, {}))
    except Exception:
        return {}


def reduction_for_year(year: int) -> float:
    active = 2025
    for step_year in sorted(FUELEU_REDUCTION_STEPS):
        if year >= step_year:
            active = step_year
    return FUELEU_REDUCTION_STEPS[active]


def fueleu_target(year: int) -> float:
    return REFERENCE_INTENSITY_GCO2E_MJ * (1.0 - reduction_for_year(year))


def rfnbo_reward_factor(year: int) -> float:
    return 2.0 if 2025 <= int(year) <= 2033 else 1.0


def ets_surrender_factor(year: int) -> float:
    if int(year) == 2025:
        return 0.70
    if int(year) >= 2026:
        return 1.00
    return 0.40


def sanitize_fuels(df: pd.DataFrame) -> pd.DataFrame:
    required = default_fuels().columns.tolist()
    work = df.copy()
    for col in required:
        if col not in work.columns:
            work[col] = default_fuels()[col].iloc[0]
    work = work[required]
    work["Code"] = work["Code"].astype(str).str.strip().str.upper().str.replace(" ", "_", regex=False)
    work["Fuel"] = work["Fuel"].astype(str)
    work["Family"] = work["Family"].astype(str)
    for col in ["LCV_MJ_t", "WtW_gCO2e_MJ", "ETS_CO2_t_t", "ETS_CH4_t_t", "ETS_N2O_t_t", "Price_EUR_t", "Supply_cap_t"]:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0).clip(lower=0.0)
    for col in ["RFNBO", "ETS_zero_if_certified"]:
        work[col] = work[col].fillna(False).astype(bool)
    work["Color"] = work["Color"].astype(str).replace("", "#334155")
    return work.drop_duplicates(subset=["Code"], keep="first").reset_index(drop=True)


def ensure_hsfo_option(df: pd.DataFrame) -> pd.DataFrame:
    work = sanitize_fuels(df)
    hsfo_default = default_fuels().loc[lambda x: x["Code"] == "HSFO"].copy()
    if "HSFO" not in set(work["Code"]):
        work = pd.concat([hsfo_default, work], ignore_index=True)
    hsfo_mask = work["Code"] == "HSFO"
    work.loc[hsfo_mask, "Fuel"] = "HSFO"
    work.loc[hsfo_mask, "Family"] = "Fossil oil"
    work = pd.concat([work.loc[hsfo_mask], work.loc[~hsfo_mask]], ignore_index=True)
    return sanitize_fuels(work)


def merge_missing_default_fuels(df: pd.DataFrame) -> pd.DataFrame:
    work = ensure_hsfo_option(df)
    defaults = default_fuels()
    missing_defaults = defaults.loc[~defaults["Code"].isin(set(work["Code"]))].copy()
    if not missing_defaults.empty:
        work = pd.concat([work, missing_defaults], ignore_index=True)
    default_order = {code: idx for idx, code in enumerate(defaults["Code"].tolist())}
    work["_order"] = work["Code"].map(lambda code: default_order.get(code, len(default_order) + 1))
    work = work.sort_values(["_order", "Code"]).drop(columns=["_order"]).reset_index(drop=True)
    return sanitize_fuels(work)


def fuel_lookup(fuels_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    return ensure_hsfo_option(fuels_df).set_index("Code").to_dict(orient="index")


def fuel_codes(fuels_df: pd.DataFrame) -> List[str]:
    return ensure_hsfo_option(fuels_df)["Code"].tolist()


def fossil_codes(fuels_df: pd.DataFrame) -> List[str]:
    fuels = ensure_hsfo_option(fuels_df)
    mask = ~(fuels["Family"].str.lower().str.contains("bio|rfnbo|renewable|electric", regex=True))
    return fuels.loc[mask, "Code"].tolist()


def alt_codes(fuels_df: pd.DataFrame) -> List[str]:
    fuels = ensure_hsfo_option(fuels_df)
    mask = fuels["Family"].str.lower().str.contains("bio|rfnbo|renewable", regex=True) | fuels["RFNBO"]
    return fuels.loc[mask, "Code"].tolist()


def sanitize_segments(df: pd.DataFrame, codes: List[str]) -> pd.DataFrame:
    base_cols = ["Segment", "Route scope", "Annual trips", "Low-carbon first", "OPS_MWh_trip"]
    work = df.copy()
    for col in base_cols:
        if col not in work.columns:
            work[col] = "" if col == "Segment" else 0.0
    work["Segment"] = work["Segment"].astype(str).replace("", "Segment")
    work["Route scope"] = work["Route scope"].where(work["Route scope"].isin(ROUTE_SCOPES), "Intra EU/EEA voyage")
    work["Annual trips"] = pd.to_numeric(work["Annual trips"], errors="coerce").fillna(0.0).clip(lower=0.0)
    work["Low-carbon first"] = work["Low-carbon first"].fillna(False).astype(bool)
    work["OPS_MWh_trip"] = pd.to_numeric(work["OPS_MWh_trip"], errors="coerce").fillna(0.0).clip(lower=0.0)
    for code in codes:
        col = f"{code}_t_trip"
        if col not in work.columns:
            work[col] = 0.0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0).clip(lower=0.0)
    keep = base_cols + [f"{code}_t_trip" for code in codes]
    return work[keep].reset_index(drop=True)


def style_fig(fig: go.Figure, height: int = 420, legend_right: bool = False) -> go.Figure:
    legend = (
        dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02)
        if legend_right
        else dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
    )
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=150 if legend_right else 10, t=62, b=20),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="Arial, sans-serif", size=13, color="#172033"),
        legend=legend,
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#e5e7eb", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#e5e7eb", zeroline=False)
    return fig


def access_gate() -> None:
    auth = get_secret_section("auth")
    if not bool(auth.get("enabled", False)):
        st.session_state.setdefault("subscriber_name", "Demo workspace")
        st.session_state.setdefault("subscription_plan", "Trial")
        return

    ttl_days = int(auth.get("trial_days", 14))
    valid_users = auth.get("users", {})
    if isinstance(valid_users, str):
        try:
            valid_users = json.loads(valid_users)
        except json.JSONDecodeError:
            valid_users = {}

    if st.session_state.get("_authenticated"):
        return

    st.title("Subscriber Sign In")
    st.caption("Access is restricted to active subscribers and trial accounts.")
    with st.form("login", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if not submitted:
        st.stop()
    if not username or valid_users.get(username) != password:
        st.error("Invalid credentials or inactive subscription.")
        st.stop()

    st.session_state["_authenticated"] = True
    st.session_state["subscriber_name"] = username
    st.session_state["subscription_plan"] = auth.get("plan", "Subscriber")
    st.session_state["trial_expires"] = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).date().isoformat()
    st.rerun()


def init_state() -> None:
    if "fuels_df" not in st.session_state:
        st.session_state["fuels_df"] = default_fuels()
    st.session_state["fuels_df"] = ensure_hsfo_option(st.session_state["fuels_df"])
    refresh_editors = st.session_state.get("_hsfo_option_version") != "plain-hsfo-v2"
    if st.session_state.get("_fuel_library_version") != "expanded-biofuels-v1":
        st.session_state["fuels_df"] = merge_missing_default_fuels(st.session_state["fuels_df"])
        st.session_state["_fuel_library_version"] = "expanded-biofuels-v1"
        refresh_editors = True
    if refresh_editors:
        st.session_state.pop("fuel_editor", None)
        st.session_state.pop("fuel_editor_plain_hsfo", None)
        st.session_state["_fuel_editor_revision"] = int(st.session_state.get("_fuel_editor_revision", 0)) + 1
        st.session_state.pop("segment_editor", None)
        st.session_state["_hsfo_option_version"] = "plain-hsfo-v2"
    st.session_state.setdefault("_fuel_editor_revision", 0)
    if "segments_df" not in st.session_state:
        st.session_state["segments_df"] = default_segments(fuel_codes(st.session_state["fuels_df"]))
    st.session_state["segments_df"] = sanitize_segments(st.session_state["segments_df"], fuel_codes(st.session_state["fuels_df"]))
    if "scenarios" not in st.session_state:
        st.session_state["scenarios"] = load_json(SCENARIO_PATH)


# =============================================================================
# Core calculations
# =============================================================================
def fueleu_scoped_energy(
    row: pd.Series,
    fuels: Dict[str, Dict[str, Any]],
    codes: List[str],
) -> Tuple[Dict[str, float], Dict[str, float], float, float]:
    trips = float(row.get("Annual trips", 0.0) or 0.0)
    scope = ROUTE_SCOPES[str(row.get("Route scope", "Intra EU/EEA voyage"))]["fueleu"]
    all_energy = {}
    annual_energy = {}
    for code in codes:
        mass = float(row.get(f"{code}_t_trip", 0.0) or 0.0) * trips
        mj = mass * float(fuels[code]["LCV_MJ_t"])
        annual_energy[code] = mj
        all_energy[code] = mj

    ops_mj = float(row.get("OPS_MWh_trip", 0.0) or 0.0) * trips * 3_600.0
    all_energy["OPS"] = ops_mj

    if scope <= 0:
        return all_energy, {code: 0.0 for code in codes} | {"OPS": 0.0}, 0.0, ops_mj

    if math.isclose(scope, 0.5) and bool(row.get("Low-carbon first", False)):
        scoped_total = scope * (sum(annual_energy.values()) + ops_mj)
        scoped_energy = {code: 0.0 for code in codes}
        scoped_energy["OPS"] = 0.0
        ordered = sorted(
            codes,
            key=lambda c: (
                0 if bool(fuels[c].get("RFNBO", False)) or "bio" in str(fuels[c].get("Family", "")).lower() else 1,
                float(fuels[c]["WtW_gCO2e_MJ"]),
            ),
        )
        if ops_mj > 0:
            take = min(scoped_total, ops_mj)
            scoped_energy["OPS"] += take
            scoped_total -= take
        for code in ordered:
            if scoped_total <= 1e-9:
                break
            take = min(scoped_total, annual_energy[code])
            scoped_energy[code] += take
            scoped_total -= take
        if scoped_total > 1e-9:
            remaining = sum(max(annual_energy[c] - scoped_energy[c], 0.0) for c in codes)
            if remaining > 0:
                for code in codes:
                    available = max(annual_energy[code] - scoped_energy[code], 0.0)
                    scoped_energy[code] += scoped_total * available / remaining
        return all_energy, scoped_energy, scope, ops_mj

    scoped_energy = {code: annual_energy[code] * scope for code in codes}
    scoped_energy["OPS"] = ops_mj * scope
    return all_energy, scoped_energy, scope, ops_mj


def calculate_year_profile(
    segments_df: pd.DataFrame,
    fuels_df: pd.DataFrame,
    year: int,
    assumptions: Dict[str, float],
) -> Dict[str, Any]:
    fuels = fuel_lookup(fuels_df)
    codes = list(fuels.keys())
    segments = sanitize_segments(segments_df, codes)

    energy_all = {code: 0.0 for code in codes}
    energy_scope = {code: 0.0 for code in codes}
    energy_all["OPS"] = 0.0
    energy_scope["OPS"] = 0.0
    masses_all = {code: 0.0 for code in codes}
    ets_mass_scope = {code: 0.0 for code in codes}
    ops_mwh_total = 0.0

    for _, row in segments.iterrows():
        trips = float(row["Annual trips"])
        route = str(row["Route scope"])
        ets_scope = ROUTE_SCOPES[route]["ets"]
        all_energy_row, scoped_energy_row, _, _ = fueleu_scoped_energy(row, fuels, codes)
        for code in codes:
            mass = float(row.get(f"{code}_t_trip", 0.0) or 0.0) * trips
            masses_all[code] += mass
            ets_mass_scope[code] += mass * ets_scope
            energy_all[code] += all_energy_row[code]
            energy_scope[code] += scoped_energy_row[code]
        energy_all["OPS"] += all_energy_row["OPS"]
        energy_scope["OPS"] += scoped_energy_row["OPS"]
        ops_mwh_total += float(row.get("OPS_MWh_trip", 0.0) or 0.0) * trips

    e_scope = sum(energy_scope.values())
    numerator = 0.0
    rfnbo_energy = 0.0
    for code in codes:
        numerator += energy_scope[code] * float(fuels[code]["WtW_gCO2e_MJ"])
        if bool(fuels[code].get("RFNBO", False)):
            rfnbo_energy += energy_scope[code]
    numerator += energy_scope["OPS"] * float(assumptions["ops_wtw_g_mj"])
    denom = e_scope + (rfnbo_reward_factor(year) - 1.0) * rfnbo_energy
    attained = numerator / denom if denom > 0 else 0.0
    target = fueleu_target(year)
    balance = ((target - attained) * e_scope) / 1_000_000.0 if e_scope > 0 else 0.0

    include_nonco2 = int(year) >= 2026
    ets_raw = 0.0
    for code in codes:
        ets_factor = ets_emissions_factor_tco2e_per_t(
            float(fuels[code]["ETS_CO2_t_t"]),
            float(fuels[code]["ETS_CH4_t_t"]),
            float(fuels[code]["ETS_N2O_t_t"]),
            float(assumptions["gwp_ch4"]),
            float(assumptions["gwp_n2o"]),
            include_nonco2,
            bool(fuels[code].get("ETS_zero_if_certified", False)),
        )
        ets_raw += ets_mass_scope[code] * ets_factor
    ets_covered = ets_raw * ets_surrender_factor(year)

    price_multiplier = (1.0 + float(assumptions["fuel_escalation"])) ** max(0, year - int(assumptions["start_year"]))
    fuel_cost = sum(masses_all[code] * float(fuels[code]["Price_EUR_t"]) * price_multiplier for code in codes)
    energy_total = sum(energy_all.values())
    fuel_share = {code: (energy_all[code] / energy_total if energy_total > 0 else 0.0) for code in codes}

    return {
        "Year": year,
        "Target_gCO2e_MJ": target,
        "Attained_gCO2e_MJ": attained,
        "FuelEU_Balance_tCO2e": balance,
        "FuelEU_Energy_MJ": e_scope,
        "RFNBO_Energy_MJ": rfnbo_energy,
        "ETS_Raw_tCO2e": ets_raw,
        "ETS_Covered_tCO2e": ets_covered,
        "Fuel_Cost_EUR": fuel_cost,
        "Masses_t": masses_all,
        "ETS_Masses_t": ets_mass_scope,
        "Energy_All_MJ": energy_all,
        "Energy_Scope_MJ": energy_scope,
        "Fuel_Share": fuel_share,
        "OPS_MWh": ops_mwh_total,
    }


def eua_price_for_year(year: int, assumptions: Dict[str, float]) -> float:
    return float(assumptions["eua_price"]) * (1.0 + float(assumptions["eua_escalation"])) ** max(
        0, int(year) - int(assumptions["start_year"])
    )


def current_segment_costs(
    segments_df: pd.DataFrame,
    fuels_df: pd.DataFrame,
    year: int,
    assumptions: Dict[str, float],
) -> pd.DataFrame:
    codes = fuel_codes(fuels_df)
    segments = sanitize_segments(segments_df, codes)
    rows: List[Dict[str, Any]] = []
    eua_price = eua_price_for_year(year, assumptions)
    for idx, segment in segments.iterrows():
        single = pd.DataFrame([segment])
        profile = calculate_year_profile(single, fuels_df, year, assumptions)
        costs = portfolio_segment_cost_values(
            float(profile["FuelEU_Balance_tCO2e"]),
            float(profile["Attained_gCO2e_MJ"]),
            float(assumptions["fueleu_penalty_vlsfo"]),
            float(profile["ETS_Covered_tCO2e"]),
            eua_price,
        )
        rows.append(
            {
                "Segment": str(segment.get("Segment", f"Segment {idx + 1}")),
                "FuelEU Cost EUR": costs["fueleu_cost_eur"],
                "EU ETS Cost EUR": costs["ets_cost_eur"],
                "Total Regulatory Cost EUR": costs["total_regulatory_cost_eur"],
                "FuelEU Deficit tCO2e": costs["signed_deficit_tco2e"],
                "ETS Covered tCO2e": float(profile["ETS_Covered_tCO2e"]),
                "Attained gCO2e/MJ": float(profile["Attained_gCO2e_MJ"]),
                "FuelEU Limit gCO2e/MJ": float(profile["Target_gCO2e_MJ"]),
            }
        )
    if rows:
        total = {
            "Segment": "TOTAL",
            "FuelEU Cost EUR": sum(r["FuelEU Cost EUR"] for r in rows),
            "EU ETS Cost EUR": sum(r["EU ETS Cost EUR"] for r in rows),
            "Total Regulatory Cost EUR": sum(r["Total Regulatory Cost EUR"] for r in rows),
            "FuelEU Deficit tCO2e": sum(r["FuelEU Deficit tCO2e"] for r in rows),
            "ETS Covered tCO2e": sum(r["ETS Covered tCO2e"] for r in rows),
            "Attained gCO2e/MJ": np.nan,
            "FuelEU Limit gCO2e/MJ": fueleu_target(year),
        }
        rows.append(total)
    return pd.DataFrame(rows)


def gfi_yearly_df(
    segments_df: pd.DataFrame,
    fuels_df: pd.DataFrame,
    years: Iterable[int],
    assumptions: Dict[str, float],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for year in years:
        profile = calculate_year_profile(segments_df, fuels_df, int(year), assumptions)
        rows.append(
            {
                "Year": int(year),
                "Attained GFI": float(profile["Attained_gCO2e_MJ"]),
                "FuelEU GFI Limit": float(profile["Target_gCO2e_MJ"]),
            }
        )
    return pd.DataFrame(rows)


def apply_fuel_switch(
    segments_df: pd.DataFrame,
    fuels_df: pd.DataFrame,
    reduce_code: str,
    mix: Dict[str, float],
    replace_fraction: float,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    codes = fuel_codes(fuels_df)
    fuels = fuel_lookup(fuels_df)
    work = sanitize_segments(segments_df, codes).copy()
    if reduce_code not in codes or replace_fraction <= 0 or not mix:
        return work, {}
    total_annual_mass = float((work[f"{reduce_code}_t_trip"] * work["Annual trips"]).sum())
    target_reduction = max(0.0, total_annual_mass * min(replace_fraction, 1.0))
    remaining = target_reduction
    added = {code: 0.0 for code in mix}

    priority = work.assign(
        _scope=work["Route scope"].map(lambda x: ROUTE_SCOPES[str(x)]["ets"]),
        _annual=work[f"{reduce_code}_t_trip"] * work["Annual trips"],
    ).sort_values(["_scope", "_annual"], ascending=[False, False])

    for idx in priority.index:
        if remaining <= 1e-9:
            break
        trips = float(work.at[idx, "Annual trips"])
        if trips <= 0:
            continue
        available = float(work.at[idx, f"{reduce_code}_t_trip"]) * trips
        take = min(remaining, available)
        per_trip_take = take / trips
        work.at[idx, f"{reduce_code}_t_trip"] = max(0.0, float(work.at[idx, f"{reduce_code}_t_trip"]) - per_trip_take)
        energy_removed = take * float(fuels[reduce_code]["LCV_MJ_t"])
        for alt, share in mix.items():
            if alt not in codes or alt == reduce_code:
                continue
            alt_mass = (energy_removed * float(share)) / max(float(fuels[alt]["LCV_MJ_t"]), 1e-9)
            work.at[idx, f"{alt}_t_trip"] = float(work.at[idx, f"{alt}_t_trip"]) + alt_mass / trips
            added[alt] = added.get(alt, 0.0) + alt_mass
        remaining -= take
    return work, {k: v for k, v in added.items() if v > 1e-9}


def apply_ops_program(
    segments_df: pd.DataFrame,
    fuels_df: pd.DataFrame,
    replacement_fraction: float,
) -> Tuple[pd.DataFrame, float]:
    codes = fuel_codes(fuels_df)
    fuels = fuel_lookup(fuels_df)
    work = sanitize_segments(segments_df, codes).copy()
    fossils = fossil_codes(fuels_df)
    if replacement_fraction <= 0:
        return work, 0.0
    added_mwh = 0.0
    for idx, row in work.iterrows():
        if str(row["Route scope"]) != "EU berth / port stay":
            continue
        trips = float(row["Annual trips"])
        if trips <= 0:
            continue
        energy_removed_annual = 0.0
        for code in fossils:
            if code not in codes:
                continue
            mass_trip = float(work.at[idx, f"{code}_t_trip"])
            remove_trip = mass_trip * min(replacement_fraction, 1.0)
            work.at[idx, f"{code}_t_trip"] = max(0.0, mass_trip - remove_trip)
            energy_removed_annual += remove_trip * trips * float(fuels[code]["LCV_MJ_t"])
        if energy_removed_annual > 0:
            added_mwh += energy_removed_annual / 3_600.0
            work.at[idx, "OPS_MWh_trip"] = float(work.at[idx, "OPS_MWh_trip"]) + (energy_removed_annual / 3_600.0) / trips
    return work, added_mwh


def simulate_strategy(
    base_segments: pd.DataFrame,
    fuels_df: pd.DataFrame,
    assumptions: Dict[str, float],
    strategy: Dict[str, Any],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    carry = 0.0
    start_year = int(assumptions["start_year"])
    end_year = int(assumptions["end_year"])
    bank_surplus = bool(strategy.get("bank_surplus", True))
    sell_surplus = bool(strategy.get("sell_surplus", False))
    pool_deficit = bool(strategy.get("pool_deficit", False))

    for year in range(start_year, end_year + 1):
        work_segments = base_segments.copy()
        added_fuels: Dict[str, float] = {}
        ops_added_mwh = 0.0
        if year >= int(strategy.get("switch_start_year", start_year)):
            work_segments, added_fuels = apply_fuel_switch(
                work_segments,
                fuels_df,
                str(strategy.get("reduce_fuel", "")),
                dict(strategy.get("mix", {})),
                float(strategy.get("replace_fraction", 0.0)),
            )
        if year >= int(strategy.get("ops_start_year", 2030)):
            work_segments, ops_added_mwh = apply_ops_program(
                work_segments,
                fuels_df,
                float(strategy.get("ops_fraction", 0.0)),
            )

        profile = calculate_year_profile(work_segments, fuels_df, year, assumptions)
        eua_price = float(assumptions["eua_price"]) * (1.0 + float(assumptions["eua_escalation"])) ** max(0, year - start_year)
        pool_buy_price = float(assumptions["pool_buy_price"]) * (1.0 + float(assumptions["pool_price_escalation"])) ** max(0, year - start_year)
        pool_sell_price = float(assumptions["pool_sell_price"]) * (1.0 + float(assumptions["pool_price_escalation"])) ** max(0, year - start_year)

        raw_balance = float(profile["FuelEU_Balance_tCO2e"])
        effective_balance = raw_balance + carry
        bought_pool = 0.0
        sold_pool = 0.0
        banked = 0.0

        if effective_balance < 0 and pool_deficit:
            cap = float(strategy.get("pool_cap_tco2e", 0.0) or 0.0)
            max_buy = abs(effective_balance) if cap <= 0 else min(abs(effective_balance), cap)
            bought_pool = max_buy
            effective_balance += bought_pool

        deficit_after_pool = max(-effective_balance, 0.0)
        if effective_balance > 0:
            if sell_surplus:
                sell_cap = float(strategy.get("pool_sell_cap_tco2e", 0.0) or 0.0)
                sold_pool = effective_balance if sell_cap <= 0 else min(effective_balance, sell_cap)
                effective_balance -= sold_pool
            if bank_surplus:
                banked = max(effective_balance, 0.0)
            else:
                banked = 0.0

        penalty = fueleu_penalty_eur(deficit_after_pool, float(profile["Attained_gCO2e_MJ"]), float(assumptions["fueleu_penalty_vlsfo"]))
        ets_cost = float(profile["ETS_Covered_tCO2e"]) * eua_price
        pool_cost = bought_pool * pool_buy_price - sold_pool * pool_sell_price
        fuel_cost = float(profile["Fuel_Cost_EUR"])
        compliance_cost = ets_cost + penalty + pool_cost
        total_cost = compliance_cost + fuel_cost
        discount = 1.0 / ((1.0 + float(assumptions["discount_rate"])) ** max(0, year - start_year))

        rows.append(
            {
                "Strategy": str(strategy["name"]),
                "Year": year,
                "Target_gCO2e_MJ": float(profile["Target_gCO2e_MJ"]),
                "Attained_gCO2e_MJ": float(profile["Attained_gCO2e_MJ"]),
                "FuelEU_Balance_raw_tCO2e": raw_balance,
                "CarryIn_tCO2e": carry,
                "Pool_Bought_tCO2e": bought_pool,
                "Pool_Sold_tCO2e": sold_pool,
                "Banked_tCO2e": banked,
                "FuelEU_Deficit_after_pool_tCO2e": deficit_after_pool,
                "FuelEU_Penalty_EUR": penalty,
                "Pool_Net_Cost_EUR": pool_cost,
                "ETS_Covered_tCO2e": float(profile["ETS_Covered_tCO2e"]),
                "ETS_Cost_EUR": ets_cost,
                "Fuel_Cost_EUR": fuel_cost,
                "Compliance_Cost_EUR": compliance_cost,
                "Total_Cost_EUR": total_cost,
                "Discounted_Total_Cost_EUR": total_cost * discount,
                "Discounted_Compliance_Cost_EUR": compliance_cost * discount,
                "RFNBO_Energy_MJ": float(profile["RFNBO_Energy_MJ"]),
                "FuelEU_Energy_MJ": float(profile["FuelEU_Energy_MJ"]),
                "OPS_MWh": float(profile["OPS_MWh"]) + ops_added_mwh,
                "Added_Fuels_t": json.dumps(added_fuels),
                "Fuel_Masses_t": json.dumps(profile["Masses_t"]),
            }
        )
        carry = banked
    return pd.DataFrame(rows)


def make_mix_candidates(selected: List[str], step: float) -> List[Dict[str, float]]:
    selected = selected[:3]
    if not selected:
        return []
    if len(selected) == 1:
        return [{selected[0]: 1.0}]
    grid = np.arange(0.0, 1.0 + 1e-9, step)
    mixes: List[Dict[str, float]] = []
    if len(selected) == 2:
        a, b = selected
        for x in grid:
            if x <= 0 or x >= 1:
                mixes.append({a: float(x), b: float(1 - x)})
            else:
                mixes.append({a: float(x), b: float(1 - x)})
    else:
        a, b, c = selected
        for x in grid:
            for y in grid:
                z = 1.0 - x - y
                if z < -1e-9:
                    continue
                if z < 0:
                    z = 0.0
                if abs(x + y + z - 1.0) <= 1e-6:
                    mixes.append({a: float(x), b: float(y), c: float(z)})
    cleaned = []
    seen = set()
    for mix in mixes:
        clean = {k: round(v, 4) for k, v in mix.items() if v > 1e-6}
        if not clean:
            continue
        key = tuple(sorted(clean.items()))
        if key not in seen:
            cleaned.append(clean)
            seen.add(key)
    return cleaned


def strategy_label(reduce_fuel: str, mix: Dict[str, float], fraction: float, pool: bool, ops: bool) -> str:
    if not mix or fraction <= 0:
        base = "Pool compliance" if pool else "Pay as-is"
    else:
        blend = " + ".join(f"{int(v * 100)}% {k}" for k, v in mix.items())
        base = f"Replace {int(fraction * 100)}% of {reduce_fuel} with {blend}"
    if ops and base != "Pay as-is":
        base += " + OPS"
    elif ops:
        base = "OPS berth program"
    if pool and "Pool" not in base:
        base += " + residual pool"
    return base


def build_strategy_set(
    selected_alts: List[str],
    replace_grid: List[float],
    mix_step: float,
    assumptions: Dict[str, float],
    reduce_fuel: str,
    include_ops: bool,
    pool_cap: float,
) -> List[Dict[str, Any]]:
    start_year = int(assumptions["start_year"])
    ops_start = int(assumptions["ops_start_year"])
    ops_fraction = float(assumptions["ops_fraction"]) if include_ops else 0.0
    strategies: List[Dict[str, Any]] = [
        {
            "name": "Pay as-is",
            "replace_fraction": 0.0,
            "mix": {},
            "reduce_fuel": reduce_fuel,
            "pool_deficit": False,
            "bank_surplus": True,
            "sell_surplus": False,
            "ops_fraction": 0.0,
            "ops_start_year": ops_start,
            "switch_start_year": start_year,
        },
        {
            "name": "Pool compliance",
            "replace_fraction": 0.0,
            "mix": {},
            "reduce_fuel": reduce_fuel,
            "pool_deficit": True,
            "pool_cap_tco2e": pool_cap,
            "bank_surplus": True,
            "sell_surplus": False,
            "ops_fraction": 0.0,
            "ops_start_year": ops_start,
            "switch_start_year": start_year,
        },
    ]
    if include_ops:
        strategies.append(
            {
                "name": "OPS berth program + residual pool",
                "replace_fraction": 0.0,
                "mix": {},
                "reduce_fuel": reduce_fuel,
                "pool_deficit": True,
                "pool_cap_tco2e": pool_cap,
                "bank_surplus": True,
                "sell_surplus": False,
                "ops_fraction": ops_fraction,
                "ops_start_year": ops_start,
                "switch_start_year": start_year,
            }
        )

    mixes = make_mix_candidates(selected_alts, mix_step)
    for mix in mixes:
        for fraction in replace_grid:
            if fraction <= 0:
                continue
            for use_pool in [False, True]:
                name = strategy_label(reduce_fuel, mix, fraction, use_pool, False)
                strategies.append(
                    {
                        "name": name,
                        "replace_fraction": fraction,
                        "mix": mix,
                        "reduce_fuel": reduce_fuel,
                        "pool_deficit": use_pool,
                        "pool_cap_tco2e": pool_cap,
                        "bank_surplus": True,
                        "sell_surplus": False,
                        "ops_fraction": 0.0,
                        "ops_start_year": ops_start,
                        "switch_start_year": start_year,
                    }
                )
                if include_ops:
                    name = strategy_label(reduce_fuel, mix, fraction, use_pool, True)
                    strategies.append(
                        {
                            "name": name,
                            "replace_fraction": fraction,
                            "mix": mix,
                            "reduce_fuel": reduce_fuel,
                            "pool_deficit": use_pool,
                            "pool_cap_tco2e": pool_cap,
                            "bank_surplus": True,
                            "sell_surplus": False,
                            "ops_fraction": ops_fraction,
                            "ops_start_year": ops_start,
                            "switch_start_year": start_year,
                        }
                    )
    unique = []
    seen = set()
    for strategy in strategies:
        key = (
            strategy["name"],
            strategy.get("replace_fraction", 0.0),
            tuple(sorted(strategy.get("mix", {}).items())),
            strategy.get("pool_deficit", False),
            strategy.get("ops_fraction", 0.0),
        )
        if key not in seen:
            unique.append(strategy)
            seen.add(key)
    return unique


@st.cache_data(show_spinner=False, ttl=120)
def evaluate_strategies(
    segments_df: pd.DataFrame,
    fuels_df: pd.DataFrame,
    assumptions: Dict[str, float],
    strategies: List[Dict[str, Any]],
    objective: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    frames = [simulate_strategy(segments_df, fuels_df, assumptions, strategy) for strategy in strategies]
    detail = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if detail.empty:
        return pd.DataFrame(), detail
    group = detail.groupby("Strategy", as_index=False).agg(
        Total_NPV_EUR=("Discounted_Total_Cost_EUR", "sum"),
        Compliance_NPV_EUR=("Discounted_Compliance_Cost_EUR", "sum"),
        FuelEU_Penalty_EUR=("FuelEU_Penalty_EUR", "sum"),
        ETS_Cost_EUR=("ETS_Cost_EUR", "sum"),
        Pool_Net_Cost_EUR=("Pool_Net_Cost_EUR", "sum"),
        Fuel_Cost_EUR=("Fuel_Cost_EUR", "sum"),
        Pool_Bought_tCO2e=("Pool_Bought_tCO2e", "sum"),
        Pool_Sold_tCO2e=("Pool_Sold_tCO2e", "sum"),
        Final_Deficit_tCO2e=("FuelEU_Deficit_after_pool_tCO2e", "sum"),
        Average_Attained_gCO2e_MJ=("Attained_gCO2e_MJ", "mean"),
    )
    sort_col = "Total_NPV_EUR" if objective == "Total cost including fuel" else "Compliance_NPV_EUR"
    group = group.sort_values(sort_col, ascending=True).reset_index(drop=True)
    baseline_value = float(group.loc[group["Strategy"] == "Pay as-is", sort_col].iloc[0]) if "Pay as-is" in group["Strategy"].values else float(group[sort_col].max())
    group["Savings_vs_Pay_As_Is_EUR"] = baseline_value - group[sort_col]
    group["Rank"] = np.arange(1, len(group) + 1)
    return group, detail


def _option_value(value: Any, options: List[Any], fallback: Any) -> Any:
    return value if value in options else fallback


def _bounded_float(value: Any, fallback: float, lower: float, upper: float) -> float:
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        number_value = fallback
    return min(max(number_value, lower), upper)


def _bounded_int(value: Any, fallback: int, lower: int, upper: int) -> int:
    try:
        number_value = int(value)
    except (TypeError, ValueError):
        number_value = fallback
    return min(max(number_value, lower), upper)


def queue_scenario_restore(payload: Dict[str, Any]) -> None:
    st.session_state["_pending_scenario_restore"] = payload


def apply_pending_scenario_restore() -> None:
    payload = st.session_state.pop("_pending_scenario_restore", None)
    if not isinstance(payload, dict):
        return

    restored_fuels = ensure_hsfo_option(pd.DataFrame(payload.get("fuels", default_fuels().to_dict(orient="records"))))
    restored_codes = fuel_codes(restored_fuels)
    restored_segments = sanitize_segments(pd.DataFrame(payload.get("segments", [])), restored_codes)
    assumptions_payload = payload.get("assumptions", {})
    settings_payload = payload.get("settings", {})
    settings = dict(assumptions_payload) if isinstance(assumptions_payload, dict) else {}
    if isinstance(settings_payload, dict):
        settings.update(settings_payload)

    st.session_state["fuels_df"] = restored_fuels
    st.session_state["segments_df"] = restored_segments

    st.session_state["objective"] = _option_value(
        settings.get("objective"),
        ["Total cost including fuel", "Regulatory cost only"],
        "Total cost including fuel",
    )
    start_year_value = _bounded_int(settings.get("start_year"), YEARS[0], YEARS[0], YEARS[-1])
    end_year_value = _bounded_int(settings.get("end_year"), max(2030, start_year_value), start_year_value, YEARS[-1])
    st.session_state["start_year_v2"] = start_year_value
    st.session_state["end_year_v2"] = end_year_value
    st.session_state["discount_rate"] = _bounded_float(settings.get("discount_rate"), 0.08, 0.0, 0.20)
    st.session_state["eua_price"] = max(_bounded_float(settings.get("eua_price"), 82.0, 0.0, 1_000_000.0), 0.0)
    st.session_state["eua_escalation"] = _bounded_float(settings.get("eua_escalation"), 0.03, -0.10, 0.25)
    st.session_state["fuel_escalation"] = _bounded_float(settings.get("fuel_escalation"), 0.02, -0.10, 0.20)
    st.session_state["fueleu_penalty_vlsfo"] = max(_bounded_float(settings.get("fueleu_penalty_vlsfo"), 2400.0, 0.0, 1_000_000.0), 0.0)
    st.session_state["pool_buy_price"] = max(_bounded_float(settings.get("pool_buy_price"), 220.0, 0.0, 1_000_000.0), 0.0)
    st.session_state["pool_sell_price"] = max(_bounded_float(settings.get("pool_sell_price"), 170.0, 0.0, 1_000_000.0), 0.0)
    st.session_state["pool_cap_tco2e"] = max(_bounded_float(settings.get("pool_cap_tco2e"), 0.0, 0.0, 1_000_000_000.0), 0.0)
    st.session_state["pool_price_escalation"] = _bounded_float(settings.get("pool_price_escalation"), 0.02, -0.10, 0.25)

    reduce_options = fossil_codes(restored_fuels) or restored_codes
    st.session_state["reduce_fuel"] = _option_value(settings.get("reduce_fuel"), reduce_options, reduce_options[0])
    candidate_default = [code for code in alt_codes(restored_fuels)[:2] if code in restored_codes]
    saved_candidates = settings.get("candidate_alts", candidate_default)
    if not isinstance(saved_candidates, list):
        saved_candidates = candidate_default
    st.session_state["candidate_alts_raw"] = [code for code in saved_candidates if code in restored_codes]
    st.session_state["max_replace"] = _bounded_float(settings.get("max_replace"), 0.30, 0.0, 1.0)
    st.session_state["replace_step"] = _option_value(
        _bounded_float(settings.get("replace_step"), 0.15, 0.05, 0.25),
        [0.05, 0.10, 0.15, 0.20, 0.25],
        0.15,
    )
    st.session_state["mix_step"] = _option_value(
        _bounded_float(settings.get("mix_step"), 0.50, 0.10, 0.50),
        [0.10, 0.20, 0.25, 0.50],
        0.50,
    )
    st.session_state["include_ops"] = bool(settings.get("include_ops", True))
    st.session_state["ops_start_year"] = _option_value(_bounded_int(settings.get("ops_start_year"), 2030, YEARS[0], YEARS[-1]), YEARS, 2030)
    st.session_state["ops_fraction"] = _bounded_float(settings.get("ops_fraction"), 0.85, 0.0, 1.0)
    st.session_state["gwp_ch4"] = max(_bounded_float(settings.get("gwp_ch4"), 28.0, 0.0, 1_000_000.0), 0.0)
    st.session_state["gwp_n2o"] = max(_bounded_float(settings.get("gwp_n2o"), 265.0, 0.0, 1_000_000.0), 0.0)
    st.session_state["ops_wtw_g_mj"] = max(_bounded_float(settings.get("ops_wtw_g_mj"), 0.0, 0.0, 1_000_000.0), 0.0)

    for key in list(st.session_state.keys()):
        if str(key).startswith("sidebar_price_") or str(key).startswith("fuel_editor") or key == "segment_editor":
            st.session_state.pop(key, None)
    for _, fuel_row in restored_fuels.iterrows():
        st.session_state[f"sidebar_price_{fuel_row['Code']}"] = float(fuel_row["Price_EUR_t"])
    st.session_state["_fuel_editor_revision"] = int(st.session_state.get("_fuel_editor_revision", 0)) + 1
    evaluate_strategies.clear()
    st.session_state["_scenario_restore_message"] = str(payload.get("_scenario_name", "Scenario"))


# =============================================================================
# UI start
# =============================================================================
access_gate()
init_state()
apply_pending_scenario_restore()

st.title(APP_NAME)
st.caption(
    "Decision support for minimizing shipowner FuelEU Maritime and EU ETS cost through fuel choice, pooling, banking, OPS, and EUA exposure."
)

st.markdown(
    f"""
<span class="status-pill">Regulatory baseline: {REGULATORY_CHECK_DATE}</span>
<span class="status-pill">Workspace: {st.session_state.get("subscriber_name", "Demo workspace")}</span>
<span class="status-pill">Plan: {st.session_state.get("subscription_plan", "Trial")}</span>
""",
    unsafe_allow_html=True,
)
scenario_restore_message = st.session_state.pop("_scenario_restore_message", None)
if scenario_restore_message:
    st.success(f"Loaded scenario: {scenario_restore_message}")


# Sidebar controls
with st.sidebar:
    st.header("Commercial Model")
    objective = st.selectbox("Optimization objective", ["Total cost including fuel", "Regulatory cost only"], index=0, key="objective")
    if "start_year_v2" in st.session_state and st.session_state.get("start_year_v2") not in YEARS:
        st.session_state["start_year_v2"] = YEARS[0]
    start_year = st.selectbox(
        "Start year",
        YEARS,
        index=0,
        key="start_year_v2",
        help="Start of the selected period used by all charts and tables except the long-horizon GFI chart.",
    )
    end_year_options = [y for y in YEARS if y >= start_year]
    default_end_year = 2030 if 2030 in end_year_options else end_year_options[-1]
    if "end_year_v2" in st.session_state and st.session_state.get("end_year_v2") not in end_year_options:
        st.session_state["end_year_v2"] = default_end_year
    end_year = st.selectbox(
        "End year",
        end_year_options,
        index=end_year_options.index(default_end_year),
        key="end_year_v2",
        help="End of the selected period used by all charts and tables except the long-horizon GFI chart.",
    )
    st.caption(f"Selected period for cost/pooling/regulatory tables: {int(start_year)}-{int(end_year)}. GFI remains 2025-2050.")
    discount_rate = st.slider(
        "Discount rate",
        0.0,
        0.20,
        0.08,
        0.01,
        key="discount_rate",
        help="Annual rate used to convert future costs to today's value. Higher values make costs far in the future matter less in NPV ranking.",
    )

    st.header("Market Prices")
    eua_price = st.number_input(
        "EUA price [€/tCO2e]",
        min_value=0.0,
        value=82.0,
        step=1.0,
        key="eua_price",
        help="Starting EU Allowance price used for EU ETS cost. ETS cost = covered ETS tCO2e x EUA price.",
    )
    eua_escalation = st.slider(
        "Annual EUA escalation",
        -0.10,
        0.25,
        0.03,
        0.01,
        key="eua_escalation",
        help="Annual percentage change applied to the EUA price after the start year. Example: 3% means the EUA price grows by 3% per year.",
    )
    fuel_escalation = st.slider(
        "Annual fuel price escalation",
        -0.10,
        0.20,
        0.02,
        0.01,
        key="fuel_escalation",
        help="Annual percentage change applied to all fuel prices after the start year. This affects Total cost including fuel.",
    )
    fueleu_penalty_vlsfo = st.number_input(
        "FuelEU penalty [€/VLSFO-eq t]",
        min_value=0.0,
        value=2400.0,
        step=50.0,
        key="fueleu_penalty_vlsfo",
        help="FuelEU deficit penalty input used by the planning model, expressed per VLSFO-equivalent tonne.",
    )

    with st.expander("Fuel prices used in total cost", expanded=False):
        st.caption("These prices feed the Fuel_Cost_EUR component when the objective is Total cost including fuel. The same values are also editable in the Portfolio fuel table.")
        fuels_for_prices = ensure_hsfo_option(st.session_state["fuels_df"]).copy()
        price_changed = False
        for idx, fuel_row in fuels_for_prices.iterrows():
            code = str(fuel_row["Code"])
            new_price = st.number_input(
                f"{code} price [€/t]",
                min_value=0.0,
                value=float(fuel_row["Price_EUR_t"]),
                step=10.0,
                key=f"sidebar_price_{code}",
                help=f"Delivered fuel price for {fuel_row['Fuel']}. Used directly in annual fuel cost and escalated by Annual fuel price escalation.",
            )
            if abs(new_price - float(fuel_row["Price_EUR_t"])) > 1e-9:
                fuels_for_prices.loc[idx, "Price_EUR_t"] = float(new_price)
                price_changed = True
        if price_changed:
            st.session_state["fuels_df"] = ensure_hsfo_option(fuels_for_prices)
            st.session_state["_fuel_editor_revision"] = int(st.session_state.get("_fuel_editor_revision", 0)) + 1
            evaluate_strategies.clear()

    st.header("Pooling Desk")
    pool_buy_price = st.number_input(
        "Pool surplus buy price [€/tCO2e]",
        min_value=0.0,
        value=220.0,
        step=10.0,
        key="pool_buy_price",
        help="Commercial price assumed when buying another vessel's positive FuelEU compliance balance to cover your deficit.",
    )
    pool_sell_price = st.number_input(
        "Pool surplus sell price [€/tCO2e]",
        min_value=0.0,
        value=170.0,
        step=10.0,
        key="pool_sell_price",
        help="Commercial price assumed when selling your positive FuelEU balance to another ship or pool.",
    )
    pool_cap = st.number_input(
        "Pool availability cap [tCO2e/year, 0=unlimited]",
        min_value=0.0,
        value=0.0,
        step=100.0,
        key="pool_cap_tco2e",
        help="Maximum FuelEU surplus that can be bought each year. Use 0 when you want the model to assume unlimited pool availability.",
    )
    pool_price_escalation = st.slider(
        "Annual pool price escalation",
        -0.10,
        0.25,
        0.02,
        0.01,
        key="pool_price_escalation",
        help="Annual percentage change applied to pool buy and sell prices after the start year.",
    )

    st.header("Strategy Search")
    codes_now = fuel_codes(st.session_state["fuels_df"])
    reduce_options = fossil_codes(st.session_state["fuels_df"]) or codes_now
    if "reduce_fuel" in st.session_state and st.session_state.get("reduce_fuel") not in reduce_options:
        st.session_state["reduce_fuel"] = reduce_options[0]
    reduce_fuel = st.selectbox(
        "Fuel to switch from",
        reduce_options,
        index=0,
        key="reduce_fuel",
        help="Choose the fuel whose annual consumption the optimizer may reduce.",
    )
    candidate_defaults = alt_codes(st.session_state["fuels_df"])[:2]
    if "candidate_alts_raw" in st.session_state:
        st.session_state["candidate_alts_raw"] = [code for code in st.session_state["candidate_alts_raw"] if code in codes_now]
    candidate_alts_raw = st.multiselect(
        "Candidate replacement fuels",
        codes_now,
        default=candidate_defaults,
        key="candidate_alts_raw",
        help="All fuels are shown here, including HSFO. If a candidate equals the fuel being switched from, it is ignored because a fuel cannot replace itself.",
    )
    if reduce_fuel in candidate_alts_raw:
        st.warning(f"{reduce_fuel} is already selected as the fuel to switch from, so it is ignored as its own replacement.")
    candidate_alts = [code for code in candidate_alts_raw if code != reduce_fuel]
    st.caption(f"Available fuel options in this selector: {', '.join(codes_now)}")
    max_replace = st.slider(
        "Maximum annual displacement",
        0.0,
        1.0,
        0.30,
        0.05,
        key="max_replace",
        help="Upper limit on the share of the selected 'fuel to switch from' that the optimizer may replace in any year. 0.30 means up to 30%.",
    )
    replace_step = st.select_slider(
        "Displacement grid",
        options=[0.05, 0.10, 0.15, 0.20, 0.25],
        value=0.15,
        key="replace_step",
        help="Resolution for testing replacement percentages. With max 30% and grid 15%, the model tests 15% and 30%. Smaller values are slower but more detailed.",
    )
    mix_step = st.select_slider(
        "Blend search step",
        options=[0.10, 0.20, 0.25, 0.50],
        value=0.50,
        key="mix_step",
        help="Resolution for testing blends between selected replacement fuels. 50% tests coarse blends; 10% tests finer blends and runs slower.",
    )
    st.caption("Default search is intentionally compact for fast first load; tighten the grid for commercial-grade sensitivity runs.")
    include_ops = st.checkbox("Search OPS berth electrification", value=True, key="include_ops")
    if "ops_start_year" in st.session_state and st.session_state.get("ops_start_year") not in YEARS:
        st.session_state["ops_start_year"] = 2030
    ops_start_year = st.selectbox("OPS program starts", YEARS, index=YEARS.index(2030), key="ops_start_year")
    ops_fraction = st.slider("EU berth fuel replaced by OPS", 0.0, 1.0, 0.85, 0.05, key="ops_fraction")

    st.header("Emission Factors")
    gwp_ch4 = st.number_input("GWP100 CH4", min_value=0.0, value=28.0, step=1.0, key="gwp_ch4")
    gwp_n2o = st.number_input("GWP100 N2O", min_value=0.0, value=265.0, step=5.0, key="gwp_n2o")
    ops_wtw_g_mj = st.number_input("OPS electricity WtW [gCO2e/MJ]", min_value=0.0, value=0.0, step=1.0, key="ops_wtw_g_mj")

assumptions = {
    "start_year": int(start_year),
    "end_year": int(end_year),
    "discount_rate": float(discount_rate),
    "eua_price": float(eua_price),
    "eua_escalation": float(eua_escalation),
    "fuel_escalation": float(fuel_escalation),
    "fueleu_penalty_vlsfo": float(fueleu_penalty_vlsfo),
    "pool_buy_price": float(pool_buy_price),
    "pool_sell_price": float(pool_sell_price),
    "pool_price_escalation": float(pool_price_escalation),
    "pool_cap_tco2e": float(pool_cap),
    "gwp_ch4": float(gwp_ch4),
    "gwp_n2o": float(gwp_n2o),
    "ops_wtw_g_mj": float(ops_wtw_g_mj),
    "ops_start_year": int(ops_start_year),
    "ops_fraction": float(ops_fraction),
}
scenario_settings = {
    **assumptions,
    "objective": str(objective),
    "reduce_fuel": str(reduce_fuel),
    "candidate_alts": list(candidate_alts_raw),
    "max_replace": float(max_replace),
    "replace_step": float(replace_step),
    "mix_step": float(mix_step),
    "include_ops": bool(include_ops),
}

tab_portfolio, tab_cockpit, tab_pooling, tab_regulations, tab_subscription = st.tabs(
    ["Portfolio", "Decision Cockpit", "Pooling Desk", "Regulations", "Subscription & Export"]
)

with tab_portfolio:
    st.subheader("Voyage Portfolio")
    st.caption("Enter annualized voyage segments. Fuel quantities are per trip; annual trips scale the model.")

    fuel_editor = st.data_editor(
        st.session_state["fuels_df"],
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_order=[col for col in st.session_state["fuels_df"].columns if col != "Color"],
        column_config={
            "Code": st.column_config.TextColumn("Code", disabled=True),
            "Fuel": st.column_config.TextColumn("Fuel"),
            "Family": st.column_config.SelectboxColumn("Family", options=["Fossil oil", "Fossil gas", "Fossil alcohol", "Certified biofuel", "RFNBO", "Renewable / other"]),
            "LCV_MJ_t": st.column_config.NumberColumn("LCV [MJ/t]", min_value=0.0, step=100.0),
            "WtW_gCO2e_MJ": st.column_config.NumberColumn("WtW [gCO2e/MJ]", min_value=0.0, step=0.5),
            "ETS_CO2_t_t": st.column_config.NumberColumn("ETS CO2 [t/t]", min_value=0.0, step=0.001),
            "ETS_CH4_t_t": st.column_config.NumberColumn("ETS CH4 [t/t]", min_value=0.0, step=0.00001, format="%.6f"),
            "ETS_N2O_t_t": st.column_config.NumberColumn("ETS N2O [t/t]", min_value=0.0, step=0.00001, format="%.6f"),
            "Price_EUR_t": st.column_config.NumberColumn("Price [€/t]", min_value=0.0, step=10.0),
            "Supply_cap_t": st.column_config.NumberColumn("Supply cap [t/year]", min_value=0.0, step=100.0),
            "RFNBO": st.column_config.CheckboxColumn("RFNBO"),
            "ETS_zero_if_certified": st.column_config.CheckboxColumn(
                "ETS zero if certified",
                help="When checked, this fuel's ETS CO2e factor is treated as zero. Use only with valid sustainability certification and evidence.",
            ),
        },
        key=f"fuel_editor_plain_hsfo_{st.session_state.get('_fuel_editor_revision', 0)}",
    )
    st.session_state["fuels_df"] = ensure_hsfo_option(fuel_editor)
    codes_now = fuel_codes(st.session_state["fuels_df"])
    st.session_state["segments_df"] = sanitize_segments(st.session_state["segments_df"], codes_now)

    segment_editor = st.data_editor(
        st.session_state["segments_df"],
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "Segment": st.column_config.TextColumn("Segment"),
            "Route scope": st.column_config.SelectboxColumn("Route scope", options=list(ROUTE_SCOPES.keys()), required=True),
            "Annual trips": st.column_config.NumberColumn("Annual trips", min_value=0.0, step=1.0),
            "Low-carbon first": st.column_config.CheckboxColumn("FuelEU low-carbon first"),
            "OPS_MWh_trip": st.column_config.NumberColumn("OPS [MWh/trip]", min_value=0.0, step=10.0),
            **{
                f"{code}_t_trip": st.column_config.NumberColumn(f"{code} [t/trip]", min_value=0.0, step=1.0)
                for code in codes_now
            },
        },
        key="segment_editor",
    )
    st.session_state["segments_df"] = sanitize_segments(segment_editor, codes_now)

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("Reset portfolio to sample"):
            st.session_state["fuels_df"] = default_fuels()
            st.session_state["segments_df"] = default_segments(fuel_codes(st.session_state["fuels_df"]))
            st.rerun()
    with c2:
        if st.button("Add empty segment"):
            blank = default_segments(codes_now).iloc[[0]].copy()
            blank.loc[:, "Segment"] = "New segment"
            blank.loc[:, "Annual trips"] = 1
            for code in codes_now:
                blank.loc[:, f"{code}_t_trip"] = 0.0
            st.session_state["segments_df"] = pd.concat([st.session_state["segments_df"], blank], ignore_index=True)
            st.rerun()

    current_profile = calculate_year_profile(st.session_state["segments_df"], st.session_state["fuels_df"], int(start_year), assumptions)
    energy = current_profile["Energy_All_MJ"]
    masses = current_profile["Masses_t"]
    overview_df = pd.DataFrame(
        [
            {"Fuel": code, "Annual mass [t]": masses.get(code, 0.0), "Annual energy [GJ]": energy.get(code, 0.0) / 1_000.0}
            for code in codes_now
        ]
    )
    g1, g2 = st.columns(2)
    with g1:
        fig = px.bar(overview_df, x="Fuel", y="Annual mass [t]", title="Annual Fuel Mass")
        st.plotly_chart(style_fig(fig, 360), use_container_width=True)
    with g2:
        fig = px.pie(overview_df, names="Fuel", values="Annual energy [GJ]", title="Energy Mix")
        st.plotly_chart(style_fig(fig, 360), use_container_width=True)

    st.markdown("### Current FuelEU and EU ETS Cost")
    period_years = list(range(int(start_year), int(end_year) + 1))
    st.caption(f"These current-cost charts use the selected period: {int(start_year)}-{int(end_year)}.")
    annual_segment_costs = []
    for yy in period_years:
        yy_costs = current_segment_costs(
            st.session_state["segments_df"],
            st.session_state["fuels_df"],
            int(yy),
            assumptions,
        )
        if not yy_costs.empty:
            yy_costs.insert(0, "Year", int(yy))
            annual_segment_costs.append(yy_costs)
    segment_cost_df = pd.concat(annual_segment_costs, ignore_index=True) if annual_segment_costs else pd.DataFrame()
    if segment_cost_df.empty:
        st.info("Add at least one voyage segment to calculate current FuelEU and EU ETS cost.")
    else:
        total_rows = segment_cost_df.loc[segment_cost_df["Segment"] == "TOTAL"].copy()
        total_summary = total_rows[
            ["FuelEU Cost EUR", "EU ETS Cost EUR", "Total Regulatory Cost EUR", "FuelEU Deficit tCO2e", "ETS Covered tCO2e"]
        ].sum()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Period FuelEU net cost", money(float(total_summary["FuelEU Cost EUR"])))
        m2.metric("Period EU ETS cost", money(float(total_summary["EU ETS Cost EUR"])))
        m3.metric("Period net regulatory total", money(float(total_summary["Total Regulatory Cost EUR"])))
        st.caption(
            "Negative FuelEU deficit and cost values denote a compliance surplus and its "
            "penalty-equivalent benefit; they reduce the net total regulatory cost."
        )
        m4.metric("Period ETS covered emissions", f"{number(float(total_summary['ETS Covered tCO2e']), 0)} tCO2e")

        cost_plot_df = segment_cost_df.loc[segment_cost_df["Segment"] != "TOTAL"].melt(
            id_vars=["Year", "Segment"],
            value_vars=["FuelEU Cost EUR", "EU ETS Cost EUR", "Total Regulatory Cost EUR"],
            var_name="Cost component",
            value_name="EUR",
        )
        fig = px.bar(
            cost_plot_df,
            x="Year",
            y="EUR",
            color="Cost component",
            barmode="group",
            facet_col="Segment",
            facet_col_wrap=2,
            title=f"Current FuelEU and EU ETS Cost by Segment ({int(start_year)}-{int(end_year)})",
            text_auto=".2s",
        )
        fig.update_xaxes(type="category")
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        fig.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(style_fig(fig, 620, legend_right=True), use_container_width=True)

        pay_as_is_annual = simulate_strategy(
            st.session_state["segments_df"],
            st.session_state["fuels_df"],
            assumptions,
            {
                "name": "Pay as-is",
                "replace_fraction": 0.0,
                "mix": {},
                "reduce_fuel": reduce_fuel,
                "pool_deficit": False,
                "bank_surplus": True,
                "sell_surplus": False,
                "ops_fraction": 0.0,
                "ops_start_year": int(ops_start_year),
                "switch_start_year": int(start_year),
            },
        )
        total_cost_yearly = pay_as_is_annual[
            ["Year", "Compliance_Cost_EUR", "Fuel_Cost_EUR", "Pool_Net_Cost_EUR", "Total_Cost_EUR"]
        ].rename(
            columns={
                "Compliance_Cost_EUR": "Regulatory cost",
                "Fuel_Cost_EUR": "Fuel cost",
                "Pool_Net_Cost_EUR": "Pooling cost",
                "Total_Cost_EUR": "Total cost",
            }
        )
        total_cost_plot_df = total_cost_yearly.melt(
            id_vars="Year",
            value_vars=["Regulatory cost", "Fuel cost", "Pooling cost", "Total cost"],
            var_name="Cost component",
            value_name="EUR",
        )
        fig = px.bar(
            total_cost_plot_df,
            x="Year",
            y="EUR",
            color="Cost component",
            barmode="group",
            title=f"Current Total Cost by Year ({int(start_year)}-{int(end_year)})",
            text_auto=".2s",
        )
        fig.update_xaxes(type="category")
        fig.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(style_fig(fig, 460, legend_right=True), use_container_width=True)

        st.dataframe(
            segment_cost_df.sort_values(["Year", "Segment"]).style.format(
                {
                    "FuelEU Cost EUR": "€{:,.0f}",
                    "EU ETS Cost EUR": "€{:,.0f}",
                    "Total Regulatory Cost EUR": "€{:,.0f}",
                    "FuelEU Deficit tCO2e": "{:,.0f}",
                    "ETS Covered tCO2e": "{:,.0f}",
                    "Attained gCO2e/MJ": "{:,.2f}",
                    "FuelEU Limit gCO2e/MJ": "{:,.2f}",
                }
            ),
            use_container_width=True,
        )

    st.markdown("### GFI vs FuelEU Limit")
    st.caption("This chart is intentionally fixed to the full FuelEU horizon, 2025-2050.")
    gfi_years = YEARS
    if gfi_years:
        gfi_df = gfi_yearly_df(
            st.session_state["segments_df"],
            st.session_state["fuels_df"],
            gfi_years,
            assumptions,
        )
        attained_value = float(gfi_df["Attained GFI"].iloc[0]) if not gfi_df.empty else 0.0
        gfi_df["Attained GFI"] = attained_value
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=gfi_df["Year"],
                y=gfi_df["Attained GFI"],
                mode="lines",
                name="Attained GFI",
                line=dict(width=3, color="#0f766e"),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=gfi_df["Year"],
                y=gfi_df["FuelEU GFI Limit"],
                mode="lines",
                name="FuelEU GFI Limit",
                line=dict(width=3, color="#b91c1c", shape="hv"),
            )
        )
        fig.update_layout(title="GFI: Attained vs FuelEU Limit (2025-2050)", yaxis_title="gCO2e/MJ")
        fig.update_xaxes(tickmode="array", tickvals=sorted(FUELEU_REDUCTION_STEPS.keys()))
        st.plotly_chart(style_fig(fig, 450, legend_right=True), use_container_width=True)
    else:
        st.info("Select at least one year for the GFI chart.")


replace_grid = [round(x, 4) for x in np.arange(float(replace_step), float(max_replace) + 1e-9, float(replace_step))]
strategies = build_strategy_set(
    candidate_alts,
    replace_grid,
    float(mix_step),
    assumptions,
    reduce_fuel,
    bool(include_ops),
    float(pool_cap),
)
comparison_df, detail_df = evaluate_strategies(
    st.session_state["segments_df"],
    st.session_state["fuels_df"],
    assumptions,
    strategies,
    objective,
)

with tab_cockpit:
    st.subheader("Decision Cockpit")
    if comparison_df.empty:
        st.warning("No valid strategy could be evaluated. Check the fuel library and voyage portfolio.")
    else:
        sort_col = "Total_NPV_EUR" if objective == "Total cost including fuel" else "Compliance_NPV_EUR"
        best = comparison_df.iloc[0]
        baseline = comparison_df.loc[comparison_df["Strategy"] == "Pay as-is"].iloc[0]
        selected_strategy = st.selectbox(
            "Strategy detail",
            comparison_df["Strategy"].tolist(),
            index=0,
        )
        selected_detail = detail_df[detail_df["Strategy"] == selected_strategy].copy()

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Recommended strategy", str(best["Strategy"])[:34])
        k2.metric("Optimized NPV", money(float(best[sort_col])))
        k3.metric("Savings vs pay as-is", money(float(best["Savings_vs_Pay_As_Is_EUR"])))
        k4.metric("Residual FuelEU deficit", f"{number(float(best['Final_Deficit_tCO2e']), 0)} tCO2e")
        k5.metric("Pool bought", f"{number(float(best['Pool_Bought_tCO2e']), 0)} tCO2e")

        if float(best["Savings_vs_Pay_As_Is_EUR"]) > 0:
            st.markdown(
                f"""
<div class="decision-box">
The model recommends <b>{best["Strategy"]}</b> for the selected horizon. It reduces the selected objective by
<b>{money(float(best["Savings_vs_Pay_As_Is_EUR"]))}</b> versus paying FuelEU/ETS exposure as-is.
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
<div class="risk-box">
The current price set does not justify fuel switching or pooling versus paying as-is. This usually means alternative fuel prices or pooling prices are too high for the selected horizon.
</div>
""",
                unsafe_allow_html=True,
            )

        top = comparison_df.head(10).copy()
        fig = px.bar(
            top.sort_values(sort_col, ascending=True),
            x=sort_col,
            y="Strategy",
            orientation="h",
            title=f"Strategy Ranking by {objective}",
            labels={sort_col: "NPV [EUR]", "Strategy": ""},
        )
        st.plotly_chart(style_fig(fig, 430), use_container_width=True)
        st.caption("Current segment cost and GFI charts are in the Portfolio tab; this cockpit ranks strategy economics.")

        st.markdown("#### Strategy Financial Table")
        show_cols = [
            "Rank",
            "Strategy",
            "Total_NPV_EUR",
            "Compliance_NPV_EUR",
            "Savings_vs_Pay_As_Is_EUR",
            "FuelEU_Penalty_EUR",
            "ETS_Cost_EUR",
            "Pool_Net_Cost_EUR",
            "Fuel_Cost_EUR",
            "Final_Deficit_tCO2e",
        ]
        st.dataframe(
            comparison_df[show_cols].style.format(
                {
                    "Total_NPV_EUR": "€{:,.0f}",
                    "Compliance_NPV_EUR": "€{:,.0f}",
                    "Savings_vs_Pay_As_Is_EUR": "€{:,.0f}",
                    "FuelEU_Penalty_EUR": "€{:,.0f}",
                    "ETS_Cost_EUR": "€{:,.0f}",
                    "Pool_Net_Cost_EUR": "€{:,.0f}",
                    "Fuel_Cost_EUR": "€{:,.0f}",
                    "Final_Deficit_tCO2e": "{:,.0f}",
                }
            ),
            use_container_width=True,
        )

        st.download_button(
            "Download strategy comparison CSV",
            comparison_df.to_csv(index=False),
            "strategy_comparison.csv",
            "text/csv",
        )
        st.download_button(
            "Download selected strategy annual CSV",
            selected_detail.to_csv(index=False),
            "selected_strategy_annual.csv",
            "text/csv",
        )

with tab_pooling:
    st.subheader("Pooling Desk")
    if detail_df.empty:
        st.info("Run a strategy first.")
    else:
        selected_strategy = comparison_df.iloc[0]["Strategy"]
        pool_detail = detail_df[detail_df["Strategy"] == selected_strategy].copy()
        year_choice = st.selectbox("Pooling year", list(range(int(start_year), int(end_year) + 1)), index=0)
        row = pool_detail[pool_detail["Year"] == year_choice].iloc[0]
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Raw balance", f"{number(float(row['FuelEU_Balance_raw_tCO2e']), 0)} tCO2e")
        p2.metric("Pool buy need", f"{number(float(row['Pool_Bought_tCO2e']), 0)} tCO2e")
        p3.metric("Pool net cost", money(float(row["Pool_Net_Cost_EUR"])))
        p4.metric("Banked surplus", f"{number(float(row['Banked_tCO2e']), 0)} tCO2e")

        fig = go.Figure()
        fig.add_trace(go.Bar(x=pool_detail["Year"], y=pool_detail["FuelEU_Balance_raw_tCO2e"], name="Raw FuelEU balance", marker_color="#0f766e"))
        fig.add_trace(go.Bar(x=pool_detail["Year"], y=pool_detail["Pool_Bought_tCO2e"], name="Pool bought", marker_color="#2563eb"))
        fig.add_trace(go.Bar(x=pool_detail["Year"], y=-pool_detail["FuelEU_Deficit_after_pool_tCO2e"], name="Residual deficit", marker_color="#b91c1c"))
        fig.update_layout(title=f"Yearly Pooling Need Under Recommended Strategy: {selected_strategy}", barmode="relative", yaxis_title="tCO2e")
        fig.update_xaxes(type="category")
        st.plotly_chart(style_fig(fig, 430), use_container_width=True)

        st.markdown(
            """
<div class="reg-card">
<h4>Pooling operating rule</h4>
<p>FuelEU pooling is a compliance flexibility mechanism, not a statutory market price. The model treats pool price as a private commercial term and tests whether buying surplus is cheaper than paying the FuelEU deficit penalty or switching fuels.</p>
</div>
""",
            unsafe_allow_html=True,
        )
        st.dataframe(
            pool_detail[
                [
                    "Year",
                    "FuelEU_Balance_raw_tCO2e",
                    "CarryIn_tCO2e",
                    "Pool_Bought_tCO2e",
                    "Pool_Sold_tCO2e",
                    "Banked_tCO2e",
                    "FuelEU_Deficit_after_pool_tCO2e",
                    "Pool_Net_Cost_EUR",
                ]
            ].style.format(
                {
                    "FuelEU_Balance_raw_tCO2e": "{:,.0f}",
                    "CarryIn_tCO2e": "{:,.0f}",
                    "Pool_Bought_tCO2e": "{:,.0f}",
                    "Pool_Sold_tCO2e": "{:,.0f}",
                    "Banked_tCO2e": "{:,.0f}",
                    "FuelEU_Deficit_after_pool_tCO2e": "{:,.0f}",
                    "Pool_Net_Cost_EUR": "€{:,.0f}",
                }
            ),
            use_container_width=True,
        )

with tab_regulations:
    st.subheader("Regulatory Map")
    st.caption("The app includes the rules that directly influence FuelEU and EU ETS cost decisions. Some administrative workflows are shown as obligations but not monetized.")

    timeline = pd.DataFrame(
        [
            {"Year": 2024, "Regulation": "EU ETS / MRV", "Milestone": "Maritime ETS starts for 2024 CO2; MRV expands to CH4/N2O"},
            {"Year": 2025, "Regulation": "FuelEU", "Milestone": "FuelEU GHG intensity starts at 2% reduction"},
            {"Year": 2025, "Regulation": "EU ETS", "Milestone": "70% surrender for 2025 maritime emissions"},
            {"Year": 2026, "Regulation": "EU ETS", "Milestone": "100% surrender; CH4/N2O included in ETS"},
            {"Year": 2026, "Regulation": "FuelEU database", "Milestone": "Implementing Regulation (EU) 2026/394 on FuelEU database"},
            {"Year": 2030, "Regulation": "FuelEU / AFIR", "Milestone": "6% FuelEU target; OPS use for passenger/container ships in covered ports"},
            {"Year": 2034, "Regulation": "FuelEU", "Milestone": "RFNBO reward factor no longer applied in this model; RFNBO subtarget may apply"},
            {"Year": 2035, "Regulation": "FuelEU", "Milestone": "14.5% FuelEU target; OPS expands to all EU ports with capacity"},
            {"Year": 2040, "Regulation": "FuelEU", "Milestone": "31% FuelEU target"},
            {"Year": 2045, "Regulation": "FuelEU", "Milestone": "62% FuelEU target"},
            {"Year": 2050, "Regulation": "FuelEU", "Milestone": "80% FuelEU target"},
        ]
    )
    timeline_period = timeline[(timeline["Year"] >= int(start_year)) & (timeline["Year"] <= int(end_year))].copy()
    if timeline_period.empty:
        st.info(f"No major regulatory timeline milestones in the selected period ({int(start_year)}-{int(end_year)}).")
    else:
        fig = px.scatter(
            timeline_period,
            x="Year",
            y="Regulation",
            color="Regulation",
            size=[18] * len(timeline_period),
            hover_data=["Milestone"],
            title=f"Compliance Timeline ({int(start_year)}-{int(end_year)})",
        )
        fig.update_xaxes(type="category")
        fig.update_traces(marker=dict(line=dict(width=1, color="white")))
        st.plotly_chart(style_fig(fig, 390), use_container_width=True)

    r1, r2 = st.columns(2)
    with r1:
        st.markdown(
            f"""
<div class="reg-card">
<h4>FuelEU Maritime</h4>
<p>Models WtW GHG intensity from the 91.16 gCO2e/MJ reference, the 2025-2050 reduction pathway, RFNBO reward treatment through 2033, banking, and pooling economics. Source: <a href="{REGULATORY_SOURCES["FuelEU Maritime"]}">European Commission FuelEU Maritime</a>.</p>
</div>
<div class="reg-card">
<h4>Pooling, Banking, Borrowing</h4>
<p>Pooling is modeled as a commercial surplus transaction. Banking is modeled as carry-forward of positive compliance balance. Borrowing is shown as a regulation feature but not monetized because it needs verifier and account-level controls. Source: <a href="{REGULATORY_SOURCES["FuelEU Q&A"]}">Commission FuelEU Q&A</a>.</p>
</div>
<div class="reg-card">
<h4>OPS and Zero-Emission Berth</h4>
<p>OPS is modeled as a berth fuel replacement option. Passenger and container ships face use obligations from 2030 in AFIR-covered ports and from 2035 in all EU ports that develop OPS capacity. Source: <a href="{REGULATORY_SOURCES["Alternative Fuels Infrastructure"]}">Alternative fuels infrastructure</a>.</p>
</div>
""",
            unsafe_allow_html=True,
        )
    with r2:
        st.markdown(
            f"""
<div class="reg-card">
<h4>EU ETS Maritime</h4>
<p>Models 100% intra-EU and EU port emissions, 50% EU/non-EU voyage emissions, 70% surrender for 2025 emissions, and 100% from 2026. CH4 and N2O are included from 2026. Source: <a href="{REGULATORY_SOURCES["EU ETS maritime"]}">European Commission ETS maritime</a>.</p>
</div>
<div class="reg-card">
<h4>EU MRV Maritime</h4>
<p>MRV is the reporting foundation for ETS and FuelEU inputs. The revised MRV framework added CH4/N2O monitoring and additional ship types. Source: <a href="{REGULATORY_SOURCES["MRV Regulation (EU) 2023/957"]}">Regulation (EU) 2023/957</a>.</p>
</div>
<div class="reg-card">
<h4>FuelEU Database and Verification</h4>
<p>The app exports decision data, but it is not a verifier workflow. FuelEU database access and technical rules are now covered by Implementing Regulation (EU) 2026/394. Source: <a href="{REGULATORY_SOURCES["FuelEU database Implementing Regulation (EU) 2026/394"]}">EUR-Lex 2026/394</a>.</p>
</div>
""",
            unsafe_allow_html=True,
        )

    obligations = pd.DataFrame(
        [
            {"Rule": "FuelEU GHG intensity", "In cost engine": "Yes", "User action": "Keep fuel evidence and monitor WtW factors"},
            {"Rule": "FuelEU pooling", "In cost engine": "Yes", "User action": "Agree pool and verifier in FuelEU database"},
            {"Rule": "FuelEU banking", "In cost engine": "Yes", "User action": "Verify positive compliance balance before carry-forward"},
            {"Rule": "FuelEU borrowing", "In cost engine": "Shown, not monetized", "User action": "Use only with verifier-controlled account workflow"},
            {"Rule": "FuelEU RFNBO subtarget", "In cost engine": "Flagged, not penalized", "User action": "Check whether the 2034 subtarget applies"},
            {"Rule": "FuelEU OPS penalties", "In cost engine": "Operational switch only", "User action": "Track eligible port calls and exemptions"},
            {"Rule": "EU ETS maritime", "In cost engine": "Yes", "User action": "Surrender EUA by the statutory deadline"},
            {"Rule": "EU MRV", "In cost engine": "Input foundation", "User action": "Align inputs with verified MRV reports"},
            {"Rule": "RED/RFNBO certification", "In cost engine": "Assumption", "User action": "Validate sustainability and certification evidence"},
        ]
    )
    st.dataframe(obligations, use_container_width=True, hide_index=True)

    st.markdown("#### Official Sources")
    for label, url in REGULATORY_SOURCES.items():
        st.markdown(f"- [{label}]({url})")

with tab_subscription:
    st.subheader("Subscription & Export")
    st.caption("This repo is delivered as a subscription-ready product prototype. Production billing should be connected to your payment provider and identity provider.")
    s1, s2, s3 = st.columns(3)
    s1.metric("Tenant", st.session_state.get("subscriber_name", "Demo workspace"))
    s2.metric("Plan", st.session_state.get("subscription_plan", "Trial"))
    s3.metric("Version", APP_VERSION)

    st.markdown(
        """
<div class="reg-card">
<h4>Commercial deployment model</h4>
<p>Use a managed Streamlit or container deployment behind SSO. Put active subscribers in secrets or an external identity provider, store scenarios per tenant, and connect Stripe, Paddle, or your enterprise billing process before public sale.</p>
</div>
""",
        unsafe_allow_html=True,
    )

    scenario_name = st.text_input("Scenario name", value="Shipowner optimization case")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Save scenario"):
            scenario_key = scenario_name.strip()
            if not scenario_key:
                st.error("Enter a scenario name before saving.")
            else:
                payload = {
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "fuels": st.session_state["fuels_df"].to_dict(orient="records"),
                    "segments": st.session_state["segments_df"].to_dict(orient="records"),
                    "assumptions": assumptions,
                    "settings": scenario_settings,
                }
                scenarios = dict(st.session_state.get("scenarios", {}))
                scenarios[scenario_key] = payload
                st.session_state["scenarios"] = scenarios
                if save_json(SCENARIO_PATH, scenarios):
                    st.success("Scenario saved.")
                else:
                    st.error("Could not save scenario file.")
    with c2:
        names = sorted(st.session_state.get("scenarios", {}).keys())
        load_name = st.selectbox("Load scenario", [""] + names)
        if st.button("Load selected", disabled=not bool(load_name)):
            payload = dict(st.session_state["scenarios"][load_name])
            payload["_scenario_name"] = load_name
            queue_scenario_restore(payload)
            st.rerun()
    with c3:
        delete_name = st.selectbox("Delete scenario", [""] + sorted(st.session_state.get("scenarios", {}).keys()))
        if st.button("Delete selected", disabled=not bool(delete_name)):
            if delete_name:
                scenarios = dict(st.session_state["scenarios"])
                scenarios.pop(delete_name, None)
                st.session_state["scenarios"] = scenarios
                save_json(SCENARIO_PATH, scenarios)
                st.success(f"Deleted {delete_name}.")

    uploaded_case = st.file_uploader("Import decision case JSON", type=["json"])
    if st.button("Import uploaded", disabled=uploaded_case is None):
        try:
            imported_payload = json.loads(uploaded_case.getvalue().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            st.error("The uploaded file is not valid JSON.")
        else:
            if isinstance(imported_payload, dict) and "fuels" in imported_payload and "segments" in imported_payload:
                imported_payload["_scenario_name"] = uploaded_case.name
                queue_scenario_restore(imported_payload)
                st.rerun()
            else:
                st.error("The uploaded JSON does not contain a decision case.")

    export_payload = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "regulatory_check_date": REGULATORY_CHECK_DATE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fuels": st.session_state["fuels_df"].to_dict(orient="records"),
        "segments": st.session_state["segments_df"].to_dict(orient="records"),
        "assumptions": assumptions,
        "settings": scenario_settings,
        "comparison": comparison_df.to_dict(orient="records") if not comparison_df.empty else [],
    }
    st.download_button(
        "Download full decision case JSON",
        json.dumps(export_payload, indent=2),
        "maritime_carbon_decision_case.json",
        "application/json",
    )

st.caption(
    "Planning model only. Final compliance must follow the binding legal texts, verified monitoring plans, verifier instructions, and company-approved methodology."
)
st.caption(f"Copyright 2026 {APP_OWNER}. All rights reserved.")
