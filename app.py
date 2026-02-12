# app.py
# FuelEU Maritime — Voyage Segments + EU ETS (Maritime) — Optimizer & Policy Comparison
# -----------------------------------------------------------------------------
# Practical notes:
# - This is a single-file Streamlit app (replace your existing app.py).
# - Keeps your core logic (segments, prioritized allocation, global rearrangement, pooling/banking,
#   EU ETS blend treatment, optimizer, simulation) but refactors for stability, usability, and clarity.
# - Adds a cleaner UI environment, stronger guards, scenario save/load, and a more explicit regulatory section.
# -----------------------------------------------------------------------------

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

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

/* App typography / spacing */
html, body, [class*="css"]  { font-size: 15px; }
.block-container { padding-top: 1.2rem; padding-bottom: 1.5rem; }

/* Sidebar compact layout */
section[data-testid="stSidebar"] div.block-container{
    padding-top:.6rem; padding-bottom:.6rem;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{ gap:.75rem; }
section[data-testid="stSidebar"] label{
    font-size:.95rem; margin-bottom:.15rem; font-weight:650;
}
section[data-testid="stSidebar"] input[type="text"],
section[data-testid="stSidebar"] input[type="number"]{
    height:2.05rem; min-height:2.05rem; padding:.30rem .55rem;
}

/* Cards ("bubbles") */
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
[data-testid="stMetricValue"]{
    font-size:.90rem !important; font-weight:750 !important; line-height:1.10 !important;
}

/* DataFrame compact */
[data-testid="stDataFrame"]{ font-size:.88rem !important; }
[data-testid="stDataFrame"] div[role="columnheader"],
[data-testid="stDataFrame"] div[role="gridcell"]{ padding:2px 6px !important; }

/* ETS block highlight */
section[data-testid="stSidebar"] .ets-section{
    border:1px solid #bbf7d0;
    background:#f0fdf4;
    border-radius:.65rem;
    padding:.50rem .60rem;
    margin-top:.35rem;
}
section[data-testid="stSidebar"] .ets-section label{ color:#15803d; }
section[data-testid="stSidebar"] .ets-section input,
section[data-testid="stSidebar"] .ets-section select{
    border-color:#86efac; background:#ecfdf5;
}

/* Buttons */
.stButton button { border-radius: .55rem; }
</style>
""",
    unsafe_allow_html=True,
)

# =============================================================================
# Constants (FuelEU)
# =============================================================================
BASELINE_2020_GFI = 91.16  # gCO2e/MJ baseline used for step-limits (as per your model)
DEFAULTS_PATH = ".fueleu_defaults.json"
SCENARIOS_PATH = ".fueleu_scenarios.json"

REDUCTION_STEPS = [
    (2025, 2029, 2.0),
    (2030, 2034, 6.0),
    (2035, 2039, 14.5),
    (2040, 2044, 31.0),
    (2045, 2049, 62.0),
    (2050, 2050, 80.0),
]
YEARS = list(range(2025, 2051))

FUELS = ["RFNBO", "BIO", "HSFO", "LFO", "MGO"]  # for stacking / ordering
FOSSILS = ["HSFO", "LFO", "MGO"]

COLORS = {
    "ELEC": "#FACC15",
    "RFNBO": "#86EFAC",
    "BIO": "#065F46",
    "MGO": "#93C5FD",
    "LFO": "#2563EB",
    "HSFO": "#1E3A8A",
}

SEG_TYPES = [
    "Intra-EU voyage",
    "EU→non-EU voyage",
    "non-EU→EU voyage",
    "EU at-berth (port stay)",
]

# =============================================================================
# EU ETS factors (TTW) + GWP100 aggregation for CO2e (from 2026+ in your model)
# =============================================================================
EF_TCO2_PER_T = {"HSFO": 3.114, "LFO": 3.151, "MGO": 3.206}

# Placeholder defaults for non-CO2 TTW factors (t gas / t fuel) for liquid fuels.
# IMPORTANT: Align these with your chosen EU MRV/ETS methodological source if required.
ETS_NONCO2_EF = {
    "HSFO": {"CH4": 5e-5, "N2O": 1.8e-4},
    "LFO": {"CH4": 5e-5, "N2O": 1.8e-4},
    "MGO": {"CH4": 5e-5, "N2O": 1.8e-4},
}

GWP100_CH4 = 28.0   # AR5-type GWP100 in your model
GWP100_N2O = 265.0  # AR5-type GWP100 in your model


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


def parse_us_any(s: Any, default: float = 0.0) -> float:
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return float(default)


def parse_us(s: Any, default: float = 0.0, min_value: float = 0.0) -> float:
    v = parse_us_any(s, default=default)
    return max(v, float(min_value))


def ss_float(key: str, default: float) -> float:
    return parse_us_any(st.session_state.get(key, _get(DEFAULTS, key, default)), default)


def float_text_input(
    label: str,
    default_val: float,
    key: str,
    min_value: float = 0.0,
    help: str | None = None,
    label_visibility: str = "visible",
) -> float:
    if key not in st.session_state:
        st.session_state[key] = us2(default_val)

    def _normalize():
        val = parse_us(st.session_state[key], default=default_val, min_value=min_value)
        st.session_state[key] = us2(val)

    st.text_input(
        label,
        value=st.session_state[key],
        key=key,
        on_change=_normalize,
        help=help,
        label_visibility=label_visibility,
    )
    return parse_us(st.session_state[key], default=default_val, min_value=min_value)


def float_text_input_signed(label: str, default_val: float, key: str, help: str | None = None) -> float:
    if key not in st.session_state:
        st.session_state[key] = us2(default_val)

    def _normalize():
        val = parse_us_any(st.session_state[key], default=default_val)
        st.session_state[key] = us2(val)

    st.text_input(label, value=st.session_state[key], key=key, on_change=_normalize, help=help)
    return parse_us_any(st.session_state[key], default=default_val)


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

# Ensure component is mounted once
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
    """
    • Shared username/password (from secrets or defaults).
    • First successful login sets a TRIAL cookie (expires in N days).
    • SESSION cookie controls current session; Logout deletes SESSION only.
    • If cookies are blocked, fallback allows tab-local access for expiry_days.
    """
    cfg = _get_auth_config()
    trial_ck = cfg.trial_cookie
    sess_ck = cfg.session_cookie
    expiry_days = cfg.expiry_days

    # Fallback flags
    if "_fallback_logged_in" not in st.session_state:
        st.session_state["_fallback_logged_in"] = False
    if "_fallback_trial_until" not in st.session_state:
        st.session_state["_fallback_trial_until"] = None

    # Cookie auth
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

    # Session without trial => clear session and force login
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

    # Login UI (form-stable)
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

    # Set trial cookie if missing
    if not trial_tok:
        _cookie_set(trial_ck, str(uuid.uuid4()), expires_days=expiry_days)

    # Set session cookie (no explicit expiry)
    _cookie_set(sess_ck, str(uuid.uuid4()))

    # Fallback expiry for cookie-less environments
    st.session_state["_fallback_logged_in"] = True
    if not st.session_state.get("_fallback_trial_until"):
        st.session_state["_fallback_trial_until"] = (_now_utc() + timedelta(days=expiry_days)).isoformat()

    st.rerun()


# =============================================================================
# FuelEU limits
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
# Core computations
# =============================================================================
def compute_energy_MJ(mass_t: float, lcv_MJ_per_t: float) -> float:
    mass_t = max(float(mass_t), 0.0)
    lcv = max(float(lcv_MJ_per_t), 0.0)
    return mass_t * lcv


def euros_from_tco2e(balance_tco2e_positive: float, g_attained: float, price_eur_per_vlsfo_t: float) -> float:
    """
    Convert tCO2e into EUR using an equivalent VLSFO-tonne price and attained intensity.
    Reference energy: 41,000 MJ/t.
    """
    if balance_tco2e_positive <= 0 or price_eur_per_vlsfo_t <= 0 or g_attained <= 0:
        return 0.0
    tco2e_per_vlsfot = (g_attained * 41_000.0) / 1_000_000.0
    if tco2e_per_vlsfot <= 0:
        return 0.0
    vlsfo_eq_t = balance_tco2e_positive / tco2e_per_vlsfot
    return vlsfo_eq_t * price_eur_per_vlsfo_t


# =============================================================================
# Segment state
# =============================================================================
def _default_segment() -> Dict[str, Any]:
    return {
        "type": SEG_TYPES[0],
        "HSFO_t": 0.0,
        "LFO_t": 0.0,
        "MGO_t": 0.0,
        "BIO_t": 0.0,
        "RFNBO_t": 0.0,
        "OPS_kWh": 0.0,   # only for berth
        "prio_on": True,  # used for cross-border segments
    }


def _ensure_segments_state():
    if "abs_segments" not in st.session_state:
        if "abs_segments" in DEFAULTS and isinstance(DEFAULTS["abs_segments"], list):
            st.session_state["abs_segments"] = DEFAULTS["abs_segments"]
        else:
            st.session_state["abs_segments"] = []


def _segments_totals_masses_and_ops() -> Tuple[Dict[str, Dict[str, float]], float]:
    res = {
        "intra_voy": {k: 0.0 for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO"]},
        "extra_voy": {k: 0.0 for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO"]},
        "eu_berth": {k: 0.0 for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO"]},
    }
    ops_kwh_total = 0.0

    for seg in st.session_state.get("abs_segments", []):
        t = seg.get("type", SEG_TYPES[0])
        bucket = "intra_voy" if t == "Intra-EU voyage" else ("eu_berth" if t == "EU at-berth (port stay)" else "extra_voy")

        for f in ["HSFO", "LFO", "MGO", "BIO", "RFNBO"]:
            res[bucket][f] += float(seg.get(f + "_t", 0.0) or 0.0)

        if t == "EU at-berth (port stay)":
            ops_kwh_total += float(seg.get("OPS_kWh", 0.0) or 0.0)

    return res, ops_kwh_total


def _masses_to_energies(masses: Dict[str, float], LCVs: Dict[str, float]) -> Dict[str, float]:
    return {f: compute_energy_MJ(masses.get(f, 0.0), LCVs.get(f, 0.0)) for f in ["HSFO", "LFO", "MGO", "BIO", "RFNBO"]}


# =============================================================================
# Allocators (per-segment and global)
# =============================================================================
def prioritized_half_scope_all_fuels(energies_voy: Dict[str, float], wtw: Dict[str, float]) -> Dict[str, float]:
    """
    Cross-border allocator when toggle is ON:
      - Pool = 50% of TOTAL segment energy (fuels only)
      - Fill by ascending WtW across ALL fuels until pool full
    """
    pool = 0.5 * sum(energies_voy.values())
    result = {k: 0.0 for k in energies_voy.keys()}
    order = sorted(energies_voy.keys(), key=lambda f: wtw.get(f, float("inf")))
    for f in order:
        if pool <= 0:
            break
        take = min(float(energies_voy.get(f, 0.0) or 0.0), pool)
        if take > 0:
            result[f] = take
            pool -= take
    return result


def _segment_energy_mj(seg: Dict[str, Any], LCV: Dict[str, float]) -> Dict[str, float]:
    return {f: compute_energy_MJ(seg.get(f"{f}_t", 0.0), LCV.get(f, 0.0)) for f in ["HSFO", "LFO", "MGO", "BIO", "RFNBO"]}


def _segment_scope_with_toggle(seg: Dict[str, Any], energies_all: Dict[str, float], wtw: Dict[str, float]) -> Tuple[Dict[str, float], float]:
    """
    Returns (in_scope_fuel_MJ, elec_MJ_segment).
    - Intra-EU: 100%
    - EU berth: 100% + ELEC
    - Cross-border: OFF => 50% each fuel; ON => prioritized pool allocator
    """
    t = seg.get("type", SEG_TYPES[0])

    if t == "Intra-EU voyage":
        return dict(energies_all), 0.0

    if t == "EU at-berth (port stay)":
        return dict(energies_all), float(seg.get("OPS_kWh", 0.0) or 0.0) * 3.6

    # Cross-border
    prio_on = bool(seg.get("prio_on", True))
    if prio_on:
        return prioritized_half_scope_all_fuels(energies_all, wtw), 0.0

    return {k: 0.5 * energies_all[k] for k in energies_all.keys()}, 0.0


def _has_prioritized_segments(segments: List[Dict[str, Any]]) -> bool:
    for seg in segments or []:
        t = seg.get("type", SEG_TYPES[0])
        if t in ("EU→non-EU voyage", "non-EU→EU voyage") and bool(seg.get("prio_on", True)):
            return True
    return False


def _global_rearrange_scope(
    combined_all: Dict[str, float],
    combined_scope: Dict[str, float],
    wtw_dict: Dict[str, float],
) -> Dict[str, float]:
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

    fuels_sorted = sorted(FUELS, key=lambda f: wtw_dict.get(f, float("inf")))
    remaining = fuel_budget

    for f in fuels_sorted:
        if remaining <= 0.0:
            break
        avail = float(combined_all.get(f, 0.0) or 0.0)
        if avail <= 0.0:
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


# =============================================================================
# EU ETS calculations
# =============================================================================
def _ets_geo_emissions_tco2e_from_masses(ets_masses: Dict[str, float], year: int) -> float:
    """
    Geographic-scope ETS emissions in tCO2e:
    - 2025: CO2 only
    - 2026+: CO2 + CH4*GWP100 + N2O*GWP100
    """
    year = int(year)

    co2 = 0.0
    ch4 = 0.0
    n2o = 0.0

    for f in ["HSFO", "LFO", "MGO"]:
        m = float(ets_masses.get(f, 0.0) or 0.0)
        co2 += m * EF_TCO2_PER_T[f]
        if year >= 2026:
            ch4 += m * ETS_NONCO2_EF[f]["CH4"]
            n2o += m * ETS_NONCO2_EF[f]["N2O"]

    if year < 2026:
        return co2

    return co2 + ch4 * GWP100_CH4 + n2o * GWP100_N2O


def _ets_in_scope_masses(
    totals_mass: Dict[str, Dict[str, float]],
    pure_bio_pct: float,
    bio_mix_type: str,
) -> Dict[str, float]:
    """
    ETS in-scope fuel masses (HSFO/LFO/MGO only after BIO blend split):
      - Intra-EU: 100%
      - EU berth: 100%
      - Cross-border: 50%
    BIO is treated as a delivered blend:
      - pure_bio_pct% is zero-rated
      - fossil share assigned to a selected fossil (Bio Mix Type)
    """
    ets_masses = {f: 0.0 for f in ["HSFO", "LFO", "MGO"]}

    for f in ["HSFO", "LFO", "MGO"]:
        ets_masses[f] = (
            totals_mass["intra_voy"][f]
            + totals_mass["eu_berth"][f]
            + 0.5 * totals_mass["extra_voy"][f]
        )

    bio_in_scope_t = (
        totals_mass["intra_voy"]["BIO"]
        + totals_mass["eu_berth"]["BIO"]
        + 0.5 * totals_mass["extra_voy"]["BIO"]
    )

    pure_bio_frac = max(0.0, min(float(pure_bio_pct) / 100.0, 1.0))
    fossil_share_t = bio_in_scope_t * (1.0 - pure_bio_frac)

    mix_type = (bio_mix_type or "").upper()
    if "HSFO" in mix_type:
        ets_masses["HSFO"] += fossil_share_t
    elif "LFO" in mix_type or "VLSFO" in mix_type:
        ets_masses["LFO"] += fossil_share_t
    elif "MGO" in mix_type:
        ets_masses["MGO"] += fossil_share_t
    else:
        ets_masses["MGO"] += fossil_share_t

    return ets_masses


def _ets_cost_from_segments(
    segments: List[Dict[str, Any]],
    pure_bio_pct: float,
    bio_mix_type: str,
    eua_price_eur_per_tco2e: float,
    year: int,
) -> float:
    """
    Recompute EU ETS cost [EUR] from a segments list, using same scope & blend logic.
    """
    totals_local = {
        "intra_voy": {f: 0.0 for f in ["HSFO", "LFO", "MGO", "BIO"]},
        "extra_voy": {f: 0.0 for f in ["HSFO", "LFO", "MGO", "BIO"]},
        "eu_berth": {f: 0.0 for f in ["HSFO", "LFO", "MGO", "BIO"]},
    }

    for seg in segments:
        t = seg.get("type", SEG_TYPES[0])
        if t == "Intra-EU voyage":
            bucket = "intra_voy"
        elif t == "EU at-berth (port stay)":
            bucket = "eu_berth"
        else:
            bucket = "extra_voy"

        for f in ["HSFO", "LFO", "MGO", "BIO"]:
            totals_local[bucket][f] += float(seg.get(f"{f}_t", 0.0) or 0.0)

    ets_masses_local = _ets_in_scope_masses(totals_local, pure_bio_pct, bio_mix_type)
    geo = _ets_geo_emissions_tco2e_from_masses(ets_masses_local, int(year))
    cov = 0.70 if int(year) == 2025 else 1.00  # your model’s phase
    return (geo * cov) * float(eua_price_eur_per_tco2e)


# =============================================================================
# Optimizer: segment priority + apply shift
# =============================================================================
def _segment_opt_priority(seg: Dict[str, Any]) -> int:
    """
    Remove fossil first where ETS exposure is higher:
      0 -> Intra-EU
      1 -> EU berth
      2 -> Cross-border
      3 -> fallback
    """
    t = seg.get("type", "")
    if t == "Intra-EU voyage":
        return 0
    if t == "EU at-berth (port stay)":
        return 1
    if t in ("non-EU→EU voyage", "EU→non-EU voyage"):
        return 2
    return 3


def _apply_shift_to_segments(
    base_segments: List[Dict[str, Any]],
    fuel: str,
    x_decrease_t: float,
    LCV: Dict[str, float],
) -> Tuple[List[Dict[str, Any]], float, float]:
    """
    Reduce selected fossil (HSFO/LFO/MGO) by x tonnes and add BIO energy-equivalently
    in the same segments where fossil is removed.
    """
    segs = copy.deepcopy(base_segments)
    x = max(0.0, float(x_decrease_t))

    total_avail = sum(float(seg.get(f"{fuel}_t", 0.0) or 0.0) for seg in segs)
    if total_avail <= 0.0:
        return segs, 0.0, 0.0

    x = min(x, total_avail)
    remaining = x

    LCV_SEL = float(LCV.get(fuel, 0.0) or 0.0)
    LCV_BIO = float(LCV.get("BIO", 0.0) or 0.0)
    if LCV_SEL <= 0.0 or LCV_BIO <= 0.0:
        return segs, 0.0, 0.0

    indices = list(range(len(segs)))
    indices.sort(key=lambda i: _segment_opt_priority(segs[i]))

    actual_dec = 0.0
    total_bio_added = 0.0

    for i in indices:
        if remaining <= 0.0:
            break
        seg = segs[i]
        avail = float(seg.get(f"{fuel}_t", 0.0) or 0.0)
        if avail <= 0.0:
            continue

        take = min(avail, remaining)
        if take <= 0.0:
            continue

        seg[f"{fuel}_t"] = avail - take
        remaining -= take
        actual_dec += take

        bio_add_t = take * (LCV_SEL / LCV_BIO)
        if bio_add_t > 0.0:
            seg["BIO_t"] = float(seg.get("BIO_t", 0.0) or 0.0) + bio_add_t
            total_bio_added += bio_add_t

    if actual_dec <= 0.0:
        return segs, 0.0, 0.0

    return segs, actual_dec, (actual_dec * (LCV_SEL / LCV_BIO))


# =============================================================================
# FuelEU scope and balance from segments (reused by optimizer + simulation)
# =============================================================================
def _scope_from_segments(
    segments: List[Dict[str, Any]],
    LCV: Dict[str, float],
    wtw: Dict[str, float],
) -> Tuple[float, float, float]:
    """
    Compute:
      - E_scope [MJ]
      - num_phys = Σ(E_scope_k * WtW_k)
      - E_rfnbo_scope [MJ]
    including global rearrangement if prioritized allocation is used.
    """
    combined_scope = {"ELEC": 0.0, "RFNBO": 0.0, "BIO": 0.0, "HSFO": 0.0, "LFO": 0.0, "MGO": 0.0}
    combined_all = {"ELEC": 0.0, "RFNBO": 0.0, "BIO": 0.0, "HSFO": 0.0, "LFO": 0.0, "MGO": 0.0}

    for seg in segments:
        energies_all = _segment_energy_mj(seg, LCV)
        energies_scope, elec_mj = _segment_scope_with_toggle(seg, energies_all, wtw)

        for f in FUELS:
            combined_all[f] += float(energies_all.get(f, 0.0) or 0.0)
            combined_scope[f] += float(energies_scope.get(f, 0.0) or 0.0)

        combined_all["ELEC"] += elec_mj
        combined_scope["ELEC"] += elec_mj

    E_scope_raw = sum(combined_scope.values())
    if E_scope_raw <= 0.0:
        return 0.0, 0.0, 0.0

    if _has_prioritized_segments(segments):
        combined_scope_final = _global_rearrange_scope(combined_all, combined_scope, wtw)
    else:
        combined_scope_final = combined_scope

    E_scope = sum(combined_scope_final.values())
    num_phys = sum(combined_scope_final.get(k, 0.0) * wtw.get(k, 0.0) for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO", "ELEC"])
    E_rfnbo_scope = combined_scope_final.get("RFNBO", 0.0)

    return E_scope, num_phys, E_rfnbo_scope


def _scope_and_balance_from_segments(
    year_idx: int,
    segments_mod: List[Dict[str, Any]],
    LCV: Dict[str, float],
    wtw: Dict[str, float],
    carry_in_list: List[float],
    pooling_start_year: int,
    pooling_tco2e_input: float,
    banking_start_year: int,
    banking_tco2e_input: float,
) -> Tuple[float, float, float, float]:
    """
    Returns:
      (g_att, E_scope, final_balance_tCO2e, pooling_used_tCO2e)
    """
    year = YEARS[year_idx]
    E_scope, num_phys, E_rfnbo_scope = _scope_from_segments(segments_mod, LCV, wtw)
    if E_scope <= 0.0:
        return 0.0, 0.0, 0.0, 0.0

    r = 2.0 if year <= 2033 else 1.0
    den_rwd = E_scope + (r - 1.0) * E_rfnbo_scope
    g_att = (num_phys / den_rwd) if den_rwd > 0 else 0.0

    g_target = float(LIMITS_DF["Limit_gCO2e_per_MJ"].iloc[year_idx])
    CB_t_raw = ((g_target - g_att) * E_scope) / 1e6
    cb_eff = CB_t_raw + carry_in_list[year_idx]

    # Pooling
    if year >= int(pooling_start_year):
        if pooling_tco2e_input >= 0:
            pre_deficit = max(-cb_eff, 0.0)
            pool_use = min(pooling_tco2e_input, pre_deficit)
        else:
            provide_abs = abs(pooling_tco2e_input)
            pre_surplus = max(cb_eff, 0.0)
            pool_use = -min(provide_abs, pre_surplus)
    else:
        pool_use = 0.0

    # Banking
    if year >= int(banking_start_year):
        req = max(float(banking_tco2e_input), 0.0)
        pre_surplus = max(cb_eff, 0.0)
        bank_use = min(req, pre_surplus)
    else:
        bank_use = 0.0

    # Clamp
    final_bal = cb_eff + pool_use - bank_use
    if final_bal < 0:
        needed = -final_bal
        trim_bank = min(needed, bank_use)
        bank_use -= trim_bank
        needed -= trim_bank
        if needed > 0 and pool_use < 0:
            pool_use += needed
        final_bal = cb_eff + pool_use - bank_use

    return g_att, E_scope, final_bal, pool_use


# =============================================================================
# “About / terms / footer”
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
APP_OWNER = "Nikitas Eleftheriou"
APP_CONTACT = "ops@example.com"
APP_VERSION = "2.0"
APP_DATE = "2026-02-12"

st.title("FuelEU Maritime & EU ETS — Voyage Segments")
st.caption("2025–2050 • WtW intensity • Pooling/Banking • EU ETS maritime (CO₂e from 2026+) • Optimizer & policy comparison")

show_trial_header(APP_OWNER, APP_CONTACT, APP_VERSION, APP_DATE)

# =============================================================================
# Regulatory section (improved and clearer)
# =============================================================================
with st.expander("Regulatory basis (high-level)", expanded=False):
    st.markdown(
        """
This app implements a **practical, operator-friendly** representation of:

**FuelEU Maritime**
- GHG intensity framework using **Well-to-Wake (WtW)** intensities and energy (MJ) via LCV.
- Stepwise limit pathway (2025–2050) applied against a 2020 baseline.
- RFNBO reward factor (in this model: 2 until 2033, then 1 from 2034).

**EU ETS (maritime extension)**
- Geographic scope logic consistent with common shipping ETS practice:
  - **Intra-EU** and **EU at-berth**: 100%
  - **Cross-border EU↔non-EU**: 50%
- **2025 coverage factor** in this model: 70%
- **2026+ coverage factor** in this model: 100%
- Emissions accounted as **tCO₂e** from 2026 onward (CO₂ + CH₄ + N₂O using **GWP100 AR5-type** factors),
  while 2025 is treated as **CO₂ only** in this model.

**Important**
- This is a demo / planning tool. Final compliance and reporting must follow the final text, implementing acts,
  and company-approved methodologies.
"""
    )

# =============================================================================
# Sidebar (structured + scenario manager)
# =============================================================================
with st.sidebar:
    _ensure_segments_state()

    st.markdown('<div class="card"><h4>Quick actions</h4>', unsafe_allow_html=True)
    qa1, qa2 = st.columns(2)
    with qa1:
        if st.button("➕ Add segment"):
            st.session_state["abs_segments"].append(_default_segment())
    with qa2:
        if st.button("🧹 Clear segments"):
            st.session_state["abs_segments"] = []
    st.markdown("</div>", unsafe_allow_html=True)

    # Scenario manager (surprise)
    st.markdown('<div class="card"><h4>Scenario manager</h4><div class="help">Save/load full input sets (segments + parameters). Useful for “Base”, “High EUA”, “BIO cheap”, etc.</div>', unsafe_allow_html=True)
    scenario_names = sorted(list(SCENARIOS.keys()))
    pick = st.selectbox("Load scenario", options=["(none)"] + scenario_names, index=0)
    sc1, sc2 = st.columns(2)
    with sc1:
        if st.button("Load", disabled=(pick == "(none)")):
            payload = SCENARIOS.get(pick, {})
            if isinstance(payload, dict):
                # Load session fields
                st.session_state["abs_segments"] = payload.get("abs_segments", st.session_state.get("abs_segments", []))
                for k, v in payload.get("session_state", {}).items():
                    st.session_state[k] = v
                st.success(f"Loaded scenario: {pick}")
                st.rerun()
    with sc2:
        name_to_save = st.text_input("Save as", value="")
        if st.button("Save"):
            nm = (name_to_save or "").strip()
            if not nm:
                st.warning("Enter a scenario name.")
            else:
                # Save only keys we manage (to avoid leaking sensitive)
                session_keys = [
                    # LCV
                    "LCV_HSFO","LCV_LFO","LCV_MGO","LCV_BIO","LCV_RFNBO",
                    # WtW
                    "WtW_HSFO","WtW_LFO","WtW_MGO","WtW_BIO","WtW_RFNBO",
                    # Prices/settings
                    "credit_per_tco2e_str","penalty_per_vlsfo_t_str","bio_premium_eur_per_t",
                    "eua_year_selection","eua_price_eur_per_tco2",
                    "pooling_price_eur_per_tco2e","POOL_T","pooling_start_year",
                    "BANK_T","banking_start_year",
                    "consecutive_deficit_years","opt_reduce_fuel",
                    "pure_bio_pct","bio_mix_type",
                ]
                SCENARIOS[nm] = {
                    "abs_segments": st.session_state.get("abs_segments", []),
                    "session_state": {k: st.session_state.get(k) for k in session_keys if k in st.session_state},
                    "saved_at_utc": _now_utc().isoformat(),
                }
                if _safe_write_json(SCENARIOS_PATH, SCENARIOS):
                    st.success(f"Saved scenario: {nm}")
                else:
                    st.error("Could not save scenarios file.")
    st.markdown("</div>", unsafe_allow_html=True)

    # Segments editor
    st.markdown(
        '<div class="card"><h4>Voyage segments</h4>'
        '<div class="help">Add voyage legs and EU at-berth stays. OPS is only for EU at-berth. '
        'Cross-border legs have an optional “prioritized allocation” toggle (WtW order).</div>',
        unsafe_allow_html=True,
    )

    to_remove: List[int] = []
    for i, seg in enumerate(st.session_state["abs_segments"]):
        with st.expander(f"Segment {i+1}", expanded=(i < 2)):
            seg["type"] = st.selectbox(
                "Type",
                SEG_TYPES,
                index=SEG_TYPES.index(seg.get("type", SEG_TYPES[0])),
                key=f"seg_type_{i}",
            )

            if seg["type"] in ("EU→non-EU voyage", "non-EU→EU voyage"):
                seg["prio_on"] = st.checkbox(
                    "Apply prioritized allocation (cross-border)",
                    value=bool(seg.get("prio_on", True)),
                    key=f"seg_prio_{i}",
                    help="If ON: 50% in-scope pool is filled by ascending WtW across all fuels.",
                )

            cA, cB = st.columns(2)
            with cA:
                seg["HSFO_t"] = float_text_input("HSFO [t]", seg.get("HSFO_t", 0.0), key=f"seg_hsfo_{i}", min_value=0.0)
                seg["MGO_t"] = float_text_input("MGO [t]", seg.get("MGO_t", 0.0), key=f"seg_mgo_{i}", min_value=0.0)
                seg["RFNBO_t"] = float_text_input("RFNBO [t]", seg.get("RFNBO_t", 0.0), key=f"seg_rfn_{i}", min_value=0.0)
            with cB:
                seg["LFO_t"] = float_text_input("LFO [t]", seg.get("LFO_t", 0.0), key=f"seg_lfo_{i}", min_value=0.0)
                seg["BIO_t"] = float_text_input("BIO [t]", seg.get("BIO_t", 0.0), key=f"seg_bio_{i}", min_value=0.0)

            if seg["type"] == "EU at-berth (port stay)":
                seg["OPS_kWh"] = float_text_input(
                    "EU OPS electricity [kWh]",
                    seg.get("OPS_kWh", 0.0),
                    key=f"seg_ops_{i}",
                    min_value=0.0,
                    help="Onshore Power Supply consumption during EU at-berth.",
                )
                st.text_input(
                    "Electricity (MJ) (derived)",
                    value=us2(float(seg["OPS_kWh"]) * 3.6),
                    disabled=True,
                    key=f"seg_ops_mj_{i}",
                )

            if st.button("Remove segment", key=f"seg_remove_{i}"):
                to_remove.append(i)

    if to_remove:
        st.session_state["abs_segments"] = [s for j, s in enumerate(st.session_state["abs_segments"]) if j not in to_remove]
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    # Fuel properties
    st.markdown('<div class="card"><h4>Fuel properties</h4>', unsafe_allow_html=True)

    st.markdown("**Lower Heating Values (LCV)** [MJ/t]")
    lcv_c1, lcv_c2, lcv_c3 = st.columns(3)
    with lcv_c1:
        _ = float_text_input("HSFO LCV", _get(DEFAULTS, "LCV_HSFO", 40_200.0), key="LCV_HSFO", min_value=0.0)
    with lcv_c2:
        _ = float_text_input("LFO LCV", _get(DEFAULTS, "LCV_LFO", 41_200.0), key="LCV_LFO", min_value=0.0)
    with lcv_c3:
        _ = float_text_input("MGO LCV", _get(DEFAULTS, "LCV_MGO", 42_700.0), key="LCV_MGO", min_value=0.0)
    lcv_c4, lcv_c5 = st.columns(2)
    with lcv_c4:
        _ = float_text_input("BIO LCV", _get(DEFAULTS, "LCV_BIO", 39_800.0), key="LCV_BIO", min_value=0.0)
    with lcv_c5:
        _ = float_text_input("RFNBO LCV", _get(DEFAULTS, "LCV_RFNBO", 30_000.0), key="LCV_RFNBO", min_value=0.0)

    st.markdown("---")

    st.markdown("**Well-to-Wake (WtW) intensities** [gCO₂e/MJ]")
    wtw_c1, wtw_c2, wtw_c3 = st.columns(3)
    with wtw_c1:
        _ = float_text_input("HSFO WtW", _get(DEFAULTS, "WtW_HSFO", 91.74), key="WtW_HSFO", min_value=0.0)
    with wtw_c2:
        _ = float_text_input("LFO WtW", _get(DEFAULTS, "WtW_LFO", 91.39), key="WtW_LFO", min_value=0.0)
    with wtw_c3:
        _ = float_text_input("MGO WtW", _get(DEFAULTS, "WtW_MGO", 90.77), key="WtW_MGO", min_value=0.0)
    wtw_c4, wtw_c5 = st.columns(2)
    with wtw_c4:
        _ = float_text_input("BIO WtW", _get(DEFAULTS, "WtW_BIO", 70.37), key="WtW_BIO", min_value=0.0)
    with wtw_c5:
        _ = float_text_input("RFNBO WtW", _get(DEFAULTS, "WtW_RFNBO", 20.00), key="WtW_RFNBO", min_value=0.0)

    st.markdown("</div>", unsafe_allow_html=True)

    # Other settings
    st.markdown('<div class="card"><h4>Other settings</h4>', unsafe_allow_html=True)
    consecutive_deficit_years_seed = int(
        st.number_input(
            "Consecutive deficit years (seed)",
            min_value=1,
            value=int(_get(DEFAULTS, "consecutive_deficit_years", 1)),
            step=1,
            key="consecutive_deficit_years",
            help="Used in your penalty step-multiplier logic.",
        )
    )
    opt_fuels = ["HSFO", "LFO", "MGO"]
    idx = opt_fuels.index(_get(DEFAULTS, "opt_reduce_fuel", "HSFO")) if _get(DEFAULTS, "opt_reduce_fuel", "HSFO") in opt_fuels else 0
    selected_fuel_for_opt = st.selectbox("Fuel to reduce (optimizer)", opt_fuels, index=idx, key="opt_reduce_fuel")
    st.markdown("</div>", unsafe_allow_html=True)

    # ETS: BIO blend settings
    st.markdown('<div class="ets-section">', unsafe_allow_html=True)
    pure_bio_pct = st.number_input(
        "Pure BIO in delivered blend (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(_get(DEFAULTS, "pure_bio_pct", 100.0)),
        step=1.0,
        key="pure_bio_pct",
        help="Example: B30 => 30. The remaining share is treated as fossil under ETS.",
    )
    bio_mix_type = st.selectbox(
        "Bio Mix Type (fossil share assigned to)",
        options=["BIO/HSFO mix", "BIO/LFO mix", "BIO/MGO mix"],
        index=1,
        key="bio_mix_type",
        help="If BIO is blended, the fossil share is mapped to this fossil for ETS emissions.",
    )

    if pure_bio_pct < 100.0:
        expected_mix = "BIO/HSFO mix" if selected_fuel_for_opt == "HSFO" else ("BIO/LFO mix" if selected_fuel_for_opt == "LFO" else "BIO/MGO mix")
        if bio_mix_type != expected_mix:
            st.warning(
                f"ETS consistency: for optimizer fuel '{selected_fuel_for_opt}', the consistent blend mapping is '{expected_mix}'. "
                f"Current: '{bio_mix_type}'.",
                icon="⚠️",
            )
    st.markdown("</div>", unsafe_allow_html=True)

    # Market prices
    st.markdown('<div class="card"><h4>Market prices</h4>', unsafe_allow_html=True)
    credit_per_tco2e = float_text_input("FuelEU credit price [€/tCO₂e]", _get(DEFAULTS, "credit_per_tco2e", 200.0), key="credit_per_tco2e_str", min_value=0.0)
    penalty_price_eur_per_vlsfo_t = float_text_input("FuelEU penalty price [€/VLSFO-eq t]", _get(DEFAULTS, "penalty_price_eur_per_vlsfo_t", 2_400.0), key="penalty_per_vlsfo_t_str", min_value=0.0)

    bio_premium_label = f"Premium BIO vs {selected_fuel_for_opt} [€/t]"
    bio_premium_eur_per_t = float_text_input(
        bio_premium_label,
        _get(DEFAULTS, "bio_premium_eur_per_t", _get(DEFAULTS, "bio_premium_usd_per_t", 300.0)),
        key="bio_premium_eur_per_t",
        min_value=0.0,
        help="Used in total cost calculations (base + optimizer + simulation).",
    )

    eua_col_year, eua_col_price = st.columns([1, 1])
    with eua_col_year:
        eua_year_selection = st.selectbox("EUAs year selector", options=["2025", "2026+"], key="eua_year_selection")
    with eua_col_price:
        eua_price_eur_per_tco2 = float_text_input("EUA price [€/tCO₂e]", _get(DEFAULTS, "eua_price_eur_per_tco2", 87.0), key="eua_price_eur_per_tco2", min_value=0.0)
    st.markdown("</div>", unsafe_allow_html=True)

    # Pooling/Banking
    st.markdown('<div class="card"><h4>Banking & Pooling (tCO₂e)</h4>', unsafe_allow_html=True)
    pooling_price_eur_per_tco2e = float_text_input("Pooling price [€/tCO₂e]", _get(DEFAULTS, "pooling_price_eur_per_tco2e", 200.0), key="pooling_price_eur_per_tco2e", min_value=0.0)
    pooling_tco2e_input = float_text_input_signed(
        "Pooling [tCO₂e] (+ uptake / − provide)",
        _get(DEFAULTS, "pooling_tco2e", 0.0),
        key="POOL_T",
        help="Uptake covers deficits; Provide supplies from surplus (both capped).",
    )
    pooling_start_year = st.selectbox("Pooling starts from year", YEARS, index=YEARS.index(int(_get(DEFAULTS, "pooling_start_year", YEARS[0]))), key="pooling_start_year")

    banking_tco2e_input = float_text_input("Banking to next year [tCO₂e]", _get(DEFAULTS, "banking_tco2e", 0.0), key="BANK_T", min_value=0.0)
    banking_start_year = st.selectbox("Banking starts from year", YEARS, index=YEARS.index(int(_get(DEFAULTS, "banking_start_year", YEARS[0]))), key="banking_start_year")
    st.markdown("</div>", unsafe_allow_html=True)

    # Save defaults
    if st.button("💾 Save current inputs as defaults"):
        defaults_to_save = {
            "credit_per_tco2e": credit_per_tco2e,
            "penalty_price_eur_per_vlsfo_t": penalty_price_eur_per_vlsfo_t,
            "bio_premium_eur_per_t": bio_premium_eur_per_t,
            "pooling_price_eur_per_tco2e": pooling_price_eur_per_tco2e,
            "banking_tco2e": banking_tco2e_input,
            "pooling_tco2e": pooling_tco2e_input,
            "pooling_start_year": int(pooling_start_year),
            "banking_start_year": int(banking_start_year),
            "consecutive_deficit_years": int(consecutive_deficit_years_seed),
            "opt_reduce_fuel": selected_fuel_for_opt,
            "pure_bio_pct": float(pure_bio_pct),
            "bio_mix_type": bio_mix_type,
            "eua_price_eur_per_tco2": eua_price_eur_per_tco2,
            "LCV_HSFO": ss_float("LCV_HSFO", 40200.0),
            "LCV_LFO": ss_float("LCV_LFO", 41200.0),
            "LCV_MGO": ss_float("LCV_MGO", 42700.0),
            "LCV_BIO": ss_float("LCV_BIO", 39800.0),
            "LCV_RFNBO": ss_float("LCV_RFNBO", 30000.0),
            "WtW_HSFO": ss_float("WtW_HSFO", 91.74),
            "WtW_LFO": ss_float("WtW_LFO", 91.39),
            "WtW_MGO": ss_float("WtW_MGO", 90.77),
            "WtW_BIO": ss_float("WtW_BIO", 70.37),
            "WtW_RFNBO": ss_float("WtW_RFNBO", 20.00),
            "abs_segments": st.session_state.get("abs_segments", []),
        }
        if _safe_write_json(DEFAULTS_PATH, defaults_to_save):
            st.success("Defaults saved.")
        else:
            st.error("Could not save defaults.")

# =============================================================================
# Live inputs (LCV / WtW dicts)
# =============================================================================
LCV_HSFO = ss_float("LCV_HSFO", 40200.0)
LCV_LFO = ss_float("LCV_LFO", 41200.0)
LCV_MGO = ss_float("LCV_MGO", 42700.0)
LCV_BIO = ss_float("LCV_BIO", 39800.0)
LCV_RFNBO = ss_float("LCV_RFNBO", 30000.0)

WtW_HSFO = ss_float("WtW_HSFO", 91.74)
WtW_LFO = ss_float("WtW_LFO", 91.39)
WtW_MGO = ss_float("WtW_MGO", 90.77)
WtW_BIO = ss_float("WtW_BIO", 70.37)
WtW_RFNBO = ss_float("WtW_RFNBO", 20.00)

LCV = {"HSFO": LCV_HSFO, "LFO": LCV_LFO, "MGO": LCV_MGO, "BIO": LCV_BIO, "RFNBO": LCV_RFNBO}
wtw = {"HSFO": WtW_HSFO, "LFO": WtW_LFO, "MGO": WtW_MGO, "BIO": WtW_BIO, "RFNBO": WtW_RFNBO, "ELEC": 0.0}

# =============================================================================
# Build totals from segments
# =============================================================================
totals_mass, ops_kwh_total = _segments_totals_masses_and_ops()
ELEC_MJ_input = ops_kwh_total * 3.6

bio_mass_total_t_base = totals_mass["intra_voy"]["BIO"] + totals_mass["extra_voy"]["BIO"] + totals_mass["eu_berth"]["BIO"]

# =============================================================================
# Main body layout (tabs)
# =============================================================================
tab_overview, tab_segments, tab_results, tab_sim = st.tabs(
    ["Overview", "Segments", "Results & Optimizer", "Simulation (BIO vs Pooling)"]
)

# =============================================================================
# OVERVIEW TAB
# =============================================================================
with tab_overview:
    if not st.session_state.get("abs_segments"):
        st.info("No segments yet. Use the sidebar to add at least one voyage / berth segment.")
    else:
        # Build combined all + in-scope stacks from segments
        combined_all = {"ELEC": 0.0, "RFNBO": 0.0, "BIO": 0.0, "HSFO": 0.0, "LFO": 0.0, "MGO": 0.0}
        combined_scope = {"ELEC": 0.0, "RFNBO": 0.0, "BIO": 0.0, "HSFO": 0.0, "LFO": 0.0, "MGO": 0.0}

        for seg in st.session_state["abs_segments"]:
            energies_all = _segment_energy_mj(seg, LCV)
            energies_scope, elec_mj_seg = _segment_scope_with_toggle(seg, energies_all, wtw)

            for f in FUELS:
                combined_all[f] += float(energies_all.get(f, 0.0) or 0.0)
                combined_scope[f] += float(energies_scope.get(f, 0.0) or 0.0)

            combined_all["ELEC"] += elec_mj_seg
            combined_scope["ELEC"] += elec_mj_seg

        if _has_prioritized_segments(st.session_state.get("abs_segments", [])):
            combined_scope_final = _global_rearrange_scope(combined_all, combined_scope, wtw)
        else:
            combined_scope_final = combined_scope

        E_total_MJ = sum(combined_all.values())
        E_scope_MJ = sum(combined_scope_final.values())

        # Attained intensity helper (uses combined in-scope stack)
        num_phys = sum(combined_scope_final.get(k, 0.0) * wtw.get(k, 0.0) for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO", "ELEC"])
        E_rfnbo_scope = combined_scope_final.get("RFNBO", 0.0)

        def attained_intensity_for_year(y: int) -> float:
            if E_scope_MJ <= 0:
                return 0.0
            r = 2.0 if int(y) <= 2033 else 1.0
            den = E_scope_MJ + (r - 1.0) * E_rfnbo_scope
            return (num_phys / den) if den > 0 else 0.0

        # Derived price factor preview (r=2 preview)
        if E_scope_MJ > 0:
            den_preview = E_scope_MJ + E_rfnbo_scope
            g_preview = (num_phys / den_preview) if den_preview > 0 else 0.0
        else:
            g_preview = 0.0
        if g_preview <= 0:
            g_preview = BASELINE_2020_GFI

        tco2e_per_vlsfo_t = (g_preview * 41_000.0) / 1_000_000.0

        st.subheader("Key metrics")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        with m1:
            st.metric("Total energy (all)", f"{us2(E_total_MJ)} MJ")
        with m2:
            st.metric("In-scope energy", f"{us2(E_scope_MJ)} MJ")
        with m3:
            st.metric("Attained intensity (2025)", f"{us2(attained_intensity_for_year(2025))} gCO₂e/MJ")
        with m4:
            st.metric("Attained intensity (2030)", f"{us2(attained_intensity_for_year(2030))} gCO₂e/MJ")
        with m5:
            st.metric("Credit €/VLSFO-eq t", us2(parse_us_any(st.session_state.get("credit_per_tco2e_str", 200.0), 200.0) * tco2e_per_vlsfo_t))
        with m6:
            pen_vlsfo = parse_us_any(st.session_state.get("penalty_per_vlsfo_t_str", 2400.0), 2400.0)
            st.metric("Penalty €/tCO₂e", us2((pen_vlsfo / tco2e_per_vlsfo_t) if tco2e_per_vlsfo_t > 0 else 0.0))

        st.markdown("### Combined energy (All vs In-scope)")

        categories = ["All energy", "In-scope energy"]
        fuels_sorted = ["ELEC"] + sorted(FUELS, key=lambda f: wtw.get(f, float("inf")))

        left_vals = {
            "ELEC": combined_all["ELEC"],
            "RFNBO": combined_all["RFNBO"],
            "BIO": combined_all["BIO"],
            "HSFO": combined_all["HSFO"],
            "LFO": combined_all["LFO"],
            "MGO": combined_all["MGO"],
        }
        right_vals = {
            "ELEC": combined_scope_final["ELEC"],
            "RFNBO": combined_scope_final["RFNBO"],
            "BIO": combined_scope_final["BIO"],
            "HSFO": combined_scope_final["HSFO"],
            "LFO": combined_scope_final["LFO"],
            "MGO": combined_scope_final["MGO"],
        }

        fig = go.Figure()
        for k in fuels_sorted:
            fig.add_trace(
                go.Bar(
                    x=categories,
                    y=[left_vals.get(k, 0.0), right_vals.get(k, 0.0)],
                    name=("ELEC (OPS)" if k == "ELEC" else k),
                    marker_color=COLORS.get(k, None),
                    hovertemplate=f"{k}<br>%{{x}}<br>%{{y:,.2f}} MJ<extra></extra>",
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

        st.caption("If any cross-border segment uses prioritized allocation, the combined in-scope mix is globally rearranged by ascending WtW (ELEC fixed).")

        st.markdown("### GHG Intensity vs FuelEU Limit (2025–2050)")
        years = LIMITS_DF["Year"].tolist()
        limit_series = LIMITS_DF["Limit_gCO2e_per_MJ"].tolist()
        actual_series = [attained_intensity_for_year(y) for y in years]

        fig2 = go.Figure()
        fig2.add_trace(
            go.Scatter(
                x=years,
                y=limit_series,
                name="FuelEU limit (step)",
                mode="lines+markers",
                line=dict(shape="hv", width=3),
                hovertemplate="Year=%{x}<br>Limit=%{y:,.2f} gCO₂e/MJ<extra></extra>",
            )
        )
        fig2.add_trace(
            go.Scatter(
                x=years,
                y=actual_series,
                name="Attained (combined in-scope)",
                mode="lines",
                line=dict(dash="dash", width=3),
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
    st.subheader("Per-segment energy (All vs In-scope)")
    if not st.session_state.get("abs_segments"):
        st.info("No segments yet. Add segments from the sidebar.")
    else:
        def _stack_with_arrows(title: str, left_vals: Dict[str, float], right_vals: Dict[str, float], show_elec: bool):
            categories = ["All", "In-scope"]
            fuels_sorted_local = sorted(FUELS, key=lambda f: wtw.get(f, float("inf")))
            stack_layers = ([("ELEC", "ELEC (OPS)")] if show_elec else []) + [(f, f) for f in fuels_sorted_local]

            fig = go.Figure()
            for key, label in stack_layers:
                fig.add_trace(
                    go.Bar(
                        x=categories,
                        y=[left_vals.get(key, 0.0), right_vals.get(key, 0.0)],
                        name=label,
                        marker_color=COLORS.get(key, None),
                        hovertemplate=f"{label}<br>%{{x}}<br>%{{y:,.2f}} MJ<extra></extra>",
                    )
                )

            total_all = sum(left_vals.get(k, 0.0) for k, _ in stack_layers)
            total_scope = sum(right_vals.get(k, 0.0) for k, _ in stack_layers)
            fig.add_annotation(x=categories[0], y=total_all, text=f"{us2(total_all)} MJ", showarrow=False, yshift=10)
            fig.add_annotation(x=categories[1], y=total_scope, text=f"{us2(total_scope)} MJ", showarrow=False, yshift=10)

            # arrows + retained %
            cum_left = 0.0
            cum_right = 0.0
            for key, _label in stack_layers:
                layer_left = float(left_vals.get(key, 0.0) or 0.0)
                layer_right = float(right_vals.get(key, 0.0) or 0.0)
                if layer_left <= 0 and layer_right <= 0:
                    cum_left += layer_left
                    cum_right += layer_right
                    continue
                y_center_left = cum_left + layer_left / 2.0
                y_center_right = cum_right + layer_right / 2.0

                fig.add_trace(
                    go.Scatter(
                        x=categories,
                        y=[y_center_left, y_center_right],
                        mode="lines",
                        line=dict(dash="dot", width=2),
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
                pct = (layer_right / layer_left * 100.0) if layer_left > 0 else 100.0
                pct = max(min(pct, 100.0), 0.0)
                y_mid = 0.5 * (y_center_left + y_center_right)
                fig.add_annotation(
                    xref="paper",
                    yref="y",
                    x=0.5,
                    y=y_mid,
                    text=f"{pct:.0f}%",
                    showarrow=False,
                    bgcolor="rgba(255,255,255,0.65)",
                )

                cum_left += layer_left
                cum_right += layer_right

            fig.update_layout(
                title=dict(text=title, x=0.02, y=0.95, font=dict(size=13)),
                barmode="stack",
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                margin=dict(l=40, r=20, t=45, b=20),
                height=270,
                yaxis_title="Energy [MJ]",
            )
            st.plotly_chart(fig, use_container_width=True)

        for i, seg in enumerate(st.session_state["abs_segments"]):
            energies_all = _segment_energy_mj(seg, LCV)
            energies_scope, elec_mj_seg = _segment_scope_with_toggle(seg, energies_all, wtw)

            left_vals = dict(energies_all)
            right_vals = dict(energies_scope)

            show_elec = (seg.get("type") == "EU at-berth (port stay)")
            if show_elec:
                left_vals["ELEC"] = elec_mj_seg
                right_vals["ELEC"] = elec_mj_seg

            _stack_with_arrows(f"Segment {i+1}: {seg.get('type','')}", left_vals, right_vals, show_elec)

# =============================================================================
# RESULTS & OPTIMIZER TAB
# =============================================================================
with tab_results:
    st.subheader("Results (per year, merged table)")

    if not st.session_state.get("abs_segments"):
        st.info("Add at least one segment to compute results.")
    else:
        # Prepare combined in-scope intensity for base case via _scope_from_segments
        E_scope_base, num_phys_base, E_rfnbo_scope_base = _scope_from_segments(st.session_state["abs_segments"], LCV, wtw)

        def attained_intensity_base(y: int) -> float:
            if E_scope_base <= 0:
                return 0.0
            r = 2.0 if int(y) <= 2033 else 1.0
            den = E_scope_base + (r - 1.0) * E_rfnbo_scope_base
            return (num_phys_base / den) if den > 0 else 0.0

        # Base ETS masses/cost series (based on totals_mass)
        ets_masses = _ets_in_scope_masses(totals_mass, float(pure_bio_pct), bio_mix_type)
        ets_emissions_series = []
        ets_cost_series = []
        for y in YEARS:
            geo = _ets_geo_emissions_tco2e_from_masses(ets_masses, int(y))
            cov = 0.70 if int(y) == 2025 else 1.00
            em = geo * cov
            ets_emissions_series.append(em)
            ets_cost_series.append(em * float(eua_price_eur_per_tco2))

        # FuelEU balances loop
        cb_raw_t, carry_in_list, cb_eff_t = [], [], []
        pool_applied, bank_applied = [], []
        final_balance_t, penalties_eur, credits_eur = [], [], []
        g_att_list = []

        carry = 0.0
        fixed_multiplier_by_step: Dict[int, float] = {}

        pooling_start_year_i = int(pooling_start_year)
        banking_start_year_i = int(banking_start_year)
        pooling_tco2e_val = float(pooling_tco2e_input)
        banking_tco2e_val = float(parse_us_any(st.session_state.get("BANK_T", banking_tco2e_input), banking_tco2e_input))
        pooling_price_eur_per_tco2e_val = float(pooling_price_eur_per_tco2e)
        credit_per_tco2e_val = float(credit_per_tco2e)
        penalty_vlsfo_val = float(penalty_price_eur_per_vlsfo_t)
        bio_premium_eur_per_t_val = float(bio_premium_eur_per_t)

        for _, row in LIMITS_DF.iterrows():
            year = int(row["Year"])
            g_target = float(row["Limit_gCO2e_per_MJ"])
            g_att = attained_intensity_base(year)
            g_att_list.append(g_att)

            CB_t_raw = ((g_target - g_att) * E_scope_base) / 1e6
            cb_raw_t.append(CB_t_raw)

            cb_eff = CB_t_raw + carry
            carry_in_list.append(carry)
            cb_eff_t.append(cb_eff)

            # Pooling
            if year >= pooling_start_year_i:
                if pooling_tco2e_val >= 0:
                    pre_deficit = max(-cb_eff, 0.0)
                    pool_use = min(pooling_tco2e_val, pre_deficit)
                else:
                    provide_abs = abs(pooling_tco2e_val)
                    pre_surplus = max(cb_eff, 0.0)
                    pool_use = -min(provide_abs, pre_surplus)
            else:
                pool_use = 0.0

            # Banking
            if year >= banking_start_year_i:
                requested_bank = max(banking_tco2e_val, 0.0)
                pre_surplus = max(cb_eff, 0.0)
                bank_use = min(requested_bank, pre_surplus)
            else:
                bank_use = 0.0

            # Clamp
            final_bal = cb_eff + pool_use - bank_use
            if final_bal < 0:
                needed = -final_bal
                trim_bank = min(needed, bank_use)
                bank_use -= trim_bank
                needed -= trim_bank
                if needed > 0 and pool_use < 0:
                    pool_use += needed
                final_bal = cb_eff + pool_use - bank_use

            carry = bank_use

            # Step-multiplier (constant per step if deficit)
            if final_bal < 0:
                step_idx = _step_of_year(year)
                if step_idx not in fixed_multiplier_by_step:
                    seed = max(int(consecutive_deficit_years_seed), 1)
                    fixed_multiplier_by_step[step_idx] = 1.0 + (seed - 1) * 0.10
                mult = fixed_multiplier_by_step[step_idx]
            else:
                mult = 1.0

            # Penalty/credit EUR
            if final_bal > 0:
                credit_val = final_bal * credit_per_tco2e_val
                penalty_val = 0.0
            elif final_bal < 0:
                penalty_val = euros_from_tco2e(-final_bal, g_att, penalty_vlsfo_val) * mult
                credit_val = 0.0
            else:
                penalty_val = 0.0
                credit_val = 0.0

            pool_applied.append(pool_use)
            bank_applied.append(bank_use)
            final_balance_t.append(final_bal)
            penalties_eur.append(penalty_val)
            credits_eur.append(credit_val)

        # Costs
        bio_premium_cost_eur_col = [bio_mass_total_t_base * bio_premium_eur_per_t_val] * len(YEARS)
        pooling_cost_eur_col = [pool_applied[i] * pooling_price_eur_per_tco2e_val for i in range(len(YEARS))]
        net_total_cost_eur_col = [
            penalties_eur[i] - credits_eur[i] + bio_premium_cost_eur_col[i] + pooling_cost_eur_col[i]
            for i in range(len(YEARS))
        ]

        # Optimizer (per year): minimize FuelEU + ETS
        dec_opt_list, bio_inc_opt_list, total_cost_eur_opt_col = [], [], []

        # Convenience for optimizer parameters
        pooling_tco2e_for_opt = float(pooling_tco2e_input)
        banking_tco2e_for_opt = float(parse_us_any(st.session_state.get("BANK_T", banking_tco2e_input), banking_tco2e_input))

        def _total_cost_for_x(year_idx: int, x: float, bio_premium_eur_per_t_local: float) -> float:
            segments_mod, _, _ = _apply_shift_to_segments(st.session_state["abs_segments"], selected_fuel_for_opt, x, LCV)

            g_att_x, E_scope_x, final_bal_x, pool_use_x = _scope_and_balance_from_segments(
                year_idx,
                segments_mod,
                LCV,
                wtw,
                carry_in_list,
                pooling_start_year_i,
                pooling_tco2e_for_opt,
                banking_start_year_i,
                banking_tco2e_for_opt,
            )
            if E_scope_x <= 0:
                return 0.0

            # penalty/credit
            if final_bal_x < 0:
                step_idx = _step_of_year(YEARS[year_idx])
                seed = max(int(consecutive_deficit_years_seed), 1)
                step_mult = 1.0 + (seed - 1) * 0.10
                penalty_eur_x = euros_from_tco2e(-final_bal_x, g_att_x, penalty_vlsfo_val) * step_mult
                credits_eur_x = 0.0
            else:
                penalty_eur_x = 0.0
                credits_eur_x = final_bal_x * credit_per_tco2e_val

            pooling_cost_x = pool_use_x * pooling_price_eur_per_tco2e_val

            bio_total_t_x = sum(float(seg.get("BIO_t", 0.0) or 0.0) for seg in segments_mod)
            bio_premium_cost_x = bio_total_t_x * bio_premium_eur_per_t_local

            ets_cost_x = _ets_cost_from_segments(
                segments_mod,
                float(pure_bio_pct),
                bio_mix_type,
                float(eua_price_eur_per_tco2),
                YEARS[year_idx],
            )

            return penalty_eur_x - credits_eur_x + pooling_cost_x + bio_premium_cost_x + ets_cost_x

        # Determine x_max (available selected fossil mass)
        def _available_mass_of_selected() -> float:
            total = 0.0
            for seg in st.session_state["abs_segments"]:
                total += float(seg.get(f"{selected_fuel_for_opt}_t", 0.0) or 0.0)
            return total

        x_max_global = _available_mass_of_selected()

        for i in range(len(YEARS)):
            if x_max_global <= 0 or LCV_BIO <= 0:
                dec_opt_list.append(0.0)
                bio_inc_opt_list.append(0.0)
                total_cost_eur_opt_col.append(net_total_cost_eur_col[i] + ets_cost_series[i])
                continue

            # Coarse + golden search
            steps_coarse = 200
            best_x, best_cost = 0.0, float("inf")
            for s in range(steps_coarse + 1):
                x = x_max_global * s / steps_coarse
                c = _total_cost_for_x(i, x, bio_premium_eur_per_t_val)
                if c < best_cost:
                    best_cost, best_x = c, x

            bin_w = x_max_global / steps_coarse
            a = max(0.0, best_x - 3 * bin_w)
            b = min(x_max_global, best_x + 3 * bin_w)

            phi = (5 ** 0.5 - 1) / 2.0
            tol = max(x_max_global * 1e-5, 1e-4)

            c = b - phi * (b - a)
            d = a + phi * (b - a)
            fc = _total_cost_for_x(i, c, bio_premium_eur_per_t_val)
            fd = _total_cost_for_x(i, d, bio_premium_eur_per_t_val)

            it, max_iter = 0, 120
            while (b - a) > tol and it < max_iter:
                if fc <= fd:
                    b, d, fd = d, c, fc
                    c = b - phi * (b - a)
                    fc = _total_cost_for_x(i, c, bio_premium_eur_per_t_val)
                else:
                    a, c, fc = c, d, fd
                    d = a + phi * (b - a)
                    fd = _total_cost_for_x(i, d, bio_premium_eur_per_t_val)
                it += 1

            x_opt = (a + b) / 2.0
            # BIO increase based on LCV ratio
            LCV_SEL = LCV[selected_fuel_for_opt]
            bio_inc = x_opt * (LCV_SEL / LCV_BIO) if LCV_BIO > 0 else 0.0

            dec_opt_list.append(x_opt)
            bio_inc_opt_list.append(bio_inc)
            total_cost_eur_opt_col.append(_total_cost_for_x(i, x_opt, bio_premium_eur_per_t_val))

        # Assemble DataFrame
        decrease_col_name = f"{selected_fuel_for_opt}_decrease(t)_for_Opt_Cost"
        emissions_tco2e_phys = (num_phys_base / 1e6) if num_phys_base > 0 else 0.0

        df_cost = pd.DataFrame(
            {
                "Year": YEARS,
                "Reduction_%": LIMITS_DF["Reduction_%"].tolist(),
                "Limit_gCO2e_per_MJ": LIMITS_DF["Limit_gCO2e_per_MJ"].tolist(),
                "Actual_gCO2e_per_MJ": [attained_intensity_base(y) for y in YEARS],
                "Emissions_tCO2e": [emissions_tco2e_phys] * len(YEARS),

                "Compliance_Balance_tCO2e": cb_raw_t,
                "CarryIn_Banked_tCO2e": carry_in_list,
                "Effective_Balance_tCO2e": cb_eff_t,
                "Banked_to_Next_Year_tCO2e": bank_applied,
                "Pooling_tCO2e_Applied": pool_applied,
                "Final_Balance_tCO2e": final_balance_t,

                "Pooling_Cost_EUR": pooling_cost_eur_col,
                "Penalty_EUR": penalties_eur,
                "Credit_EUR": credits_eur,
                "BIO_Premium_Cost_EUR": bio_premium_cost_eur_col,
                "Net_Total_FuelEU_Cost_EUR": net_total_cost_eur_col,

                "ETS_Emissions_tCO2e": ets_emissions_series,
                "ETS_Cost_EUR": ets_cost_series,
                "FuelEU_+_EU_ETS_Cost": [net_total_cost_eur_col[i] + ets_cost_series[i] for i in range(len(YEARS))],

                decrease_col_name: dec_opt_list,
                "BIO_Increase(t)_For_Opt_Cost": bio_inc_opt_list,
                "Total_Cost_FUEL_EU_ETS_Opt": total_cost_eur_opt_col,
            }
        )

        # Styling
        df_fmt = df_cost.copy()
        for col in df_fmt.columns:
            if col != "Year":
                df_fmt[col] = df_fmt[col].apply(us2)

        def _highlight_cols(col):
            if col.name in ["ETS_Emissions_tCO2e", "ETS_Cost_EUR"]:
                return ["background-color: #e0f2fe; font-weight: 650;"] * len(col)
            if col.name == "FuelEU_+_EU_ETS_Cost":
                return ["background-color: #fef9c3; font-weight: 650;"] * len(col)
            if col.name == "Total_Cost_FUEL_EU_ETS_Opt":
                return ["background-color: #dcfce7; font-weight: 700;"] * len(col)
            return [""] * len(col)

        st.dataframe(df_fmt.style.apply(_highlight_cols, axis=0), use_container_width=True)

        c_dl1, c_dl2 = st.columns([1, 1])
        with c_dl1:
            st.download_button(
                "Download results CSV",
                data=df_cost.to_csv(index=False),
                file_name="fueleu_ets_results_2025_2050.csv",
                mime="text/csv",
            )
        with c_dl2:
            # “Surprise”: one-click “input snapshot” export for audit trail
            snapshot = {
                "app_version": APP_VERSION,
                "saved_at_utc": _now_utc().isoformat(),
                "segments": st.session_state.get("abs_segments", []),
                "parameters": {
                    "LCV": LCV,
                    "WtW": {k: wtw[k] for k in ["HSFO","LFO","MGO","BIO","RFNBO"]},
                    "prices": {
                        "credit_per_tco2e": credit_per_tco2e_val,
                        "penalty_price_eur_per_vlsfo_t": penalty_vlsfo_val,
                        "bio_premium_eur_per_t": bio_premium_eur_per_t_val,
                        "pooling_price_eur_per_tco2e": pooling_price_eur_per_tco2e_val,
                        "eua_price_eur_per_tco2e": float(eua_price_eur_per_tco2),
                    },
                    "pooling": {"value_tco2e": pooling_tco2e_val, "start_year": pooling_start_year_i},
                    "banking": {"value_tco2e": banking_tco2e_val, "start_year": banking_start_year_i},
                    "ets_blend": {"pure_bio_pct": float(pure_bio_pct), "bio_mix_type": bio_mix_type},
                    "optimizer": {"fuel_to_reduce": selected_fuel_for_opt, "deficit_seed": int(consecutive_deficit_years_seed)},
                },
            }
            st.download_button(
                "Download input snapshot (JSON)",
                data=json.dumps(snapshot, indent=2),
                file_name="fueleu_ets_input_snapshot.json",
                mime="application/json",
            )

        st.info("Green columns show optimized total cost (FuelEU + ETS). Yellow shows base FuelEU+ETS cost. Blue shows ETS columns.", icon="ℹ️")

# =============================================================================
# SIMULATION TAB (BIO vs Pooling) — corrected pooling neutrality
# =============================================================================
with tab_sim:
    st.subheader("Interactive simulation: BIO optimization vs Pooling policy")

    if not st.session_state.get("abs_segments"):
        st.info("Add at least one segment to run the simulation.")
    else:
        # Controls
        with st.expander("Simulation controls", expanded=True):
            sim_year = st.selectbox("Year", YEARS, index=0, key="sim_year_bio_premium")

            c1, c2, c3 = st.columns(3)
            with c1:
                premium_min = float_text_input("BIO premium min [€/t]", 0.0, key="sim_premium_min", min_value=0.0)
            with c2:
                premium_max = float_text_input("BIO premium max [€/t]", 1_000.0, key="sim_premium_max", min_value=0.0)
            with c3:
                premium_step = float_text_input("BIO premium step [€/t]", 50.0, key="sim_premium_step", min_value=1.0)

            pooling_price_compare = float_text_input(
                "Pooling price for comparison [€/tCO₂e]",
                200.0,
                key="sim_pool_price",
                min_value=0.0,
                help="Used for the 'Pooling only' policy curve.",
            )

        # guards
        if premium_step <= 0:
            st.warning("BIO premium step must be > 0.")
        else:
            if premium_max < premium_min:
                premium_min, premium_max = premium_max, premium_min

            n_points = int((premium_max - premium_min) // premium_step) + 1
            n_points = max(n_points, 1)
            bio_premium_grid = [premium_min + i * premium_step for i in range(n_points)]

            year_idx_sim = YEARS.index(int(sim_year)) if int(sim_year) in YEARS else 0

            # Helper: total cost for candidate segments at candidate premium, with pooling NEUTRAL (no effect, no cost)
            def _total_cost_for_candidate_premium_no_pool(
                year_idx: int,
                segments_mod: List[Dict[str, Any]],
                bio_premium_candidate: float,
            ) -> float:
                # Force pooling neutral: both effect and cost = 0
                g_att_x, E_scope_x, final_bal_x, pool_use_x = _scope_and_balance_from_segments(
                    year_idx,
                    segments_mod,
                    LCV,
                    wtw,
                    carry_in_list=[0.0] * len(YEARS),  # policy sim: treat carry-in as 0 unless you want to reuse table
                    pooling_start_year=int(pooling_start_year),
                    pooling_tco2e_input=0.0,
                    banking_start_year=int(banking_start_year),
                    banking_tco2e_input=float(parse_us_any(st.session_state.get("BANK_T", 0.0), 0.0)),
                )
                if E_scope_x <= 0:
                    return 0.0

                # penalty/credit
                if final_bal_x < 0:
                    seed = max(int(st.session_state.get("consecutive_deficit_years", 1)), 1)
                    step_mult = 1.0 + (seed - 1) * 0.10
                    penalty_eur_x = euros_from_tco2e(-final_bal_x, g_att_x, float(parse_us_any(st.session_state.get("penalty_per_vlsfo_t_str", 2400.0), 2400.0))) * step_mult
                    credit_eur_x = 0.0
                else:
                    penalty_eur_x = 0.0
                    credit_eur_x = final_bal_x * float(parse_us_any(st.session_state.get("credit_per_tco2e_str", 200.0), 200.0))

                # BIO premium
                bio_total_t = sum(float(seg.get("BIO_t", 0.0) or 0.0) for seg in segments_mod)
                bio_cost = bio_total_t * float(bio_premium_candidate)

                # ETS cost
                ets_cost = _ets_cost_from_segments(
                    segments_mod,
                    float(pure_bio_pct),
                    bio_mix_type,
                    float(eua_price_eur_per_tco2),
                    YEARS[year_idx],
                )

                return penalty_eur_x - credit_eur_x + bio_cost + ets_cost

            # For each premium -> optimize x
            def _optimize_x_for_premium(year_idx: int, premium_candidate: float) -> float:
                # x_max available
                x_max = 0.0
                for seg in st.session_state["abs_segments"]:
                    x_max += float(seg.get(f"{selected_fuel_for_opt}_t", 0.0) or 0.0)
                if x_max <= 0 or LCV_BIO <= 0:
                    return 0.0

                def obj(x: float) -> float:
                    segs_mod, _, _ = _apply_shift_to_segments(st.session_state["abs_segments"], selected_fuel_for_opt, x, LCV)
                    return _total_cost_for_candidate_premium_no_pool(year_idx, segs_mod, premium_candidate)

                # coarse + golden
                steps = 200
                best_x, best_cost = 0.0, float("inf")
                for s in range(steps + 1):
                    x = x_max * s / steps
                    c = obj(x)
                    if c < best_cost:
                        best_cost, best_x = c, x

                bin_w = x_max / steps
                a = max(0.0, best_x - 3 * bin_w)
                b = min(x_max, best_x + 3 * bin_w)

                phi = (5 ** 0.5 - 1) / 2.0
                tol = max(x_max * 1e-5, 1e-4)
                c = b - phi * (b - a)
                d = a + phi * (b - a)
                fc = obj(c)
                fd = obj(d)

                it, max_iter = 0, 120
                while (b - a) > tol and it < max_iter:
                    if fc <= fd:
                        b, d, fd = d, c, fc
                        c = b - phi * (b - a)
                        fc = obj(c)
                    else:
                        a, c, fc = c, d, fd
                        d = a + phi * (b - a)
                        fd = obj(d)
                    it += 1

                return (a + b) / 2.0

            cost_opt_grid = []
            for prem in bio_premium_grid:
                x_opt = _optimize_x_for_premium(year_idx_sim, prem)
                segs_mod, _, _ = _apply_shift_to_segments(st.session_state["abs_segments"], selected_fuel_for_opt, x_opt, LCV)
                cost_opt_grid.append(_total_cost_for_candidate_premium_no_pool(year_idx_sim, segs_mod, prem))

            # Pooling-only curve:
            # Use base Final_Balance from a simplified base calc with current segments and then offset via pooling price.
            # Here we compute base balance for sim-year with pooling neutral in the base (then pooling cost represents policy).
            g_att0, E0, final_bal0, _ = _scope_and_balance_from_segments(
                year_idx_sim,
                st.session_state["abs_segments"],
                LCV,
                wtw,
                carry_in_list=[0.0] * len(YEARS),
                pooling_start_year=int(pooling_start_year),
                pooling_tco2e_input=0.0,
                banking_start_year=int(banking_start_year),
                banking_tco2e_input=float(parse_us_any(st.session_state.get("BANK_T", 0.0), 0.0)),
            )

            # pooling required to neutralize balance: pool_use = -final_balance
            pooling_cost_component = (-final_bal0) * float(pooling_price_compare)

            # ETS cost for base (year-aware)
            ets_cost_base = _ets_cost_from_segments(
                st.session_state["abs_segments"],
                float(pure_bio_pct),
                bio_mix_type,
                float(eua_price_eur_per_tco2),
                YEARS[year_idx_sim],
            )

            cost_pooling_grid = [bio_mass_total_t_base * prem + ets_cost_base + pooling_cost_component for prem in bio_premium_grid]

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=bio_premium_grid,
                    y=cost_opt_grid,
                    mode="lines+markers",
                    name="BIO optimization (pooling neutral)",
                    hovertemplate="Premium=%{x:,.0f} €/t<br>Total=%{y:,.0f} EUR<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=bio_premium_grid,
                    y=cost_pooling_grid,
                    mode="lines",
                    name=f"Pooling-only policy @ {float(pooling_price_compare):,.0f} €/tCO₂e",
                    line=dict(dash="dash"),
                    hovertemplate="Premium=%{x:,.0f} €/t<br>Total=%{y:,.0f} EUR<extra></extra>",
                )
            )

            # Reference line at current premium
            current_prem = float(parse_us_any(st.session_state.get("bio_premium_eur_per_t", bio_premium_eur_per_t), bio_premium_eur_per_t))
            if premium_min <= current_prem <= premium_max:
                fig.add_vline(x=current_prem, line=dict(dash="dot"), annotation_text="Current BIO premium", annotation_position="top left")

            fig.update_layout(
                hovermode="x unified",
                margin=dict(l=40, r=20, t=35, b=40),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                xaxis_title="Premium BIO vs selected fossil [€/t]",
                yaxis_title="Total cost [EUR] (FuelEU + ETS)",
            )
            st.plotly_chart(fig, use_container_width=True)

            st.caption("BIO-optimization curve is computed with pooling neutral (no pooling effect and no pooling cost). Pooling-only curve represents a policy: compensate base balance by pooling at a given price.")

# =============================================================================
# Footer
# =============================================================================
st.info("Public demo — non-production. Results are informational; no warranty.", icon="ℹ️")
show_trial_footer(APP_OWNER, APP_VERSION, APP_DATE)
st.caption("Built with Streamlit • By using this app you also accept Streamlit’s Terms and Privacy.")
