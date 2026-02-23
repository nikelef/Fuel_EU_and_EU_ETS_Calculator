# app.py
# FuelEU Maritime — Voyage Segments + EU ETS (Maritime) — Multi-Fuel Optimizer & Policy Comparison
# ----------------------------------------------------------------------------------------------
# Single-file Streamlit app. Paste over your existing app.py.
#
# What’s improved vs your previous version:
# - Segments editor now uses st.data_editor (fast, scalable, add/remove rows cleanly).
# - Fuel library supports additional fuels (BIO/RFNBO + 2 custom fuels by default).
# - Optimizer can replace a fossil fuel with 1–3 alternative fuels (BIO, RFNBO, CUSTOM_A, CUSTOM_B, etc.).
# - Banking/carry is computed consistently per scenario (no “base carry-in reuse” artifact).
# - ETS factors are editable per fuel; BIO blend handling preserved for continuity.
# - More robust guards, scenario save/load/export, clearer results & charts.
#
# Notes (important):
# - This is a planning tool. Final compliance must follow the official text + implementing acts + your company method.
# - Default ETS CH4/N2O factors are placeholders (editable in the UI). Tune to your MRV/ETS methodology.
# ----------------------------------------------------------------------------------------------

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple, Optional

import extra_streamlit_components as stx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# =============================================================================
# Page config FIRST
# =============================================================================
st.set_page_config(
    page_title="FuelEU Maritime — Voyage Segments",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- UI environment (clean chrome + compact widgets) ---
st.markdown(
    """
<style>
/* Hide Streamlit toolbar/header decorations */
[data-testid="stToolbar"] { visibility: hidden; height: 0; position: fixed; }
[data-testid="stDecoration"], [data-testid="header"] { display: none; }

/* Hide viewer badge (Streamlit Community Cloud) */
div[class^="viewerBadge_"], div[class*=" viewerBadge_"] { display: none !important; }

/* Typography / spacing */
html, body, [class*="css"]  { font-size: 15px; }
.block-container { padding-top: 1.2rem; padding-bottom: 1.4rem; }

/* Sidebar compact layout */
section[data-testid="stSidebar"] div.block-container{ padding-top:.6rem; padding-bottom:.6rem; }
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{ gap:.75rem; }
section[data-testid="stSidebar"] label{ font-size:.95rem; margin-bottom:.15rem; font-weight:650; }

/* Cards */
section[data-testid="stSidebar"] .card{
    padding:.65rem .75rem;
    border:1px solid #e5e7eb;
    border-radius:.65rem;
    background:#fbfbfb;
}
section[data-testid="stSidebar"] .card h4{
    margin:.10rem 0 .70rem 0;
    font-size:1.02rem;
    font-weight:850;
}
section[data-testid="stSidebar"] .card .help{
    font-size:.86rem;
    color:#6b7280;
    margin:.20rem 0 .80rem 0;
}

/* Metrics */
[data-testid="stMetricLabel"]{ font-weight:800 !important; }
[data-testid="stMetricValue"]{ font-size:.90rem !important; font-weight:750 !important; line-height:1.10 !important; }

/* DataFrame compact */
[data-testid="stDataFrame"]{ font-size:.90rem !important; }

/* Buttons */
.stButton button { border-radius: .55rem; }

/* ETS highlight */
section[data-testid="stSidebar"] .ets-section{
    border:1px solid #bbf7d0;
    background:#f0fdf4;
    border-radius:.65rem;
    padding:.50rem .60rem;
    margin-top:.35rem;
}
</style>
""",
    unsafe_allow_html=True,
)

# =============================================================================
# App metadata
# =============================================================================
APP_OWNER = "Nikitas Eleftheriou"
APP_CONTACT = "ops@example.com"
APP_VERSION = "3.0"
APP_DATE = "2026-02-14"

DEFAULTS_PATH = ".fueleu_defaults.json"
SCENARIOS_PATH = ".fueleu_scenarios.json"

# =============================================================================
# FuelEU core constants
# =============================================================================
BASELINE_2020_GFI = 91.16  # gCO2e/MJ (model baseline)
REDUCTION_STEPS = [
    (2025, 2029, 2.0),
    (2030, 2034, 6.0),
    (2035, 2039, 14.5),
    (2040, 2044, 31.0),
    (2045, 2049, 62.0),
    (2050, 2050, 80.0),
]
YEARS = list(range(2025, 2051))

# Segment types
SEG_TYPES = [
    "Intra-EU voyage",
    "EU→non-EU voyage",
    "non-EU→EU voyage",
    "EU at-berth (port stay)",
]

# Default colors (Plotly will still handle palette if None; these are optional)
COLORS = {
    "ELEC": "#FACC15",
    "RFNBO": "#86EFAC",
    "BIO": "#065F46",
    "CUSTOM_A": "#A78BFA",
    "CUSTOM_B": "#FCA5A5",
    "MGO": "#93C5FD",
    "LFO": "#2563EB",
    "HSFO": "#1E3A8A",
}

# =============================================================================
# EU ETS defaults (TTW) + GWP100 aggregation
# =============================================================================
# CO2: tCO2 per t fuel (defaults for liquid fossil fuels; editable in UI)
EF_TCO2_PER_T_DEFAULT = {"HSFO": 3.114, "LFO": 3.151, "MGO": 3.206}

# Placeholder defaults for non-CO2 TTW factors (t gas / t fuel). Editable in UI.
ETS_NONCO2_EF_DEFAULT = {
    "HSFO": {"CH4": 5e-5, "N2O": 1.8e-4},
    "LFO": {"CH4": 5e-5, "N2O": 1.8e-4},
    "MGO": {"CH4": 5e-5, "N2O": 1.8e-4},
}

# GWP (editable in UI if you wish)
GWP100_CH4_DEFAULT = 28.0
GWP100_N2O_DEFAULT = 265.0


# =============================================================================
# Utility / persistence
# =============================================================================
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _safe_write_json(path: str, data: Dict[str, Any]) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


DEFAULTS = _safe_read_json(DEFAULTS_PATH)
SCENARIOS = _safe_read_json(SCENARIOS_PATH)


def _get(d: Dict[str, Any], key: str, fallback: Any) -> Any:
    return d.get(key, fallback)


def us2(x: Any) -> str:
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return str(x)


def parse_float_any(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return float(default)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def text_input_float(label: str, key: str, default: float, min_value: Optional[float] = None, help: str | None = None) -> float:
    if key not in st.session_state:
        st.session_state[key] = us2(default)

    def _normalize():
        val = parse_float_any(st.session_state[key], default)
        if min_value is not None:
            val = max(val, float(min_value))
        st.session_state[key] = us2(val)

    st.text_input(label, value=st.session_state[key], key=key, on_change=_normalize, help=help)
    val = parse_float_any(st.session_state[key], default)
    if min_value is not None:
        val = max(val, float(min_value))
    return val


def text_input_float_signed(label: str, key: str, default: float, help: str | None = None) -> float:
    if key not in st.session_state:
        st.session_state[key] = us2(default)

    def _normalize():
        val = parse_float_any(st.session_state[key], default)
        st.session_state[key] = us2(val)

    st.text_input(label, value=st.session_state[key], key=key, on_change=_normalize, help=help)
    return parse_float_any(st.session_state[key], default)


def compute_energy_MJ(mass_t: float, lcv_MJ_per_t: float) -> float:
    m = max(float(mass_t), 0.0)
    l = max(float(lcv_MJ_per_t), 0.0)
    return m * l


# =============================================================================
# Login gate (shared creds) — cookie + session fallback
# =============================================================================
@dataclass(frozen=True)
class AuthConfig:
    trial_cookie: str
    session_cookie: str
    expiry_days: int
    username: str
    password: str


_cookie_mgr = stx.CookieManager(key="cookie_mgr")
try:
    _ = _cookie_mgr.get_all()
except Exception:
    pass


def _get_auth_config() -> AuthConfig:
    auth = st.secrets.get("auth", {})
    return AuthConfig(
        trial_cookie=auth.get("trial_cookie_name", "fueleu_trial_id"),
        session_cookie=auth.get("session_cookie_name", "fueleu_session"),
        expiry_days=int(auth.get("cookie_expiry_days", 14)),
        username=auth.get("username", "temp"),
        password=auth.get("password", "1234"),
    )


def _cookie_get(name: str):
    try:
        return _cookie_mgr.get(cookie=name)
    except TypeError:
        try:
            return _cookie_mgr.get(name)
        except Exception:
            return None
    except Exception:
        return None


def _cookie_set(name: str, value: str, *, expires_days: int | None = None) -> bool:
    try:
        if expires_days is None:
            _cookie_mgr.set(name, value, key=f"k-{uuid.uuid4()}")
        else:
            _cookie_mgr.set(
                name,
                value,
                expires_at=datetime.utcnow() + timedelta(days=expires_days),
                key=f"k-{uuid.uuid4()}",
            )
        return True
    except Exception:
        return False


def _cookie_del(name: str) -> bool:
    try:
        _cookie_mgr.delete(name)
        return True
    except Exception:
        return False


def shared_creds_cookie_gate() -> None:
    cfg = _get_auth_config()
    trial_ck = cfg.trial_cookie
    sess_ck = cfg.session_cookie
    expiry_days = cfg.expiry_days

    if "_fallback_logged_in" not in st.session_state:
        st.session_state["_fallback_logged_in"] = False
    if "_fallback_trial_until" not in st.session_state:
        st.session_state["_fallback_trial_until"] = None

    trial_tok = _cookie_get(trial_ck)
    sess_tok = _cookie_get(sess_ck)

    if sess_tok and trial_tok:
        with st.sidebar:
            if st.button("Logout"):
                _cookie_del(sess_ck)
                st.session_state["_fallback_logged_in"] = False
                st.session_state["_fallback_trial_until"] = None
                st.rerun()
        return

    if sess_tok and not trial_tok:
        _cookie_del(sess_ck)
        st.session_state["_fallback_logged_in"] = False
        st.session_state["_fallback_trial_until"] = None
        st.rerun()

    # Fallback session (cookie-less)
    if st.session_state["_fallback_logged_in"]:
        tu = st.session_state["_fallback_trial_until"]
        if isinstance(tu, str):
            try:
                tu_dt = datetime.fromisoformat(tu)
                if tu_dt.tzinfo is None:
                    tu_dt = tu_dt.replace(tzinfo=timezone.utc)
                tu = tu_dt
            except Exception:
                tu = None
        if isinstance(tu, datetime) and _now_utc() < tu:
            with st.sidebar:
                if st.button("Logout"):
                    st.session_state["_fallback_logged_in"] = False
                    st.session_state["_fallback_trial_until"] = None
                    st.rerun()
            return
        st.session_state["_fallback_logged_in"] = False
        st.session_state["_fallback_trial_until"] = None

    st.title("Sign in")
    st.caption("Enter the temporary credentials to access the app.")

    with st.form("login_form", clear_on_submit=False):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign in", type="primary")

    if not submit:
        st.stop()

    if not ((u == cfg.username) and (p == cfg.password)):
        st.error("Invalid credentials.")
        st.stop()

    if not trial_tok:
        _cookie_set(trial_ck, str(uuid.uuid4()), expires_days=expiry_days)
    _cookie_set(sess_ck, str(uuid.uuid4()))

    st.session_state["_fallback_logged_in"] = True
    if not st.session_state.get("_fallback_trial_until"):
        st.session_state["_fallback_trial_until"] = (_now_utc() + timedelta(days=expiry_days)).isoformat()

    st.rerun()


# =============================================================================
# Limits table
# =============================================================================
def limits_by_year() -> pd.DataFrame:
    rows = []
    for y in YEARS:
        perc = next(p for s, e, p in REDUCTION_STEPS if s <= y <= e)
        limit = BASELINE_2020_GFI * (1 - perc / 100.0)
        rows.append({"Year": y, "Reduction_%": perc, "Limit_gCO2e_per_MJ": round(limit, 2)})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def limits_by_year_cached() -> pd.DataFrame:
    return limits_by_year()


LIMITS_DF = limits_by_year_cached()


def _step_of_year(y: int) -> int:
    for i, (s, e, _) in enumerate(REDUCTION_STEPS):
        if s <= y <= e:
            return i
    return -1


# =============================================================================
# Fuel library (editable)
# =============================================================================
@dataclass
class FuelSpec:
    key: str
    display: str
    is_fossil: bool
    is_rfnbo_reward: bool  # eligible for reward factor (r=2 until 2033 in this model)
    lcv_MJ_per_t: float
    wtw_gCO2e_per_MJ: float
    ets_co2_tco2_per_t: float
    ets_ch4_t_per_t: float
    ets_n2o_t_per_t: float
    price_eur_per_t: float  # optional full-price mode
    color: str | None = None


def _default_fuels() -> Dict[str, FuelSpec]:
    # Conservative defaults; user can edit.
    # CUSTOM_A/B default to MGO-like values to avoid accidental “free compliance”.
    return {
        "HSFO": FuelSpec("HSFO", "HSFO", True, False, 40200.0, 91.74,
                         EF_TCO2_PER_T_DEFAULT["HSFO"], ETS_NONCO2_EF_DEFAULT["HSFO"]["CH4"], ETS_NONCO2_EF_DEFAULT["HSFO"]["N2O"],
                         0.0, COLORS.get("HSFO")),
        "LFO": FuelSpec("LFO", "LFO / VLSFO", True, False, 41200.0, 91.39,
                        EF_TCO2_PER_T_DEFAULT["LFO"], ETS_NONCO2_EF_DEFAULT["LFO"]["CH4"], ETS_NONCO2_EF_DEFAULT["LFO"]["N2O"],
                        0.0, COLORS.get("LFO")),
        "MGO": FuelSpec("MGO", "MGO", True, False, 42700.0, 90.77,
                        EF_TCO2_PER_T_DEFAULT["MGO"], ETS_NONCO2_EF_DEFAULT["MGO"]["CH4"], ETS_NONCO2_EF_DEFAULT["MGO"]["N2O"],
                        0.0, COLORS.get("MGO")),
        "BIO": FuelSpec("BIO", "BIO", False, False, 39800.0, 70.37,
                        0.0, 0.0, 0.0,
                        0.0, COLORS.get("BIO")),
        "RFNBO": FuelSpec("RFNBO", "RFNBO", False, True, 30000.0, 20.00,
                          0.0, 0.0, 0.0,
                          0.0, COLORS.get("RFNBO")),
        "CUSTOM_A": FuelSpec("CUSTOM_A", "Custom A", False, False, 42700.0, 90.77,
                             0.0, 0.0, 0.0,
                             0.0, COLORS.get("CUSTOM_A")),
        "CUSTOM_B": FuelSpec("CUSTOM_B", "Custom B", False, False, 42700.0, 90.77,
                             0.0, 0.0, 0.0,
                             0.0, COLORS.get("CUSTOM_B")),
    }


def _load_fuels_from_defaults() -> Dict[str, FuelSpec]:
    base = _default_fuels()
    saved = _get(DEFAULTS, "fuels", {})
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k in base and isinstance(v, dict):
                fs = base[k]
                fs.display = str(v.get("display", fs.display))
                fs.is_fossil = bool(v.get("is_fossil", fs.is_fossil))
                fs.is_rfnbo_reward = bool(v.get("is_rfnbo_reward", fs.is_rfnbo_reward))
                fs.lcv_MJ_per_t = float(v.get("lcv_MJ_per_t", fs.lcv_MJ_per_t) or fs.lcv_MJ_per_t)
                fs.wtw_gCO2e_per_MJ = float(v.get("wtw_gCO2e_per_MJ", fs.wtw_gCO2e_per_MJ) or fs.wtw_gCO2e_per_MJ)
                fs.ets_co2_tco2_per_t = float(v.get("ets_co2_tco2_per_t", fs.ets_co2_tco2_per_t) or fs.ets_co2_tco2_per_t)
                fs.ets_ch4_t_per_t = float(v.get("ets_ch4_t_per_t", fs.ets_ch4_t_per_t) or fs.ets_ch4_t_per_t)
                fs.ets_n2o_t_per_t = float(v.get("ets_n2o_t_per_t", fs.ets_n2o_t_per_t) or fs.ets_n2o_t_per_t)
                fs.price_eur_per_t = float(v.get("price_eur_per_t", fs.price_eur_per_t) or fs.price_eur_per_t)
                fs.color = v.get("color", fs.color)
    return base


FUELS: Dict[str, FuelSpec] = _load_fuels_from_defaults()
FUEL_KEYS = [k for k in FUELS.keys() if k != "ELEC"]  # all fuel keys (non-electric)
FOSSIL_KEYS = [k for k, fs in FUELS.items() if fs.is_fossil]


# =============================================================================
# Segment state & editor
# =============================================================================
def _default_segment_row() -> Dict[str, Any]:
    row = {"type": "Intra-EU voyage", "OPS_kWh": 0.0, "prio_on": True}
    for fk in FUEL_KEYS:
        row[f"{fk}_t"] = 0.0
    return row


def _ensure_segments_state():
    if "segments" not in st.session_state:
        saved = _get(DEFAULTS, "segments", None)
        if isinstance(saved, list) and saved:
            st.session_state["segments"] = saved
        else:
            st.session_state["segments"] = []


def _segments_df_from_state() -> pd.DataFrame:
    rows = st.session_state.get("segments", [])
    if not rows:
        return pd.DataFrame(columns=["type", "prio_on", "OPS_kWh"] + [f"{fk}_t" for fk in FUEL_KEYS])
    df = pd.DataFrame(rows)
    # Ensure all required columns exist
    for col in ["type", "prio_on", "OPS_kWh"] + [f"{fk}_t" for fk in FUEL_KEYS]:
        if col not in df.columns:
            df[col] = 0.0 if col != "type" else "Intra-EU voyage"
    df["type"] = df["type"].fillna("Intra-EU voyage")
    df["prio_on"] = df["prio_on"].fillna(True).astype(bool)
    df["OPS_kWh"] = pd.to_numeric(df["OPS_kWh"], errors="coerce").fillna(0.0)
    for fk in FUEL_KEYS:
        df[f"{fk}_t"] = pd.to_numeric(df[f"{fk}_t"], errors="coerce").fillna(0.0).clip(lower=0.0)
    return df


def _segments_state_from_df(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        st.session_state["segments"] = []
        return
    df2 = df.copy()
    df2["type"] = df2["type"].fillna("Intra-EU voyage")
    df2["prio_on"] = df2.get("prio_on", True).fillna(True).astype(bool)
    df2["OPS_kWh"] = pd.to_numeric(df2.get("OPS_kWh", 0.0), errors="coerce").fillna(0.0).clip(lower=0.0)
    for fk in FUEL_KEYS:
        col = f"{fk}_t"
        df2[col] = pd.to_numeric(df2.get(col, 0.0), errors="coerce").fillna(0.0).clip(lower=0.0)
    st.session_state["segments"] = df2.to_dict(orient="records")


# =============================================================================
# Scope logic (FuelEU)
# =============================================================================
def _segment_energy_mj(seg: Dict[str, Any], fuels: Dict[str, FuelSpec]) -> Dict[str, float]:
    e = {}
    for fk, fs in fuels.items():
        if fk == "ELEC":
            continue
        e[fk] = compute_energy_MJ(float(seg.get(f"{fk}_t", 0.0) or 0.0), fs.lcv_MJ_per_t)
    return e


def prioritized_half_scope_all_fuels(energies_voy: Dict[str, float], fuels: Dict[str, FuelSpec]) -> Dict[str, float]:
    # Cross-border allocator when toggle is ON:
    # - Pool = 50% of TOTAL segment energy (fuels only)
    # - Fill by ascending WtW across ALL fuels until pool full
    pool = 0.5 * sum(energies_voy.values())
    result = {k: 0.0 for k in energies_voy.keys()}
    order = sorted(energies_voy.keys(), key=lambda f: fuels[f].wtw_gCO2e_per_MJ if f in fuels else float("inf"))
    for f in order:
        if pool <= 0:
            break
        take = min(float(energies_voy.get(f, 0.0) or 0.0), pool)
        if take > 0:
            result[f] = take
            pool -= take
    return result


def _segment_scope_with_toggle(seg: Dict[str, Any], energies_all: Dict[str, float], fuels: Dict[str, FuelSpec]) -> Tuple[Dict[str, float], float]:
    """
    Returns (in_scope_fuel_MJ, elec_MJ_segment).
    - Intra-EU: 100%
    - EU berth: 100% + ELEC
    - Cross-border: OFF => 50% each fuel; ON => prioritized pool allocator
    """
    t = str(seg.get("type", "Intra-EU voyage"))
    if t == "Intra-EU voyage":
        return dict(energies_all), 0.0

    if t == "EU at-berth (port stay)":
        elec_mj = float(seg.get("OPS_kWh", 0.0) or 0.0) * 3.6
        return dict(energies_all), elec_mj

    # Cross-border
    prio_on = bool(seg.get("prio_on", True))
    if prio_on:
        return prioritized_half_scope_all_fuels(energies_all, fuels), 0.0

    return {k: 0.5 * energies_all.get(k, 0.0) for k in energies_all.keys()}, 0.0


def _has_prioritized_segments(segments: List[Dict[str, Any]]) -> bool:
    for seg in segments or []:
        t = str(seg.get("type", ""))
        if t in ("EU→non-EU voyage", "non-EU→EU voyage") and bool(seg.get("prio_on", True)):
            return True
    return False


def _global_rearrange_scope(combined_all: Dict[str, float], combined_scope: Dict[str, float], fuels: Dict[str, FuelSpec]) -> Dict[str, float]:
    """
    Global WtW-prioritized reallocation for combined in-scope mix:
    - Keeps total in-scope energy (including ELEC) unchanged
    - ELEC fixed
    - Fuels reassigned by ascending WtW, capped by 100% of each fuel's total energy (combined_all[f])
    """
    scope_total = sum(combined_scope.values())
    if scope_total <= 0.0:
        return dict(combined_scope)

    elec_scope = float(combined_scope.get("ELEC", 0.0) or 0.0)
    fuel_budget = max(scope_total - elec_scope, 0.0)

    result = {k: 0.0 for k in combined_scope.keys()}
    result["ELEC"] = elec_scope
    if fuel_budget <= 0.0:
        return result

    fuel_keys = [k for k in combined_all.keys() if k != "ELEC"]
    fuels_sorted = sorted(fuel_keys, key=lambda f: fuels[f].wtw_gCO2e_per_MJ if f in fuels else float("inf"))
    remaining = fuel_budget

    for f in fuels_sorted:
        if remaining <= 0:
            break
        avail = float(combined_all.get(f, 0.0) or 0.0)
        if avail <= 0:
            continue
        take = min(avail, remaining)
        result[f] = take
        remaining -= take

    # numerical residue
    if remaining > 1e-6:
        for f in reversed(fuels_sorted):
            if result.get(f, 0.0) > 0.0:
                result[f] += remaining
                break

    return result


def scope_from_segments(segments: List[Dict[str, Any]], fuels: Dict[str, FuelSpec]) -> Tuple[float, float, float, Dict[str, float], Dict[str, float]]:
    """
    Returns:
      - E_scope [MJ]
      - num_phys = Σ(E_scope_k * WtW_k)
      - E_rfnbo_scope [MJ] (sum of reward-eligible fuels in-scope)
      - combined_all (MJ)
      - combined_scope_final (MJ) after global rearrangement if prioritized used
    """
    combined_all: Dict[str, float] = {"ELEC": 0.0}
    combined_scope: Dict[str, float] = {"ELEC": 0.0}
    for fk in fuels.keys():
        if fk == "ELEC":
            continue
        combined_all[fk] = 0.0
        combined_scope[fk] = 0.0

    for seg in segments:
        energies_all = _segment_energy_mj(seg, fuels)
        energies_scope, elec_mj = _segment_scope_with_toggle(seg, energies_all, fuels)

        for fk in energies_all.keys():
            combined_all[fk] += float(energies_all.get(fk, 0.0) or 0.0)
            combined_scope[fk] += float(energies_scope.get(fk, 0.0) or 0.0)
        combined_all["ELEC"] += elec_mj
        combined_scope["ELEC"] += elec_mj

    if sum(combined_scope.values()) <= 0:
        return 0.0, 0.0, 0.0, combined_all, combined_scope

    if _has_prioritized_segments(segments):
        combined_scope_final = _global_rearrange_scope(combined_all, combined_scope, fuels)
    else:
        combined_scope_final = combined_scope

    E_scope = sum(combined_scope_final.values())
    num_phys = 0.0
    for k, mj in combined_scope_final.items():
        if k == "ELEC":
            continue
        num_phys += float(mj) * float(fuels[k].wtw_gCO2e_per_MJ)
    # Electricity assumed 0 gCO2e/MJ in this model
    E_rfnbo_scope = 0.0
    for k, mj in combined_scope_final.items():
        if k in fuels and fuels[k].is_rfnbo_reward:
            E_rfnbo_scope += float(mj)

    return E_scope, num_phys, E_rfnbo_scope, combined_all, combined_scope_final


# =============================================================================
# FuelEU balance + € conversion
# =============================================================================
def euros_from_tco2e(balance_tco2e_positive: float, g_att: float, price_eur_per_vlsfo_t: float) -> float:
    """
    Convert tCO2e into EUR using an equivalent VLSFO-tonne price and attained intensity.
    Reference energy: 41,000 MJ/t.
    """
    if balance_tco2e_positive <= 0 or price_eur_per_vlsfo_t <= 0 or g_att <= 0:
        return 0.0
    tco2e_per_vlsfot = (g_att * 41_000.0) / 1_000_000.0
    if tco2e_per_vlsfot <= 0:
        return 0.0
    vlsfo_eq_t = balance_tco2e_positive / tco2e_per_vlsfot
    return vlsfo_eq_t * price_eur_per_vlsfo_t


def fueleu_attained_intensity(year: int, E_scope: float, num_phys: float, E_rfnbo_scope: float) -> float:
    if E_scope <= 0:
        return 0.0
    # Model reward factor
    r = 2.0 if int(year) <= 2033 else 1.0
    den = E_scope + (r - 1.0) * E_rfnbo_scope
    return (num_phys / den) if den > 0 else 0.0


def fueleu_target_intensity(year: int) -> float:
    row = LIMITS_DF[LIMITS_DF["Year"] == int(year)]
    if row.empty:
        return float(LIMITS_DF["Limit_gCO2e_per_MJ"].iloc[0])
    return float(row["Limit_gCO2e_per_MJ"].iloc[0])


def fueleu_balance_tco2e(year: int, g_att: float, g_target: float, E_scope: float, carry_in: float) -> Tuple[float, float]:
    """
    Returns (cb_raw, cb_effective) in tCO2e
    """
    cb_raw = ((g_target - g_att) * E_scope) / 1e6
    cb_eff = cb_raw + float(carry_in)
    return cb_raw, cb_eff


# =============================================================================
# EU ETS calculations (generalized, BIO blend preserved)
# =============================================================================
def ets_coverage_factor(year: int) -> float:
    # Your prior model: 2025=70%, 2026+=100%
    return 0.70 if int(year) == 2025 else 1.00


def ets_geo_emissions_tco2e_from_masses(
    masses_by_fuel: Dict[str, float],
    fuels: Dict[str, FuelSpec],
    year: int,
    gwp_ch4: float,
    gwp_n2o: float,
) -> float:
    """
    Geographic-scope ETS emissions in tCO2e:
    - 2025: CO2 only (per your model)
    - 2026+: CO2 + CH4*GWP100 + N2O*GWP100
    """
    co2 = 0.0
    ch4 = 0.0
    n2o = 0.0

    for fk, m in masses_by_fuel.items():
        if fk not in fuels:
            continue
        fs = fuels[fk]
        m = float(m or 0.0)
        if m <= 0:
            continue

        co2 += m * float(fs.ets_co2_tco2_per_t)
        if int(year) >= 2026:
            ch4 += m * float(fs.ets_ch4_t_per_t)
            n2o += m * float(fs.ets_n2o_t_per_t)

    if int(year) < 2026:
        return co2
    return co2 + ch4 * float(gwp_ch4) + n2o * float(gwp_n2o)


def ets_in_scope_masses_from_segments(
    segments: List[Dict[str, Any]],
    fuels: Dict[str, FuelSpec],
    pure_bio_pct: float,
    bio_mix_type: str,
) -> Dict[str, float]:
    """
    ETS in-scope fuel masses across segments:
      - Intra-EU: 100%
      - EU berth: 100%
      - Cross-border: 50%

    BIO blend handling (kept for continuity):
      - pure_bio_pct% treated as zero-rated BIO (no ETS)
      - fossil share assigned to a selected fossil (Bio Mix Type)
    """
    masses = {fk: 0.0 for fk in fuels.keys() if fk != "ELEC"}

    for seg in segments:
        t = str(seg.get("type", "Intra-EU voyage"))
        scope = 1.0 if t in ("Intra-EU voyage", "EU at-berth (port stay)") else 0.5

        for fk in fuels.keys():
            if fk == "ELEC":
                continue
            masses[fk] += scope * float(seg.get(f"{fk}_t", 0.0) or 0.0)

    # BIO blend fossil-share mapping
    if "BIO" in masses:
        bio_in_scope_t = float(masses["BIO"])
        pure_frac = clamp(float(pure_bio_pct) / 100.0, 0.0, 1.0)
        fossil_share_t = bio_in_scope_t * (1.0 - pure_frac)

        # Remove fossil share from BIO
        masses["BIO"] = bio_in_scope_t * pure_frac

        # Assign fossil share to chosen fossil (HSFO/LFO/MGO) for ETS
        mix_type = (bio_mix_type or "").upper()
        if "HSFO" in mix_type and "HSFO" in masses:
            masses["HSFO"] += fossil_share_t
        elif ("LFO" in mix_type or "VLSFO" in mix_type) and "LFO" in masses:
            masses["LFO"] += fossil_share_t
        elif "MGO" in mix_type and "MGO" in masses:
            masses["MGO"] += fossil_share_t
        else:
            # safe fallback
            if "MGO" in masses:
                masses["MGO"] += fossil_share_t

    return masses


def ets_cost_from_segments(
    segments: List[Dict[str, Any]],
    fuels: Dict[str, FuelSpec],
    pure_bio_pct: float,
    bio_mix_type: str,
    eua_price_eur_per_tco2e: float,
    year: int,
    gwp_ch4: float,
    gwp_n2o: float,
) -> Tuple[float, float]:
    """
    Returns (ETS_emissions_tCO2e_after_coverage, ETS_cost_EUR)
    """
    masses = ets_in_scope_masses_from_segments(segments, fuels, pure_bio_pct, bio_mix_type)
    geo = ets_geo_emissions_tco2e_from_masses(masses, fuels, year, gwp_ch4, gwp_n2o)
    cov = ets_coverage_factor(year)
    em = geo * cov
    cost = em * float(eua_price_eur_per_tco2e)
    return em, cost


# =============================================================================
# Optimizer — multi-fuel shift
# =============================================================================
def _segment_opt_priority(seg: Dict[str, Any]) -> int:
    """
    Remove fossil first where ETS exposure is higher:
      0 -> Intra-EU
      1 -> EU berth
      2 -> Cross-border
      3 -> fallback
    """
    t = str(seg.get("type", ""))
    if t == "Intra-EU voyage":
        return 0
    if t == "EU at-berth (port stay)":
        return 1
    if t in ("non-EU→EU voyage", "EU→non-EU voyage"):
        return 2
    return 3


def apply_shift_multi_to_segments(
    base_segments: List[Dict[str, Any]],
    reduce_fuel: str,
    x_reduce_t: float,
    alt_shares: Dict[str, float],
    fuels: Dict[str, FuelSpec],
) -> Tuple[List[Dict[str, Any]], float, Dict[str, float]]:
    """
    Reduce reduce_fuel by x tonnes and add alternative fuels energy-equivalently in the same segments.
    alt_shares is a dict like {"BIO":0.6,"RFNBO":0.4} (must sum ~1 across included keys).
    Returns (segments_mod, actual_reduced_t, alt_added_t_by_fuel)
    """
    segs = copy.deepcopy(base_segments)
    x = max(0.0, float(x_reduce_t))

    if reduce_fuel not in fuels or reduce_fuel == "ELEC":
        return segs, 0.0, {}

    LCV_SEL = float(fuels[reduce_fuel].lcv_MJ_per_t)
    if LCV_SEL <= 0:
        return segs, 0.0, {}

    # Normalize shares on eligible fuels with LCV>0
    shares = {k: float(v) for k, v in alt_shares.items() if k in fuels and k != "ELEC" and float(v) > 0 and float(fuels[k].lcv_MJ_per_t) > 0}
    if not shares:
        return segs, 0.0, {}
    ssum = sum(shares.values())
    if ssum <= 0:
        return segs, 0.0, {}
    for k in list(shares.keys()):
        shares[k] /= ssum

    total_avail = sum(float(seg.get(f"{reduce_fuel}_t", 0.0) or 0.0) for seg in segs)
    if total_avail <= 0.0:
        return segs, 0.0, {}

    x = min(x, total_avail)
    remaining = x

    indices = list(range(len(segs)))
    indices.sort(key=lambda i: _segment_opt_priority(segs[i]))

    actual_dec = 0.0
    alt_added: Dict[str, float] = {k: 0.0 for k in shares.keys()}

    for i in indices:
        if remaining <= 0:
            break
        seg = segs[i]
        avail = float(seg.get(f"{reduce_fuel}_t", 0.0) or 0.0)
        if avail <= 0:
            continue

        take = min(avail, remaining)
        if take <= 0:
            continue

        seg[f"{reduce_fuel}_t"] = avail - take
        remaining -= take
        actual_dec += take

        removed_energy = take * LCV_SEL  # MJ
        for alt_fuel, sh in shares.items():
            LCV_ALT = float(fuels[alt_fuel].lcv_MJ_per_t)
            add_t = (removed_energy * sh / LCV_ALT) if LCV_ALT > 0 else 0.0
            if add_t > 0:
                seg[f"{alt_fuel}_t"] = float(seg.get(f"{alt_fuel}_t", 0.0) or 0.0) + add_t
                alt_added[alt_fuel] += add_t

    return segs, actual_dec, alt_added


def share_grid(alts: List[str], step: float) -> List[Dict[str, float]]:
    """
    Generates share combinations for up to 3 fuels.
    - if 1 fuel -> [{fuel:1}]
    - if 2 fuels -> p in [0..1] step -> {a:p,b:1-p}
    - if 3 fuels -> simplex grid
    """
    alts = [a for a in alts if a]
    if not alts:
        return []
    if len(alts) == 1:
        return [{alts[0]: 1.0}]
    step = float(step)
    step = 0.05 if step <= 0 else step
    step = min(max(step, 0.05), 0.5)  # reasonable
    combos: List[Dict[str, float]] = []
    if len(alts) == 2:
        a, b = alts
        n = int(round(1.0 / step))
        for i in range(n + 1):
            p = i * step
            p = clamp(p, 0.0, 1.0)
            combos.append({a: p, b: 1.0 - p})
        return combos
    # 3 fuels
    a, b, c = alts[:3]
    n = int(round(1.0 / step))
    for i in range(n + 1):
        pa = i * step
        for j in range(n + 1):
            pb = j * step
            pc = 1.0 - pa - pb
            if pc < -1e-9:
                continue
            pc = max(pc, 0.0)
            # snap
            s = pa + pb + pc
            if s <= 0:
                continue
            combos.append({a: pa / s, b: pb / s, c: pc / s})
    # prune near-duplicates
    uniq = []
    seen = set()
    for d in combos:
        key = tuple(round(d[k], 3) for k in sorted(d.keys()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq


def golden_search_min(f, a: float, b: float, max_iter: int = 80, tol: float = 1e-4) -> Tuple[float, float]:
    """
    Robust 1D minimizer (unimodality not guaranteed; used after coarse bracket).
    Returns (x_best, f_best)
    """
    phi = (5 ** 0.5 - 1) / 2.0
    c = b - phi * (b - a)
    d = a + phi * (b - a)
    fc = f(c)
    fd = f(d)
    it = 0
    while (b - a) > tol and it < max_iter:
        if fc <= fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = f(d)
        it += 1
    x = (a + b) / 2.0
    return x, f(x)


# =============================================================================
# Policy simulation (time-consistent banking)
# =============================================================================
@dataclass
class PolicyConfig:
    # prices
    credit_eur_per_tco2e: float
    penalty_eur_per_vlsfo_t: float
    pooling_price_eur_per_tco2e: float
    eua_price_eur_per_tco2e: float

    # pooling/banking
    pooling_tco2e: float
    pooling_start_year: int
    banking_tco2e: float
    banking_start_year: int

    # penalty multiplier
    penalty_multiplier_mode: str  # "fixed_seed" or "dynamic"
    deficit_seed: int

    # ETS
    pure_bio_pct: float
    bio_mix_type: str
    gwp_ch4: float
    gwp_n2o: float

    # costing
    cost_mode: str  # "incremental" or "full_fuel_costs"
    # incremental: use per-alt premium vs reduced fuel
    # full_fuel_costs: use fs.price_eur_per_t for all fuels

    # optimizer
    reduce_fuel: str
    alt_fuels: List[str]          # list of fuel keys
    share_step: float             # grid step for shares (if 2–3 alts)
    x_steps_coarse: int           # coarse search bins
    max_replace_frac: float       # cap on reduced fuel mass fraction of available
    cap_added_t: Dict[str, float] # per alt fuel added max tonnes (0 => unlimited)
    enable_optimizer: bool


def _penalty_multiplier(year: int, is_deficit: bool, step_idx: int, state: Dict[str, Any], cfg: PolicyConfig) -> float:
    """
    Two modes:
    - fixed_seed: constant per step period when deficit occurs (like your previous seeded multiplier)
    - dynamic: consecutive deficit years increases multiplier (resets on surplus)
    """
    if not is_deficit:
        if cfg.penalty_multiplier_mode == "dynamic":
            state["consec_def"] = 0
        return 1.0

    if cfg.penalty_multiplier_mode == "fixed_seed":
        fixed = state.setdefault("fixed_multiplier_by_step", {})
        if step_idx not in fixed:
            seed = max(int(cfg.deficit_seed), 1)
            fixed[step_idx] = 1.0 + (seed - 1) * 0.10
        return float(fixed[step_idx])

    # dynamic
    state["consec_def"] = int(state.get("consec_def", 0)) + 1
    return 1.0 + (max(state["consec_def"], 1) - 1) * 0.10


def compute_one_year(
    year: int,
    segments: List[Dict[str, Any]],
    fuels: Dict[str, FuelSpec],
    carry_in: float,
    cfg: PolicyConfig,
    opt_override: Optional[Tuple[float, Dict[str, float]]] = None,  # (x_reduce_t, shares)
    premiums_vs_reduce: Optional[Dict[str, float]] = None,          # alt premium €/t vs reduce_fuel (incremental mode)
) -> Dict[str, Any]:
    """
    Computes all key outputs for one year. If opt_override provided, applies shift before computation.
    """
    segs = segments
    x_used = 0.0
    alt_added = {}
    shares_used = {}

    if opt_override is not None:
        x_candidate, shares = opt_override
        segs, x_used, alt_added = apply_shift_multi_to_segments(segs, cfg.reduce_fuel, x_candidate, shares, fuels)
        shares_used = shares

    E_scope, num_phys, E_rfnbo_scope, _, _ = scope_from_segments(segs, fuels)
    g_att = fueleu_attained_intensity(year, E_scope, num_phys, E_rfnbo_scope)
    g_target = fueleu_target_intensity(year)

    cb_raw, cb_eff = fueleu_balance_tco2e(year, g_att, g_target, E_scope, carry_in)

    # pooling
    pool_use = 0.0
    if year >= int(cfg.pooling_start_year):
        if cfg.pooling_tco2e >= 0:
            pre_def = max(-cb_eff, 0.0)
            pool_use = min(cfg.pooling_tco2e, pre_def)
        else:
            provide_abs = abs(cfg.pooling_tco2e)
            pre_sur = max(cb_eff, 0.0)
            pool_use = -min(provide_abs, pre_sur)

    # banking
    bank_use = 0.0
    if year >= int(cfg.banking_start_year):
        req = max(float(cfg.banking_tco2e), 0.0)
        pre_sur = max(cb_eff, 0.0)
        bank_use = min(req, pre_sur)

    # clamp (avoid “impossible” negative final balance due to over-bank/over-provide)
    final_bal = cb_eff + pool_use - bank_use
    if final_bal < 0:
        needed = -final_bal
        trim_bank = min(needed, bank_use)
        bank_use -= trim_bank
        needed -= trim_bank
        if needed > 0 and pool_use < 0:
            pool_use += needed
        final_bal = cb_eff + pool_use - bank_use

    # FuelEU € (credit/penalty)
    step_idx = _step_of_year(year)
    is_deficit = final_bal < 0
    # multiplier handled outside in simulate (needs state); for single-year we return raw and let caller apply if needed
    credit_eur = max(final_bal, 0.0) * float(cfg.credit_eur_per_tco2e)
    penalty_eur_raw = euros_from_tco2e(max(-final_bal, 0.0), max(g_att, 1e-9), float(cfg.penalty_eur_per_vlsfo_t))

    # pooling cost
    pooling_cost_eur = float(pool_use) * float(cfg.pooling_price_eur_per_tco2e)

    # ETS
    ets_em, ets_cost = ets_cost_from_segments(
        segs,
        fuels,
        cfg.pure_bio_pct,
        cfg.bio_mix_type,
        cfg.eua_price_eur_per_tco2e,
        year,
        cfg.gwp_ch4,
        cfg.gwp_n2o,
    )

    # Fuel cost component (two modes)
    fuel_cost_eur = 0.0
    if cfg.cost_mode == "full_fuel_costs":
        # full cost of all fuels consumed (in segs)
        for seg in segs:
            for fk, fs in fuels.items():
                if fk == "ELEC":
                    continue
                fuel_cost_eur += float(seg.get(f"{fk}_t", 0.0) or 0.0) * float(fs.price_eur_per_t)
    else:
        # incremental mode: only cost for alternative fuels added relative to reduced fuel via premiums
        # premiums_vs_reduce: {alt_fuel: premium €/t}
        premiums_vs_reduce = premiums_vs_reduce or {}
        for af, add_t in alt_added.items():
            fuel_cost_eur += float(add_t) * float(premiums_vs_reduce.get(af, 0.0))

    return {
        "Year": year,
        "E_scope_MJ": E_scope,
        "g_target": g_target,
        "g_att": g_att,
        "CB_raw_tCO2e": cb_raw,
        "CarryIn_tCO2e": carry_in,
        "CB_eff_tCO2e": cb_eff,
        "Pooling_tCO2e": pool_use,
        "Banking_tCO2e": bank_use,
        "FinalBalance_tCO2e": final_bal,
        "Credit_EUR": credit_eur,
        "Penalty_EUR_raw": penalty_eur_raw,
        "Pooling_Cost_EUR": pooling_cost_eur,
        "ETS_Emissions_tCO2e": ets_em,
        "ETS_Cost_EUR": ets_cost,
        "Fuel_Cost_EUR": fuel_cost_eur,
        "x_reduce_used_t": x_used,
        "alt_added_t": alt_added,
        "shares_used": shares_used,
        "step_idx": step_idx,
        "is_deficit": is_deficit,
    }


def optimize_one_year(
    year: int,
    segments: List[Dict[str, Any]],
    fuels: Dict[str, FuelSpec],
    carry_in: float,
    cfg: PolicyConfig,
    premiums_vs_reduce: Dict[str, float],
) -> Tuple[float, Dict[str, float], Dict[str, Any]]:
    """
    Chooses (x_reduce_t, shares) minimizing total cost for this year under cfg.
    Returns (x_best, shares_best, metrics_best).
    """
    reduce_fuel = cfg.reduce_fuel
    if reduce_fuel not in fuels or reduce_fuel == "ELEC":
        metrics = compute_one_year(year, segments, fuels, carry_in, cfg, None, premiums_vs_reduce)
        return 0.0, {}, metrics

    # available to reduce
    total_avail = sum(float(seg.get(f"{reduce_fuel}_t", 0.0) or 0.0) for seg in segments)
    if total_avail <= 0:
        metrics = compute_one_year(year, segments, fuels, carry_in, cfg, None, premiums_vs_reduce)
        return 0.0, {}, metrics

    # cap
    max_frac = clamp(float(cfg.max_replace_frac), 0.0, 1.0)
    x_max = total_avail * max_frac

    # alts (max 3)
    alts = [a for a in cfg.alt_fuels if a in fuels and a != reduce_fuel and a != "ELEC"]
    alts = alts[:3]
    if not alts:
        metrics = compute_one_year(year, segments, fuels, carry_in, cfg, None, premiums_vs_reduce)
        return 0.0, {}, metrics

    # share combinations
    combos = share_grid(alts, cfg.share_step)

    # objective
    def total_cost(metrics: Dict[str, Any], mult: float) -> float:
        # apply multiplier to penalty only
        penalty = float(metrics["Penalty_EUR_raw"]) * float(mult)
        return penalty - float(metrics["Credit_EUR"]) + float(metrics["Pooling_Cost_EUR"]) + float(metrics["ETS_Cost_EUR"]) + float(metrics["Fuel_Cost_EUR"])

    best = {"cost": float("inf"), "x": 0.0, "shares": {}, "metrics": None}

    # penalty multiplier state (dynamic needs a state; for per-year optimization we apply multiplier based on cfg.seed only)
    # Use fixed_seed multiplier logic here for stability; dynamic is handled over time in simulate_policy.
    # For per-year optimization: apply seed-based multiplier if deficit.
    def per_year_multiplier(is_deficit: bool) -> float:
        if not is_deficit:
            return 1.0
        seed = max(int(cfg.deficit_seed), 1)
        return 1.0 + (seed - 1) * 0.10

    # coarse bins
    steps = max(int(cfg.x_steps_coarse), 40)
    tol = max(x_max * 1e-5, 1e-4)

    for shares in combos:
        # enforce alt caps (approx by rejecting infeasible points after evaluation)
        def feasible(metrics: Dict[str, Any]) -> bool:
            for af, cap_t in cfg.cap_added_t.items():
                if cap_t and cap_t > 0:
                    add = float(metrics.get("alt_added_t", {}).get(af, 0.0) or 0.0)
                    if add > cap_t + 1e-9:
                        return False
            return True

        # objective in x
        def obj(x: float) -> float:
            m = compute_one_year(year, segments, fuels, carry_in, cfg, (x, shares), premiums_vs_reduce)
            if not feasible(m):
                return 1e30
            mult = per_year_multiplier(bool(m["is_deficit"]))
            return total_cost(m, mult)

        # coarse scan
        best_x = 0.0
        best_c = obj(0.0)
        for i in range(1, steps + 1):
            x = x_max * i / steps
            c = obj(x)
            if c < best_c:
                best_c, best_x = c, x

        # refine around best_x
        bin_w = x_max / steps
        a = max(0.0, best_x - 3 * bin_w)
        b = min(x_max, best_x + 3 * bin_w)

        x_ref, c_ref = golden_search_min(obj, a, b, max_iter=70, tol=tol)

        metrics_ref = compute_one_year(year, segments, fuels, carry_in, cfg, (x_ref, shares), premiums_vs_reduce)
        if not feasible(metrics_ref):
            continue
        mult_ref = per_year_multiplier(bool(metrics_ref["is_deficit"]))
        cost_ref = total_cost(metrics_ref, mult_ref)

        if cost_ref < best["cost"]:
            best = {"cost": cost_ref, "x": x_ref, "shares": shares, "metrics": metrics_ref}

    if best["metrics"] is None:
        metrics = compute_one_year(year, segments, fuels, carry_in, cfg, None, premiums_vs_reduce)
        return 0.0, {}, metrics

    return float(best["x"]), dict(best["shares"]), dict(best["metrics"])


def simulate_policy(
    segments: List[Dict[str, Any]],
    fuels: Dict[str, FuelSpec],
    cfg: PolicyConfig,
    premiums_vs_reduce: Dict[str, float],
) -> pd.DataFrame:
    """
    Simulate 2025–2050 sequentially with scenario-consistent banking/carry.
    If cfg.enable_optimizer is True, optimize year-by-year (greedy) using current carry_in.
    Penalty multiplier:
      - fixed_seed: constant per step for deficits (like old behavior)
      - dynamic: consecutive deficit years ramps up and resets on surplus
    """
    carry = 0.0
    state = {"fixed_multiplier_by_step": {}, "consec_def": 0}
    rows = []

    for y in YEARS:
        if cfg.enable_optimizer:
            x, shares, m = optimize_one_year(y, segments, fuels, carry, cfg, premiums_vs_reduce)
        else:
            x, shares = 0.0, {}
            m = compute_one_year(y, segments, fuels, carry, cfg, None, premiums_vs_reduce)

        mult = _penalty_multiplier(y, bool(m["is_deficit"]), int(m["step_idx"]), state, cfg)
        penalty_eur = float(m["Penalty_EUR_raw"]) * float(mult)

        total_cost = penalty_eur - float(m["Credit_EUR"]) + float(m["Pooling_Cost_EUR"]) + float(m["ETS_Cost_EUR"]) + float(m["Fuel_Cost_EUR"])

        rows.append({
            "Year": y,
            "Reduction_%": float(LIMITS_DF[LIMITS_DF["Year"] == y]["Reduction_%"].iloc[0]),
            "Limit_gCO2e_per_MJ": float(m["g_target"]),
            "Attained_gCO2e_per_MJ": float(m["g_att"]),
            "E_scope_MJ": float(m["E_scope_MJ"]),
            "CB_raw_tCO2e": float(m["CB_raw_tCO2e"]),
            "CarryIn_tCO2e": float(m["CarryIn_tCO2e"]),
            "CB_eff_tCO2e": float(m["CB_eff_tCO2e"]),
            "Pooling_tCO2e": float(m["Pooling_tCO2e"]),
            "Banked_to_Next_tCO2e": float(m["Banking_tCO2e"]),
            "FinalBalance_tCO2e": float(m["FinalBalance_tCO2e"]),
            "Penalty_Multiplier": float(mult),
            "Penalty_EUR": float(penalty_eur),
            "Credit_EUR": float(m["Credit_EUR"]),
            "Pooling_Cost_EUR": float(m["Pooling_Cost_EUR"]),
            "ETS_Emissions_tCO2e": float(m["ETS_Emissions_tCO2e"]),
            "ETS_Cost_EUR": float(m["ETS_Cost_EUR"]),
            "Fuel_Cost_EUR": float(m["Fuel_Cost_EUR"]),
            "Total_Cost_EUR": float(total_cost),
            "x_reduce_used_t": float(m.get("x_reduce_used_t", 0.0) or 0.0),
            "shares_used": json.dumps(m.get("shares_used", {}) or {}),
            "alt_added_t": json.dumps(m.get("alt_added_t", {}) or {}),
        })

        # carry to next year = banked amount
        carry = float(m["Banking_tCO2e"])

    return pd.DataFrame(rows)


# =============================================================================
# About / footer
# =============================================================================
def show_trial_header(owner_name: str, contact_email: str, version: str, date_str: str) -> None:
    with st.expander("About, Terms & Privacy", expanded=False):
        st.markdown(
            f"""
**About.** FuelEU Maritime calculator & optimizer (public trial).  
**Status.** Non-production demo for evaluation only (temporary credentials).  
**Ownership.** © {date_str.split('-')[0]} {owner_name}. All rights reserved. Access only — code not distributed.  
**No warranty.** Provided “as is”; results may contain errors.  
**No advice.** Not legal, regulatory, or financial advice.  
**Privacy.** No personal data is stored by this app; minimal anonymous usage logs may be kept by the hosting provider for reliability.  
**Contact.** {contact_email}  
**Third-party.** Built with Streamlit, Plotly, Pandas, and open-source libraries. Trademarks belong to their owners.
"""
        )


def show_trial_footer(owner_name: str, version: str, date_str: str) -> None:
    st.caption(f"© {date_str.split('-')[0]} {owner_name}. All rights reserved. v{version} ({date_str})")


# =============================================================================
# Gate (must run before app UI)
# =============================================================================
shared_creds_cookie_gate()

# =============================================================================
# Header
# =============================================================================
st.title("FuelEU & EU ETS — Voyage Segments — Maritime")
st.caption("2025–2050 • WtW intensity • Pooling/Banking • EU ETS maritime (CO₂e from 2026+) • Multi-fuel optimizer & policy comparison")
show_trial_header(APP_OWNER, APP_CONTACT, APP_VERSION, APP_DATE)

# =============================================================================
# Regulatory section (high-level)
# =============================================================================
with st.expander("Regulatory basis (high-level)", expanded=False):
    st.markdown(
        """
This app implements a **practical, operator-friendly** representation of:

**FuelEU Maritime**
- GHG intensity framework using **Well-to-Wake (WtW)** intensities and energy (MJ) via LCV.
- Stepwise limit pathway (2025–2050) applied against a 2020 baseline.
- RFNBO reward factor in this model: **2 until 2033**, then **1 from 2034** (editable only by changing the model).

**EU ETS (maritime extension)**
- Geographic scope logic:
  - **Intra-EU** and **EU at-berth**: 100%
  - **Cross-border EU↔non-EU**: 50%
- Coverage factor in this model:
  - **2025:** 70%
  - **2026+:** 100%
- Emissions accounted as **tCO₂e** from 2026 onward (CO₂ + CH₄ + N₂O using GWP100 inputs),
  while 2025 is treated as **CO₂ only** in this model.

**Important**
- Defaults for CH₄ and N₂O factors are placeholders; edit them to match your chosen MRV/ETS methodology.
- Final compliance and reporting must follow the final legal texts, implementing acts, and company-approved methods.
"""
    )

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    _ensure_segments_state()

    # Scenario manager
    st.markdown('<div class="card"><h4>Scenario manager</h4><div class="help">Save/load full input sets (segments + parameters). Use for “Base”, “High EUA”, “Alt fuels”, etc.</div>', unsafe_allow_html=True)
    scenario_names = sorted(list(SCENARIOS.keys()))
    pick = st.selectbox("Load scenario", options=["(none)"] + scenario_names, index=0)

    csc1, csc2, csc3 = st.columns(3)
    with csc1:
        if st.button("Load", disabled=(pick == "(none)")):
            payload = SCENARIOS.get(pick, {})
            if isinstance(payload, dict):
                st.session_state["segments"] = payload.get("segments", st.session_state.get("segments", []))
                for k, v in payload.get("session_state", {}).items():
                    st.session_state[k] = v
                st.success(f"Loaded: {pick}")
                st.rerun()
    with csc2:
        name_to_save = st.text_input("Save as", value="", label_visibility="collapsed", placeholder="Name…")
    with csc3:
        if st.button("Save"):
            nm = (name_to_save or "").strip()
            if not nm:
                st.warning("Enter a scenario name.")
            else:
                # Save selected UI keys (keep it controlled)
                session_keys = [
                    # pricing/policy
                    "credit_eur_per_tco2e", "penalty_eur_per_vlsfo_t",
                    "pooling_price_eur_per_tco2e", "pooling_tco2e", "pooling_start_year",
                    "banking_tco2e", "banking_start_year",
                    "eua_price_eur_per_tco2e",
                    "cost_mode", "penalty_multiplier_mode", "deficit_seed",
                    # ETS/bio
                    "pure_bio_pct", "bio_mix_type", "gwp_ch4", "gwp_n2o",
                    # optimizer
                    "reduce_fuel", "alt_fuels", "share_step", "x_steps_coarse", "max_replace_frac",
                    "opt_enabled",
                    # premiums
                    "prem_BIO", "prem_RFNBO", "prem_CUSTOM_A", "prem_CUSTOM_B",
                    # caps
                    "cap_BIO", "cap_RFNBO", "cap_CUSTOM_A", "cap_CUSTOM_B",
                    # fuel library
                    "fuels_editor_blob",
                ]
                SCENARIOS[nm] = {
                    "segments": st.session_state.get("segments", []),
                    "session_state": {k: st.session_state.get(k) for k in session_keys if k in st.session_state},
                    "saved_at_utc": _now_utc().isoformat(),
                }
                if _safe_write_json(SCENARIOS_PATH, SCENARIOS):
                    st.success(f"Saved: {nm}")
                else:
                    st.error("Could not save scenarios file.")
    with st.expander("Import / Export / Delete", expanded=False):
        if scenario_names:
            del_name = st.selectbox("Delete scenario", options=["(none)"] + scenario_names, index=0, key="del_scn")
            if st.button("Delete", disabled=(del_name == "(none)")):
                SCENARIOS.pop(del_name, None)
                _safe_write_json(SCENARIOS_PATH, SCENARIOS)
                st.success(f"Deleted: {del_name}")
                st.rerun()

        st.download_button(
            "Export all scenarios (JSON)",
            data=json.dumps(SCENARIOS, indent=2),
            file_name="fueleu_scenarios.json",
            mime="application/json",
        )
        up = st.file_uploader("Import scenarios JSON", type=["json"], accept_multiple_files=False)
        if up is not None:
            try:
                incoming = json.loads(up.read().decode("utf-8"))
                if isinstance(incoming, dict):
                    SCENARIOS.update(incoming)
                    _safe_write_json(SCENARIOS_PATH, SCENARIOS)
                    st.success("Imported scenarios.")
                    st.rerun()
                else:
                    st.error("Invalid JSON structure.")
            except Exception as e:
                st.error(f"Import failed: {e}")

    st.markdown("</div>", unsafe_allow_html=True)

    # Quick actions
    st.markdown('<div class="card"><h4>Quick actions</h4>', unsafe_allow_html=True)
    qa1, qa2 = st.columns(2)
    with qa1:
        if st.button("➕ Add segment"):
            st.session_state["segments"].append(_default_segment_row())
            st.rerun()
    with qa2:
        if st.button("🧹 Clear"):
            st.session_state["segments"] = []
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # Fuel library editor (compact)
    st.markdown('<div class="card"><h4>Fuel library</h4><div class="help">Edit names/LCV/WtW/ETS factors. RFNBO “reward eligible” determines the r-factor denominator adjustment.</div>', unsafe_allow_html=True)

    # Keep fuel edits in one blob for scenario save/load stability
    if "fuels_editor_blob" not in st.session_state:
        st.session_state["fuels_editor_blob"] = json.dumps({k: fs.__dict__ for k, fs in FUELS.items()}, indent=2)

    with st.expander("Edit fuels (advanced)", expanded=False):
        blob = st.text_area("Fuel specs (JSON)", value=st.session_state["fuels_editor_blob"], height=260)
        cfb1, cfb2 = st.columns(2)
        with cfb1:
            if st.button("Apply fuel edits"):
                try:
                    obj = json.loads(blob)
                    if not isinstance(obj, dict):
                        raise ValueError("Root must be a dict.")
                    for k, v in obj.items():
                        if k in FUELS and isinstance(v, dict):
                            FUELS[k].display = str(v.get("display", FUELS[k].display))
                            FUELS[k].is_fossil = bool(v.get("is_fossil", FUELS[k].is_fossil))
                            FUELS[k].is_rfnbo_reward = bool(v.get("is_rfnbo_reward", FUELS[k].is_rfnbo_reward))
                            FUELS[k].lcv_MJ_per_t = float(v.get("lcv_MJ_per_t", FUELS[k].lcv_MJ_per_t))
                            FUELS[k].wtw_gCO2e_per_MJ = float(v.get("wtw_gCO2e_per_MJ", FUELS[k].wtw_gCO2e_per_MJ))
                            FUELS[k].ets_co2_tco2_per_t = float(v.get("ets_co2_tco2_per_t", FUELS[k].ets_co2_tco2_per_t))
                            FUELS[k].ets_ch4_t_per_t = float(v.get("ets_ch4_t_per_t", FUELS[k].ets_ch4_t_per_t))
                            FUELS[k].ets_n2o_t_per_t = float(v.get("ets_n2o_t_per_t", FUELS[k].ets_n2o_t_per_t))
                            FUELS[k].price_eur_per_t = float(v.get("price_eur_per_t", FUELS[k].price_eur_per_t))
                            FUELS[k].color = v.get("color", FUELS[k].color)
                    st.session_state["fuels_editor_blob"] = json.dumps({k: fs.__dict__ for k, fs in FUELS.items()}, indent=2)
                    st.success("Fuel edits applied (session).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Invalid JSON: {e}")
        with cfb2:
            if st.button("Reset fuel defaults"):
                FUELS = _default_fuels()  # local rebind only
                st.session_state["fuels_editor_blob"] = json.dumps({k: fs.__dict__ for k, fs in FUELS.items()}, indent=2)
                st.success("Reset to defaults (session).")
                st.rerun()

    # Quick visible inputs (common)
    st.markdown("**LCV & WtW (common)**")
    for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO", "CUSTOM_A", "CUSTOM_B"]:
        if k not in FUELS:
            continue
        fs = FUELS[k]
        c1, c2 = st.columns(2)
        with c1:
            fs.lcv_MJ_per_t = text_input_float(f"{fs.display} LCV [MJ/t]", f"LCV_{k}", _get(DEFAULTS, f"LCV_{k}", fs.lcv_MJ_per_t), min_value=0.0)
        with c2:
            fs.wtw_gCO2e_per_MJ = text_input_float(f"{fs.display} WtW [gCO₂e/MJ]", f"WTW_{k}", _get(DEFAULTS, f"WTW_{k}", fs.wtw_gCO2e_per_MJ), min_value=0.0)

    st.markdown("</div>", unsafe_allow_html=True)

    # ETS/bio parameters
    st.markdown('<div class="ets-section"><b>EU ETS & BIO blend</b>', unsafe_allow_html=True)
    pure_bio_pct = st.number_input("Pure BIO in delivered blend (%)", 0.0, 100.0, float(_get(DEFAULTS, "pure_bio_pct", 100.0)), 1.0, key="pure_bio_pct")
    bio_mix_type = st.selectbox(
        "Bio fossil-share assigned to",
        options=["BIO/HSFO mix", "BIO/LFO mix", "BIO/MGO mix"],
        index=int(_get(DEFAULTS, "bio_mix_type_idx", 1)),
        key="bio_mix_type",
    )
    gwp_ch4 = st.number_input("GWP100 CH₄ (2026+)", value=float(_get(DEFAULTS, "gwp_ch4", GWP100_CH4_DEFAULT)), step=1.0, key="gwp_ch4")
    gwp_n2o = st.number_input("GWP100 N₂O (2026+)", value=float(_get(DEFAULTS, "gwp_n2o", GWP100_N2O_DEFAULT)), step=1.0, key="gwp_n2o")
    st.markdown("</div>", unsafe_allow_html=True)

    # Policy / prices
    st.markdown('<div class="card"><h4>Prices & policy</h4>', unsafe_allow_html=True)
    credit_eur_per_tco2e = text_input_float("FuelEU credit price [€/tCO₂e]", "credit_eur_per_tco2e", float(_get(DEFAULTS, "credit_eur_per_tco2e", 200.0)), min_value=0.0)
    penalty_eur_per_vlsfo_t = text_input_float("FuelEU penalty price [€/VLSFO-eq t]", "penalty_eur_per_vlsfo_t", float(_get(DEFAULTS, "penalty_eur_per_vlsfo_t", 2400.0)), min_value=0.0)
    eua_price_eur_per_tco2e = text_input_float("EUA price [€/tCO₂e]", "eua_price_eur_per_tco2e", float(_get(DEFAULTS, "eua_price_eur_per_tco2e", 87.0)), min_value=0.0)

    cost_mode = st.selectbox(
        "Fuel cost mode",
        options=["incremental", "full_fuel_costs"],
        index=0 if _get(DEFAULTS, "cost_mode", "incremental") == "incremental" else 1,
        key="cost_mode",
        help="incremental: only premiums of added alternative fuels vs reduced fuel. full_fuel_costs: uses €/t per fuel (set in fuel JSON).",
    )

    pooling_price_eur_per_tco2e = text_input_float("Pooling price [€/tCO₂e]", "pooling_price_eur_per_tco2e", float(_get(DEFAULTS, "pooling_price_eur_per_tco2e", 200.0)), min_value=0.0)
    pooling_tco2e = text_input_float_signed("Pooling [tCO₂e] (+ uptake / − provide)", "pooling_tco2e", float(_get(DEFAULTS, "pooling_tco2e", 0.0)))
    pooling_start_year = st.selectbox("Pooling starts", YEARS, index=YEARS.index(int(_get(DEFAULTS, "pooling_start_year", 2025))), key="pooling_start_year")

    banking_tco2e = text_input_float("Banking to next year [tCO₂e]", "banking_tco2e", float(_get(DEFAULTS, "banking_tco2e", 0.0)), min_value=0.0)
    banking_start_year = st.selectbox("Banking starts", YEARS, index=YEARS.index(int(_get(DEFAULTS, "banking_start_year", 2025))), key="banking_start_year")

    penalty_multiplier_mode = st.selectbox(
        "Penalty multiplier mode",
        options=["fixed_seed", "dynamic"],
        index=0 if _get(DEFAULTS, "penalty_multiplier_mode", "fixed_seed") == "fixed_seed" else 1,
        key="penalty_multiplier_mode",
        help="fixed_seed: constant per step period if deficit (seed-based). dynamic: increases with consecutive deficit years and resets on surplus.",
    )
    deficit_seed = int(st.number_input("Deficit years seed", min_value=1, value=int(_get(DEFAULTS, "deficit_seed", 1)), step=1, key="deficit_seed"))

    st.markdown("</div>", unsafe_allow_html=True)

    # Optimizer controls (multi-fuel)
    st.markdown('<div class="card"><h4>Optimizer (multi-fuel)</h4><div class="help">Reduce one fossil fuel and replace energy-equivalently with 1–3 alternative fuels. The optimizer minimizes FuelEU + ETS + pooling + fuel-cost (per chosen mode).</div>', unsafe_allow_html=True)

    opt_enabled = st.checkbox("Enable optimizer scenario", value=bool(_get(DEFAULTS, "opt_enabled", True)), key="opt_enabled")

    reduce_fuel = st.selectbox("Reduce fuel", options=FOSSIL_KEYS, index=0, key="reduce_fuel")
    alt_fuels = st.multiselect(
        "Alternative fuels (choose 1–3)",
        options=[k for k in FUELS.keys() if k != "ELEC" and k != reduce_fuel],
        default=_get(DEFAULTS, "alt_fuels", ["BIO"]),
        key="alt_fuels",
    )
    share_step = st.select_slider("Blend share grid step (if 2–3 fuels)", options=[0.05, 0.10, 0.20, 0.25, 0.33, 0.50],
                                  value=float(_get(DEFAULTS, "share_step", 0.10)), key="share_step")
    x_steps_coarse = int(st.select_slider("Optimizer resolution (coarse bins)", options=[40, 60, 80, 120, 160], value=int(_get(DEFAULTS, "x_steps_coarse", 80)), key="x_steps_coarse"))
    max_replace_frac = float(st.slider("Max fraction of reducible fuel to replace", 0.0, 1.0, float(_get(DEFAULTS, "max_replace_frac", 1.0)), 0.05, key="max_replace_frac"))

    st.markdown("**Premiums vs reduced fuel (incremental mode)**")
    prem_inputs = {}
    for fk in ["BIO", "RFNBO", "CUSTOM_A", "CUSTOM_B"]:
        if fk in FUELS:
            prem_inputs[fk] = text_input_float(f"{FUELS[fk].display} premium [€/t]", f"prem_{fk}", float(_get(DEFAULTS, f"prem_{fk}", 0.0)), min_value=0.0)

    st.markdown("**Caps on added alternative fuels (optional)**")
    cap_added = {}
    for fk in ["BIO", "RFNBO", "CUSTOM_A", "CUSTOM_B"]:
        if fk in FUELS:
            cap_added[fk] = text_input_float(f"Max added {FUELS[fk].display} [t] (0=unlimited)", f"cap_{fk}", float(_get(DEFAULTS, f"cap_{fk}", 0.0)), min_value=0.0)

    st.markdown("</div>", unsafe_allow_html=True)

    # Save defaults
    if st.button("💾 Save current inputs as defaults"):
        defaults_to_save = {
            # segments
            "segments": st.session_state.get("segments", []),
            # fuels
            "fuels": {k: fs.__dict__ for k, fs in FUELS.items()},
            # LCV/WtW quick inputs
            **{f"LCV_{k}": FUELS[k].lcv_MJ_per_t for k in FUELS.keys()},
            **{f"WTW_{k}": FUELS[k].wtw_gCO2e_per_MJ for k in FUELS.keys()},
            # ETS/bio
            "pure_bio_pct": float(pure_bio_pct),
            "bio_mix_type_idx": int(["BIO/HSFO mix", "BIO/LFO mix", "BIO/MGO mix"].index(bio_mix_type)),
            "gwp_ch4": float(gwp_ch4),
            "gwp_n2o": float(gwp_n2o),
            # policy
            "credit_eur_per_tco2e": float(credit_eur_per_tco2e),
            "penalty_eur_per_vlsfo_t": float(penalty_eur_per_vlsfo_t),
            "eua_price_eur_per_tco2e": float(eua_price_eur_per_tco2e),
            "cost_mode": cost_mode,
            "pooling_price_eur_per_tco2e": float(pooling_price_eur_per_tco2e),
            "pooling_tco2e": float(pooling_tco2e),
            "pooling_start_year": int(pooling_start_year),
            "banking_tco2e": float(banking_tco2e),
            "banking_start_year": int(banking_start_year),
            "penalty_multiplier_mode": penalty_multiplier_mode,
            "deficit_seed": int(deficit_seed),
            # optimizer
            "opt_enabled": bool(opt_enabled),
            "reduce_fuel": reduce_fuel,
            "alt_fuels": alt_fuels,
            "share_step": float(share_step),
            "x_steps_coarse": int(x_steps_coarse),
            "max_replace_frac": float(max_replace_frac),
            # premiums/caps
            **{f"prem_{k}": float(v) for k, v in prem_inputs.items()},
            **{f"cap_{k}": float(v) for k, v in cap_added.items()},
        }
        if _safe_write_json(DEFAULTS_PATH, defaults_to_save):
            st.success("Defaults saved.")
        else:
            st.error("Could not save defaults.")


# =============================================================================
# Main tabs
# =============================================================================
tab_overview, tab_segments, tab_results, tab_sim = st.tabs(
    ["Overview", "Segments", "Results & Policies", "Simulation"]
)

segments_df = _segments_df_from_state()
segments = segments_df.to_dict(orient="records")

# Build config objects
premiums_vs_reduce = {k: float(v) for k, v in prem_inputs.items()}

base_cfg = PolicyConfig(
    credit_eur_per_tco2e=float(credit_eur_per_tco2e),
    penalty_eur_per_vlsfo_t=float(penalty_eur_per_vlsfo_t),
    pooling_price_eur_per_tco2e=float(pooling_price_eur_per_tco2e),
    eua_price_eur_per_tco2e=float(eua_price_eur_per_tco2e),
    pooling_tco2e=float(pooling_tco2e),
    pooling_start_year=int(pooling_start_year),
    banking_tco2e=float(banking_tco2e),
    banking_start_year=int(banking_start_year),
    penalty_multiplier_mode=str(penalty_multiplier_mode),
    deficit_seed=int(deficit_seed),
    pure_bio_pct=float(pure_bio_pct),
    bio_mix_type=str(bio_mix_type),
    gwp_ch4=float(gwp_ch4),
    gwp_n2o=float(gwp_n2o),
    cost_mode=str(cost_mode),
    reduce_fuel=str(reduce_fuel),
    alt_fuels=list(alt_fuels) if isinstance(alt_fuels, list) else [],
    share_step=float(share_step),
    x_steps_coarse=int(x_steps_coarse),
    max_replace_frac=float(max_replace_frac),
    cap_added_t={k: float(v) for k, v in cap_added.items()},
    enable_optimizer=False,
)

opt_cfg = copy.deepcopy(base_cfg)
opt_cfg.enable_optimizer = bool(opt_enabled)


# =============================================================================
# OVERVIEW TAB
# =============================================================================
with tab_overview:
    if segments_df.empty:
        st.info("No segments yet. Use the sidebar to add segments.")
    else:
        # Compute combined energy stacks
        E_scope, num_phys, E_rfnbo_scope, combined_all, combined_scope = scope_from_segments(segments, FUELS)
        E_total = sum(combined_all.values())

        st.subheader("Key metrics (current inputs)")

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        with c1:
            st.metric("Total energy (all)", f"{us2(E_total)} MJ")
        with c2:
            st.metric("In-scope energy", f"{us2(E_scope)} MJ")
        with c3:
            st.metric("Attained (2025)", f"{us2(fueleu_attained_intensity(2025, E_scope, num_phys, E_rfnbo_scope))} gCO₂e/MJ")
        with c4:
            st.metric("Attained (2030)", f"{us2(fueleu_attained_intensity(2030, E_scope, num_phys, E_rfnbo_scope))} gCO₂e/MJ")
        with c5:
            # preview conversion
            g_preview = fueleu_attained_intensity(2025, E_scope, num_phys, E_rfnbo_scope) or BASELINE_2020_GFI
            tco2e_per_vlsfo_t = (g_preview * 41_000.0) / 1_000_000.0
            st.metric("Credit €/VLSFO-eq t", us2(float(credit_eur_per_tco2e) * tco2e_per_vlsfo_t))
        with c6:
            g_preview = g_preview or BASELINE_2020_GFI
            tco2e_per_vlsfo_t = (g_preview * 41_000.0) / 1_000_000.0
            st.metric("Penalty €/tCO₂e", us2((float(penalty_eur_per_vlsfo_t) / tco2e_per_vlsfo_t) if tco2e_per_vlsfo_t > 0 else 0.0))

        st.markdown("### Combined energy (All vs In-scope)")
        categories = ["All energy", "In-scope energy"]
        fuel_keys_sorted = sorted([k for k in combined_all.keys() if k != "ELEC"], key=lambda k: FUELS[k].wtw_gCO2e_per_MJ if k in FUELS else 1e9)
        stack_order = ["ELEC"] + fuel_keys_sorted

        fig = go.Figure()
        for k in stack_order:
            name = "ELEC (OPS)" if k == "ELEC" else FUELS[k].display
            fig.add_trace(
                go.Bar(
                    x=categories,
                    y=[combined_all.get(k, 0.0), combined_scope.get(k, 0.0)],
                    name=name,
                    marker_color=COLORS.get(k, None),
                    hovertemplate=f"{name}<br>%{{x}}<br>%{{y:,.2f}} MJ<extra></extra>",
                )
            )
        fig.update_layout(
            barmode="stack",
            hovermode="x unified",
            margin=dict(l=40, r=20, t=35, b=30),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            yaxis_title="Energy [MJ]",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("If any cross-border segment uses prioritized allocation, in-scope mix is globally rearranged by ascending WtW (ELEC fixed).")

        st.markdown("### GHG Intensity vs FuelEU Limit (2025–2050)")
        years = LIMITS_DF["Year"].tolist()
        limit_series = LIMITS_DF["Limit_gCO2e_per_MJ"].tolist()
        actual_series = [fueleu_attained_intensity(y, E_scope, num_phys, E_rfnbo_scope) for y in years]

        fig2 = go.Figure()
        fig2.add_trace(
            go.Scatter(
                x=years, y=limit_series, name="FuelEU limit (step)",
                mode="lines+markers", line=dict(shape="hv", width=3),
                hovertemplate="Year=%{x}<br>Limit=%{y:,.2f} gCO₂e/MJ<extra></extra>",
            )
        )
        fig2.add_trace(
            go.Scatter(
                x=years, y=actual_series, name="Attained (combined in-scope)",
                mode="lines", line=dict(dash="dash", width=3),
                hovertemplate="Year=%{x}<br>Attained=%{y:,.2f} gCO₂e/MJ<extra></extra>",
            )
        )
        fig2.update_layout(
            hovermode="x unified",
            margin=dict(l=40, r=20, t=35, b=35),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            xaxis_title="Year",
            yaxis_title="GHG intensity [gCO₂e/MJ]",
        )
        st.plotly_chart(fig2, use_container_width=True)


# =============================================================================
# SEGMENTS TAB
# =============================================================================
with tab_segments:
    st.subheader("Segments editor (table)")

    cfg_cols = {
        "type": st.column_config.SelectboxColumn("Type", options=SEG_TYPES, required=True),
        "prio_on": st.column_config.CheckboxColumn("Prioritized cross-border", help="Only used for cross-border legs; ignored otherwise."),
        "OPS_kWh": st.column_config.NumberColumn("EU OPS [kWh]", min_value=0.0, step=100.0),
    }
    for fk in FUEL_KEYS:
        cfg_cols[f"{fk}_t"] = st.column_config.NumberColumn(f"{FUELS[fk].display} [t]", min_value=0.0, step=1.0)

    edited = st.data_editor(
        segments_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config=cfg_cols,
        hide_index=True,
        key="segments_editor",
    )

    _segments_state_from_df(edited)
    segments = st.session_state.get("segments", [])
    segments_df = _segments_df_from_state()

    st.caption("Tip: For cross-border legs, toggle “Prioritized cross-border” to fill the 50% in-scope pool by ascending WtW across fuels.")

    st.markdown("### Per-segment scope preview")
    if segments_df.empty:
        st.info("Add at least one segment.")
    else:
        for i, seg in enumerate(segments):
            energies_all = _segment_energy_mj(seg, FUELS)
            energies_scope, elec_mj = _segment_scope_with_toggle(seg, energies_all, FUELS)
            left_vals = dict(energies_all)
            right_vals = dict(energies_scope)
            if str(seg.get("type")) == "EU at-berth (port stay)":
                left_vals["ELEC"] = elec_mj
                right_vals["ELEC"] = elec_mj

            stack_order = ["ELEC"] + sorted([k for k in energies_all.keys()], key=lambda k: FUELS[k].wtw_gCO2e_per_MJ)

            fig = go.Figure()
            cats = ["All", "In-scope"]
            for k in stack_order:
                if k == "ELEC":
                    name = "ELEC (OPS)"
                    y = [left_vals.get("ELEC", 0.0), right_vals.get("ELEC", 0.0)]
                else:
                    name = FUELS[k].display
                    y = [left_vals.get(k, 0.0), right_vals.get(k, 0.0)]
                if (y[0] <= 0 and y[1] <= 0):
                    continue
                fig.add_trace(go.Bar(x=cats, y=y, name=name, marker_color=COLORS.get(k, None)))
            fig.update_layout(
                title=f"Segment {i+1}: {seg.get('type','')}",
                barmode="stack",
                height=260,
                margin=dict(l=40, r=20, t=40, b=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                yaxis_title="Energy [MJ]",
            )
            st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# RESULTS & POLICIES TAB
# =============================================================================
with tab_results:
    st.subheader("Scenario results (time-consistent banking)")

    if segments_df.empty:
        st.info("Add segments first.")
    else:
        # Base scenario (no optimizer)
        df_base = simulate_policy(segments, FUELS, base_cfg, premiums_vs_reduce)

        # Optimizer scenario
        df_opt = simulate_policy(segments, FUELS, opt_cfg, premiums_vs_reduce) if opt_cfg.enable_optimizer else None

        # Display
        cA, cB = st.columns([1, 1])
        with cA:
            st.markdown("**Base policy**")
        with cB:
            st.markdown("**Optimizer policy**" if df_opt is not None else "**Optimizer disabled**")

        # Quick comparison metrics for a selected year
        sel_year = st.selectbox("Compare year", YEARS, index=YEARS.index(2030) if 2030 in YEARS else 0)
        base_row = df_base[df_base["Year"] == sel_year].iloc[0].to_dict()
        if df_opt is not None:
            opt_row = df_opt[df_opt["Year"] == sel_year].iloc[0].to_dict()

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Base total cost (EUR)", us2(base_row["Total_Cost_EUR"]))
        with m2:
            if df_opt is None:
                st.metric("Opt total cost (EUR)", "—")
            else:
                st.metric("Opt total cost (EUR)", us2(opt_row["Total_Cost_EUR"]))
        with m3:
            if df_opt is None:
                st.metric("Δ cost (Opt-Base)", "—")
            else:
                st.metric("Δ cost (Opt-Base)", us2(opt_row["Total_Cost_EUR"] - base_row["Total_Cost_EUR"]))
        with m4:
            if df_opt is None:
                st.metric("x reduced (t)", "—")
            else:
                st.metric(f"{reduce_fuel} reduced (t)", us2(opt_row["x_reduce_used_t"]))

        # Plots
        # --- 5-year window selector for chart readability ---
        periods_5y = [
            ("2025–2030", 2025, 2030),
            ("2030–2035", 2030, 2035),
            ("2035–2040", 2035, 2040),
            ("2040–2045", 2040, 2045),
            ("2045–2050", 2045, 2050),
        ]
        period_label = st.selectbox(
            "Total cost chart period (5-year windows)",
            options=[p[0] for p in periods_5y],
            index=0,
            key="total_cost_period_5y",
        )
        p_start, p_end = next((s, e) for (lab, s, e) in periods_5y if lab == period_label)

        df_base_plot = df_base[(df_base["Year"] >= p_start) & (df_base["Year"] <= p_end)]
        df_opt_plot = None if df_opt is None else df_opt[(df_opt["Year"] >= p_start) & (df_opt["Year"] <= p_end)]

        st.markdown(f"### Total cost comparison ({period_label})")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_base_plot["Year"], y=df_base_plot["Total_Cost_EUR"],
            mode="lines", name="Base"
        ))
        if df_opt_plot is not None:
            fig.add_trace(go.Scatter(
                x=df_opt_plot["Year"], y=df_opt_plot["Total_Cost_EUR"],
                mode="lines", name="Optimizer", line=dict(dash="dash")
            ))

        fig.update_layout(
            hovermode="x unified",
            margin=dict(l=40, r=20, t=35, b=35),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            xaxis_title="Year",
            yaxis_title="Total cost [EUR]",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Attained intensity vs limit")
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=df_base["Year"], y=df_base["Limit_gCO2e_per_MJ"], mode="lines+markers", name="Limit", line=dict(shape="hv", width=3)))
        fig2.add_trace(go.Scatter(x=df_base["Year"], y=df_base["Attained_gCO2e_per_MJ"], mode="lines", name="Base attained"))
        if df_opt is not None:
            fig2.add_trace(go.Scatter(x=df_opt["Year"], y=df_opt["Attained_gCO2e_per_MJ"], mode="lines", name="Opt attained", line=dict(dash="dash")))
        fig2.update_layout(
            hovermode="x unified",
            margin=dict(l=40, r=20, t=35, b=35),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            xaxis_title="Year",
            yaxis_title="gCO₂e/MJ",
        )
        st.plotly_chart(fig2, use_container_width=True)

        # Tables
        st.markdown("### Detailed tables")
        show_cols = [
            "Year", "Reduction_%", "Limit_gCO2e_per_MJ", "Attained_gCO2e_per_MJ",
            "FinalBalance_tCO2e", "Penalty_EUR", "Credit_EUR", "Pooling_Cost_EUR",
            "ETS_Emissions_tCO2e", "ETS_Cost_EUR", "Fuel_Cost_EUR", "Total_Cost_EUR",
            "x_reduce_used_t", "shares_used", "alt_added_t"
        ]

        def fmt_df(df: pd.DataFrame) -> pd.DataFrame:
            out = df.copy()
            for c in out.columns:
                if c in ("Year", "shares_used", "alt_added_t"):
                    continue
                out[c] = out[c].apply(us2)
            return out

        ctab1, ctab2 = st.columns([1, 1])
        with ctab1:
            st.markdown("**Base**")
            st.dataframe(fmt_df(df_base[show_cols]), use_container_width=True)
            st.download_button("Download base CSV", df_base.to_csv(index=False), "base_results.csv", "text/csv")
        with ctab2:
            if df_opt is None:
                st.info("Optimizer disabled.")
            else:
                st.markdown("**Optimizer**")
                st.dataframe(fmt_df(df_opt[show_cols]), use_container_width=True)
                st.download_button("Download optimizer CSV", df_opt.to_csv(index=False), "optimizer_results.csv", "text/csv")

        # Input snapshot
        snapshot = {
            "app_version": APP_VERSION,
            "saved_at_utc": _now_utc().isoformat(),
            "segments": st.session_state.get("segments", []),
            "fuels": {k: fs.__dict__ for k, fs in FUELS.items()},
            "policy": base_cfg.__dict__,
            "optimizer_policy": opt_cfg.__dict__,
            "premiums_vs_reduce": premiums_vs_reduce,
            "caps_added_t": {k: float(v) for k, v in cap_added.items()},
        }
        st.download_button("Download input snapshot (JSON)", json.dumps(snapshot, indent=2), "input_snapshot.json", "application/json")

        st.info(
            "Optimizer is greedy year-by-year with scenario-consistent banking carry. "
            "If you need a multi-year joint optimization, the model can be extended (but this version avoids heavy runtimes).",
            icon="ℹ️",
        )


# =============================================================================
# SIMULATION TAB
# =============================================================================
with tab_sim:
    st.subheader("Premium sensitivity (optimizer vs pooling-only)")

    if segments_df.empty:
        st.info("Add segments first.")
    else:
        sim_year = st.selectbox("Year", YEARS, index=YEARS.index(2030) if 2030 in YEARS else 0, key="sim_year")
        pooling_price_compare = text_input_float("Pooling price for comparison [€/tCO₂e]", "sim_pool_price", 200.0, min_value=0.0)

        # choose which premium to sweep (one dimension)
        sweep_fuel = st.selectbox("Sweep premium for fuel", options=["BIO", "RFNBO", "CUSTOM_A", "CUSTOM_B"], index=0, key="sweep_fuel")
        prem_min = text_input_float("Premium min [€/t]", "sim_prem_min", 0.0, min_value=0.0)
        prem_max = text_input_float("Premium max [€/t]", "sim_prem_max", 1000.0, min_value=0.0)
        prem_step = text_input_float("Premium step [€/t]", "sim_prem_step", 50.0, min_value=1.0)

        if prem_step <= 0:
            st.warning("Step must be > 0.")
            st.stop()

        if prem_max < prem_min:
            prem_min, prem_max = prem_max, prem_min

        n = int((prem_max - prem_min) // prem_step) + 1
        grid = [prem_min + i * prem_step for i in range(max(n, 1))]

        # Build configs: optimizer uses current alt_fuels selection; pooling-only uses no fuel switch but pooling to neutralize deficits
        y = int(sim_year)

        # base with pooling neutral (0 pooling) to compute deficit
        # --- compute carry-in up to selected year using the SAME policy as Results tab ---
        carry_base = 0.0
        state_base = {"fixed_multiplier_by_step": {}, "consec_def": 0}
        
        for yy in YEARS:
            if yy >= y:
                break
            m_prev = compute_one_year(yy, segments, FUELS, carry_base, base_cfg, None, premiums_vs_reduce)
            # advance multiplier state (only matters for dynamic)
            _ = _penalty_multiplier(yy, bool(m_prev["is_deficit"]), int(m_prev["step_idx"]), state_base, base_cfg)
            carry_base = float(m_prev["Banking_tCO2e"])
        
        # base policy for the selected year (matches the tables' carry-in mechanics)
        base_one = compute_one_year(y, segments, FUELS, carry_base, base_cfg, None, premiums_vs_reduce)
        ets_cost_base = float(base_one["ETS_Cost_EUR"])
        
        # deficit used for "pooling-only" reference: compute deficit with pooling disabled BUT same carry-in
        tmp_cfg = copy.deepcopy(base_cfg)
        tmp_cfg.pooling_tco2e = 0.0
        tmp_cfg.enable_optimizer = False
        base_no_pool = compute_one_year(y, segments, FUELS, carry_base, tmp_cfg, None, premiums_vs_reduce)
        
        deficit = max(-float(base_no_pool["FinalBalance_tCO2e"]), 0.0)
        pooling_cost_component = deficit * float(pooling_price_compare)

        
        deficit = max(-float(base_one["FinalBalance_tCO2e"]), 0.0)
        pooling_cost_component = deficit * float(pooling_price_compare)
        ets_cost_base = float(base_one["ETS_Cost_EUR"])
        with st.expander("Debug: pooling-only line components", expanded=False):
            st.write({
                "Year": y,
                "FinalBalance_tCO2e (base)": float(base_one["FinalBalance_tCO2e"]),
                "Deficit used for pooling (tCO2e)": float(deficit),
                "Pooling price (€/tCO2e)": float(pooling_price_compare),
                "Pooling cost component (EUR)": float(pooling_cost_component),
                "ETS base cost (EUR)": float(ets_cost_base),
                "Dashed line total (EUR)": float(ets_cost_base + pooling_cost_component),
            })

        # Cost curves
        opt_costs = []
        pool_only_costs = []

        for prem in grid:
            prem_local = dict(premiums_vs_reduce)
            prem_local[sweep_fuel] = float(prem)

            # optimizer cost for this year only (pooling neutral to make policy comparison cleaner)
            cfg_local = copy.deepcopy(opt_cfg)
            cfg_local.pooling_tco2e = 0.0
            cfg_local.enable_optimizer = True

            # Ensure sweep fuel is included if user forgot
            if sweep_fuel not in cfg_local.alt_fuels and sweep_fuel in FUELS:
                cfg_local.alt_fuels = (cfg_local.alt_fuels or []) + [sweep_fuel]
                cfg_local.alt_fuels = list(dict.fromkeys(cfg_local.alt_fuels))  # unique

            x, shares, m = optimize_one_year(y, segments, FUELS, 0.0, cfg_local, prem_local)
            # apply per-year seed multiplier
            mult = 1.0 + (max(int(cfg_local.deficit_seed), 1) - 1) * 0.10 if bool(m["is_deficit"]) else 1.0
            total = float(m["Penalty_EUR_raw"]) * mult - float(m["Credit_EUR"]) + float(m["ETS_Cost_EUR"]) + float(m["Fuel_Cost_EUR"])
            opt_costs.append(total)

            # pooling-only: base ETS + pooling to neutralize deficit + fuel premium cost of existing alt fuels is ignored in incremental comparison
            pool_only_costs.append(ets_cost_base + pooling_cost_component)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=grid, y=opt_costs, mode="lines+markers", name="Fuel switch optimizer (pooling neutral)"))
        fig.add_trace(go.Scatter(x=grid, y=pool_only_costs, mode="lines", name=f"Pooling-only policy @ {float(pooling_price_compare):,.0f} €/tCO₂e", line=dict(dash="dash")))
        fig.update_layout(
            hovermode="x unified",
            margin=dict(l=40, r=20, t=35, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
            xaxis_title=f"Premium of {sweep_fuel} vs reduced fuel [€/t]",
            yaxis_title="Total cost [EUR] (FuelEU + ETS + fuel-cost per mode)",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "The optimizer curve is computed with pooling neutral (pooling_tco2e=0) to isolate fuel-switch economics. "
            "The pooling-only line represents covering the base deficit via pooling at the chosen price."
        )


# =============================================================================
# Footer
# =============================================================================
st.info("Public demo — non-production. Results are informational; no warranty.", icon="ℹ️")
show_trial_footer(APP_OWNER, APP_VERSION, APP_DATE)
st.caption("Built with Streamlit • By using this app you also accept Streamlit’s Terms and Privacy.")
