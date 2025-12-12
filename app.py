from __future__ import annotations
import json, os, copy
from typing import Dict, Any, Tuple, List
import pandas as pd

import plotly.graph_objects as go
import streamlit as st
 
# ↓↓↓ hardened shared-credentials login (cookie + session fallback)
from datetime import datetime, timedelta, timezone
import uuid
import extra_streamlit_components as stx
# ↑↑↑

# ──────────────────────────────────────────────────────────────────────────────
# Page config FIRST
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="FuelEU Maritime — Voyage Segments", layout="wide")
st.markdown("""
<style>
/* Hide the entire top-right toolbar (menu, rerun, GitHub link) */
[data-testid="stToolbar"] { visibility: hidden; height: 0; position: fixed; }

/* Hide the “Open in GitHub / Fork this app” badge (class names can vary by version) */
div[class^="viewerBadge_"],
div[class*=" viewerBadge_"] { display: none !important; }

/* Optional: remove header/decoration space for a cleaner top edge */
[data-testid="stDecoration"], [data-testid="header"] { display: none; }
</style>
""", unsafe_allow_html=True)


#*******************






# ──────────────────────────────────────────────────────────────────────────────
# LOGIN GATE — shared username/password with cookie + session fallback
# ──────────────────────────────────────────────────────────────────────────────
_cookie_mgr = stx.CookieManager(key="cookie_mgr")

# ensure the component is mounted once per run (prevents “button does nothing” on some setups)
try:
    _ = _cookie_mgr.get_all()
except Exception:
    pass

def _get_auth_config():
    auth = st.secrets.get("auth", {})
    return {
        "trial_cookie":   auth.get("trial_cookie_name", "fueleu_trial_id"),
        "session_cookie": auth.get("session_cookie_name", "fueleu_session"),
        "expiry_days":    int(auth.get("cookie_expiry_days", 14)),
        "username":       auth.get("username", "temp"),
        "password":       auth.get("password", "1234"),
    }

def _cookie_get(name: str):
    try:
        return _cookie_mgr.get(name)
    except Exception:
        return None

def _cookie_set(name: str, value: str, *, expires_days: int | None = None) -> bool:
    try:
        if expires_days is None:
            _cookie_mgr.set(name, value, key=f"k-{uuid.uuid4()}")
        else:
            _cookie_mgr.set(
                name, value,
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

def _now_utc():
    return datetime.now(timezone.utc)

def shared_creds_cookie_gate():
    """
    • Shared username/password (from secrets or defaults).
    • First successful login on a browser sets TRIAL cookie (14 days by default) — never deleted on Logout.
    • SESSION cookie controls the live session; Logout deletes only SESSION (not TRIAL).
    • Fallback: if cookies are blocked, allow login for the current tab via session_state.
    """
    cfg = _get_auth_config()
    trial_ck = cfg["trial_cookie"]; sess_ck = cfg["session_cookie"]; expiry_days = cfg["expiry_days"]

    # Fallback session flags (tab-local)
    if "_fallback_logged_in" not in st.session_state:
        st.session_state["_fallback_logged_in"] = False
    if "_fallback_trial_until" not in st.session_state:
        st.session_state["_fallback_trial_until"] = None

    # 1) Authenticated via cookies
    trial_tok = _cookie_get(trial_ck)
    sess_tok  = _cookie_get(sess_ck)
    if sess_tok and trial_tok:
        with st.sidebar:
            if st.button("Logout"):
                _cookie_del(sess_ck)  # keep trial cookie (preserves countdown)
                st.session_state["_fallback_logged_in"] = False
                st.session_state["_fallback_trial_until"] = None
                st.rerun()
        return  # allow app

    # 2) Session exists but trial missing → clear and force login
    if sess_tok and not trial_tok:
        _cookie_del(sess_ck)
        st.session_state["_fallback_logged_in"] = False
        st.session_state["_fallback_trial_until"] = None
        st.rerun()

    # 3) Cookie-less fallback (tab-local)
    if st.session_state["_fallback_logged_in"]:
        tu = st.session_state["_fallback_trial_until"]
        if isinstance(tu, str):
            try:
                tu = datetime.fromisoformat(tu)
                if tu.tzinfo is None:
                    tu = tu.replace(tzinfo=timezone.utc)
            except Exception:
                tu = None
        if tu and _now_utc() < tu:
            with st.sidebar:
                if st.button("Logout"):
                    st.session_state["_fallback_logged_in"] = False
                    st.session_state["_fallback_trial_until"] = None
                    st.rerun()
            return
        else:
            st.session_state["_fallback_logged_in"] = False
            st.session_state["_fallback_trial_until"] = None

    # 4) Login form
    st.title("Sign in")
    st.write("Enter the temporary credentials to access the app.")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    submit = st.button("Sign in", type="primary")

    if not submit:
        st.stop()

    # 5) Validate
    if not ((u == cfg["username"]) and (p == cfg["password"])):  # shared creds
        st.error("Invalid credentials.")
        st.stop()

    # 6) On success: set TRIAL (if missing) and SESSION; also set fallback for cookie-less environments
    trial_tok = _cookie_get(trial_ck)
    if not trial_tok:
        _cookie_set(trial_ck, str(uuid.uuid4()), expires_days=expiry_days)

    _cookie_set(sess_ck, str(uuid.uuid4()))  # session cookie (no explicit expires)

    st.session_state["_fallback_logged_in"] = True
    st.session_state["_fallback_trial_until"] = (
        (_now_utc() + timedelta(days=expiry_days)).isoformat()
        if not trial_tok else st.session_state.get("_fallback_trial_until")
    )

    st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# Constants & Assumptions
# ──────────────────────────────────────────────────────────────────────────────
BASELINE_2020_GFI = 91.16  # gCO2e/MJ
DEFAULTS_PATH = ".fueleu_defaults.json"

REDUCTION_STEPS = [
    (2025, 2029, 2.0),
    (2030, 2034, 6.0),
    (2035, 2039, 14.5),
    (2040, 2044, 31.0),
    (2045, 2049, 62.0),
    (2050, 2050, 80.0),
]
YEARS = list(range(2025, 2051))

def limits_by_year() -> pd.DataFrame:
    rows = []
    for y in YEARS:
        perc = next(p for s, e, p in REDUCTION_STEPS if s <= y <= e)
        limit = BASELINE_2020_GFI * (1 - perc / 100.0)
        rows.append({"Year": y, "Reduction_%": perc, "Limit_gCO2e_per_MJ": round(limit, 2)})
    return pd.DataFrame(rows)

LIMITS_DF = limits_by_year()

def _step_of_year(y: int) -> int:
    for i, (s, e, _) in enumerate(REDUCTION_STEPS):
        if s <= y <= e:
            return i
    return -1

# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────
def _load_defaults() -> Dict[str, Any]:
    if os.path.exists(DEFAULTS_PATH):
        try:
            with open(DEFAULTS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}
def _get(d: Dict[str, Any], key: str, fallback):
    return d.get(key, fallback)
DEFAULTS = _load_defaults()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def compute_energy_MJ(mass_t: float, lcv_MJ_per_t: float) -> float:
    mass_t = max(float(mass_t), 0.0)
    lcv = max(float(lcv_MJ_per_t), 0.0)
    return mass_t * lcv

def euros_from_tco2e(balance_tco2e_positive: float, g_attained: float, price_eur_per_vlsfo_t: float) -> float:
    """
    Convert a tCO2e amount into EUR using an equivalent VLSFO-tonne price and the attained intensity.
    """
    if balance_tco2e_positive <= 0 or price_eur_per_vlsfo_t <= 0 or g_attained <= 0:
        return 0.0
    tco2e_per_vlsfot = (g_attained * 41_000.0) / 1_000_000.0  # tCO2e per VLSFO-eq tonne at attained intensity
    if tco2e_per_vlsfot <= 0:
        return 0.0
    vlsfo_eq_t = balance_tco2e_positive / tco2e_per_vlsfot
    return vlsfo_eq_t * price_eur_per_vlsfo_t

def us2(x: float) -> str:
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return x
def parse_us(s: str, default: float = 0.0, min_value: float = 0.0) -> float:
    try:
        val = float(str(s).replace(",", ""))
    except Exception:
        val = float(default)
    return max(val, min_value)
def parse_us_any(s: str, default: float = 0.0) -> float:
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return float(default)
def float_text_input(label: str, default_val: float, key: str, min_value: float = 0.0, label_visibility: str = "visible") -> float:
    if key not in st.session_state:
        st.session_state[key] = us2(default_val)
    def _normalize():
        val = parse_us(st.session_state[key], default=default_val, min_value=min_value)
        st.session_state[key] = us2(val)
    st.text_input(label, value=st.session_state[key], key=key, on_change=_normalize, label_visibility=label_visibility)
    return parse_us(st.session_state[key], default=default_val, min_value=min_value)
def float_text_input_signed(label: str, default_val: float, key: str) -> float:
    if key not in st.session_state:
        st.session_state[key] = us2(default_val)
    def _normalize():
        val = parse_us_any(st.session_state[key], default=default_val)
        st.session_state[key] = us2(val)
    st.text_input(label, value=st.session_state[key], key=key, on_change=_normalize, label_visibility="visible")
    return parse_us_any(st.session_state[key], default=default_val)
# --- Proprietary trial notices (access-only) ---------------------------------
def show_trial_header(owner_name: str, contact_email: str, version: str, date_str: str) -> None:
    
    with st.expander("About, Terms & Privacy"):
        st.markdown(f"""
**About.** FuelEU Maritime calculator & optimizer (public trial).  
**Status.** Non-production demo for evaluation only (temporary credentials).  
**Ownership.** © {date_str.split('-')[0]} {owner_name}. All rights reserved. Access only — code not distributed.  
**No warranty.** Provided “as is”; results may contain errors.  
**No advice.** Not legal, regulatory, or financial advice.  
**Privacy.** No personal data is stored; minimal anonymous usage logs may be kept for reliability.  
**Contact.** {contact_email}  
**Third-party.** Built with Streamlit and open-source libraries. Trademarks belong to their owners.
""")

def show_trial_footer(owner_name: str, version: str, date_str: str) -> None:
    st.caption(f"© {date_str.split('-')[0]} {owner_name}. All rights reserved. v{version} ({date_str})")
# -----------------------------------------------------------------------------

# ──────────────────────────────────────────────────────────────────────────────
# Allocators
# ──────────────────────────────────────────────────────────────────────────────
def scoped_energies_extra_eu(energies_fuel_voyage: Dict[str, float],
                             energies_fuel_berth: Dict[str, float],
                             elec_MJ: float,
                             wtw: Dict[str, float]) -> Dict[str, float]:
    """
    (Kept for optimizer evaluation)  pooled allocator with berth-100% guarantee:
      • Pool = 100% berth + 50% of total voyage (fuels only). ELEC always 100%.
      • Fill by WtW priority: renewables first (berth→voy up to spare after reserving berth fossils),
        then 100% fossil berth, then 50% fossil voyage.
    """
    def g(d, k): return float(d.get(k, 0.0))
    fossils = ["HSFO", "LFO", "MGO"]
    foss_sorted = sorted(fossils, key=lambda f: wtw.get(f, float("inf")))
    total_voy = sum(energies_fuel_voyage.values())
    half_voy  = 0.5 * total_voy
    berth_fossil_total = sum(g(energies_fuel_berth, f) for f in fossils)
    pool_total = sum(energies_fuel_berth.values()) + half_voy

    scoped = {k: 0.0 for k in ["HSFO","LFO","MGO","BIO","RFNBO","ELEC"]}
    scoped["ELEC"] = max(elec_MJ, 0.0)
    remaining = pool_total  # fuels only

    ren_sorted = sorted(["RFNBO","BIO"], key=lambda f: wtw.get(f, float("inf")))
    for f in ren_sorted:
        take_b = min(g(energies_fuel_berth, f), remaining)
        if take_b > 0: scoped[f] += take_b; remaining -= take_b
        if remaining <= 0: return scoped
        spare_for_voy_ren = max(0.0, remaining - berth_fossil_total)
        take_v = min(g(energies_fuel_voyage, f), spare_for_voy_ren)
        if take_v > 0: scoped[f] += take_v; remaining -= take_v
        if remaining <= 0: return scoped

    for f in foss_sorted:
        take = min(g(energies_fuel_berth, f), remaining)
        if take > 0: scoped[f] += take; remaining -= take
        if remaining <= 0: return scoped

    for f in foss_sorted:
        half_v = 0.5 * g(energies_fuel_voyage, f)
        if half_v <= 0 or remaining <= 0: continue
        take = min(half_v, remaining)
        scoped[f] += take; remaining -= take
        if remaining <= 0: return scoped

    return scoped

def prioritized_half_scope_all_fuels(energies_voy: Dict[str, float],
                                     wtw: Dict[str, float]) -> Dict[str, float]:
    """
    New per-segment allocator for cross-border segments when toggle is ON:
      • POOL = 50% of TOTAL segment energy (fuels only).
      • Fill the pool by ascending WtW across ALL fuels (RFNBO, BIO, HSFO, LFO, MGO),
        taking up to each fuel's available energy until pool is full.
      • No ELEC in voyage segments.
    """
    pool = 0.5 * sum(energies_voy.values())
    result = {k: 0.0 for k in energies_voy.keys()}
    order = sorted(energies_voy.keys(), key=lambda f: wtw.get(f, float("inf")))
    for f in order:
        if pool <= 0: break
        take = min(energies_voy.get(f, 0.0), pool)
        if take > 0:
            result[f] = take
            pool -= take
    return result

# ──────────────────────────────────────────────────────────────────────────────
# Segments
# ──────────────────────────────────────────────────────────────────────────────
SEG_TYPES = [
    "Intra-EU voyage",
    "EU→non-EU voyage",
    "non-EU→EU voyage",
    "EU at-berth (port stay)"
]

def _default_segment() -> Dict[str, Any]:
    return {
        "type": SEG_TYPES[0],
        "HSFO_t": 0.0, "LFO_t": 0.0, "MGO_t": 0.0, "BIO_t": 0.0, "RFNBO_t": 0.0,
        "OPS_kWh": 0.0,
        "prio_on": True  # default ON for cross-border; harmless otherwise
    }

def _ensure_segments_state():
    if "abs_segments" not in st.session_state:
        if "abs_segments" in DEFAULTS and isinstance(DEFAULTS["abs_segments"], list):
            st.session_state["abs_segments"] = DEFAULTS["abs_segments"]
        else:
            st.session_state["abs_segments"] = []

def _segments_totals_masses_and_ops() -> Tuple[Dict[str, Dict[str, float]], float]:
    res = {
        "intra_voy": {k: 0.0 for k in ["HSFO","LFO","MGO","BIO","RFNBO"]},
        "extra_voy": {k: 0.0 for k in ["HSFO","LFO","MGO","BIO","RFNBO"]},
        "eu_berth":  {k: 0.0 for k in ["HSFO","LFO","MGO","BIO","RFNBO"]},
    }
    ops_kwh_total = 0.0
    for seg in st.session_state.get("abs_segments", []):
        t = seg.get("type", SEG_TYPES[0])
        bucket = "intra_voy" if t == "Intra-EU voyage" else ("eu_berth" if t == "EU at-berth (port stay)" else "extra_voy")
        for f in ["HSFO","LFO","MGO","BIO","RFNBO"]:
            res[bucket][f] += float(seg.get(f + "_t", 0.0)) or 0.0
        if t == "EU at-berth (port stay)":
            ops_kwh_total += float(seg.get("OPS_kWh", 0.0)) or 0.0
    return res, ops_kwh_total

def _masses_to_energies(masses: Dict[str, float], LCVs: Dict[str, float]) -> Dict[str, float]:
    return {f: compute_energy_MJ(masses.get(f, 0.0), LCVs.get(f, 0.0)) for f in ["HSFO","LFO","MGO","BIO","RFNBO"]}

# ──────────────────────────────────────────────────────────────────────────────
# Gate (MUST run before any other UI beyond page_config)
# ──────────────────────────────────────────────────────────────────────────────
shared_creds_cookie_gate()

# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────
st.title("FuelEU & EU ETS Maritime — Voyage Segments — GHG Intensity & Cost  — Cost Optimizer: Pooling compared to Bunkering Optimization with Alternative Fuels (ex. BIO/RFNBO/other)")
#show_trial_header("Nikitas Eleftheriou", "ops@example.com", "1.1", "2025-12-12")
st.caption("2025–2050 • Limits from 2020 baseline 91.16 gCO₂e/MJ • WtW • Prices in EUR")

# ──────────────────────────────────────────────────────────────────────────────
# Methodology & Units — aligned to full code scope (FuelEU + EU ETS + Optimizer)
# ──────────────────────────────────────────────────────────────────────────────
with st.expander("Methodology & Units", expanded=False):
    st.markdown("""
### Units
- **Fuel mass:** tonnes **[t]**  
- **Fuel energy:** **[MJ]** via **LCV [MJ/t]**  
- **Electricity (OPS):** **kWh → MJ** using **1 kWh = 3.6 MJ** (EU at-berth only)  
- **WtW intensity:** **[gCO₂e/MJ]**  
- **FuelEU balance:** **[tCO₂e]**  
- **EU ETS emissions:** **[tCO₂]** (Tank-to-Wake, fossil portion only)  
- **All costs:** **EUR**

---

### Energy conversion
""")
    st.latex(r"E_f = m_f \cdot \text{LCV}_f")
    st.latex(r"E_{\text{OPS}}[\text{MJ}] = E_{\text{OPS}}[\text{kWh}]\cdot 3.6")

    st.markdown("""
---

### Per-segment in-scope energy (FuelEU scope)
For each segment, the app computes **All energy** and **In-scope energy**:

- **Intra-EU voyage:** 100% of each fuel’s energy is in scope.  
- **EU at-berth (port stay):** 100% of each fuel’s energy is in scope **plus OPS electricity (100%)**.  
- **Cross-border (EU→non-EU, non-EU→EU):**
  - If **Apply prioritized allocation = OFF**: each fuel is simply **50% in scope**.
  - If **Apply prioritized allocation = ON**: a **pool equal to 50% of total segment fuel energy** is formed and filled by **ascending WtW across all fuels** (RFNBO, BIO, HSFO, LFO, MGO) until the pool is full.

> Note: OPS electricity does **not** appear in voyage segments. Electricity has WtW = 0 in the app.
""")

    st.markdown("""
---

### Combined in-scope mix and “global WtW re-arrangement”
After summing the **per-segment in-scope** energies into a combined in-scope stack:

- If **at least one cross-border segment has prioritized allocation ON**, the app applies a **global WtW-prioritized re-allocation** on the *combined in-scope fuels only*:
  - **Total in-scope energy (including OPS)** is kept unchanged.
  - **Electricity stays fixed**.
  - Fuel in-scope energy is reassigned across fuels by ascending WtW, **capped by 100% of each fuel’s total (all-segments) energy**.

This global re-arranged combined in-scope mix is what drives the **attained intensity and all FuelEU costs**.
""")

    st.markdown("**Attained WtW intensity (year \\(y\\))**:")
    st.latex(
        r"I_{\text{att}}(y)=\frac{\sum_{k\in\{HSFO,LFO,MGO,BIO,RFNBO,ELEC\}} I_k \, E^{\text{scope}}_{k}}"
        r"{E^{\text{scope}}_{\text{total}}+(r(y)-1)\,E^{\text{scope}}_{\text{RFNBO}}}"
    )
    st.markdown("**RFNBO reward factor**:")
    st.latex(r"r(y)=\begin{cases}2,& y\le 2033\\[4pt]1,& y\ge 2034\end{cases}")

    st.markdown("""
---

### FuelEU compliance balance (tCO₂e), carry-in, pooling, banking
First compute the raw compliance balance:
""")
    st.latex(
        r"CB_{\text{raw}}(y)=\frac{\big(I_{\text{limit}}(y)-I_{\text{att}}(y)\big)\cdot E^{\text{scope}}_{\text{total}}}{10^{6}}"
    )
    st.markdown("""
Then apply **carry-in** (banked from prior year), **pooling**, and **banking**:
""")
    st.latex(
        r"CB_{\text{eff}}(y)=CB_{\text{raw}}(y)+\text{CarryIn}(y)"
    )
    st.latex(
        r"CB_{\text{final}}(y)=CB_{\text{eff}}(y)+\text{PoolingApplied}(y)-\text{BankedToNext}(y)"
    )

    st.markdown("""
**Pooling sign convention (as implemented):**
- Pooling input **≥ 0** means **uptake** (can only cover a deficit; capped by current deficit).
- Pooling input **< 0** means **provide** (can only be provided from a surplus; capped by current surplus).

**Banking (as implemented):**
- Banking is capped by the current surplus and becomes **next year’s carry-in**.
- A safety clamp prevents ending “more negative” just because banking/providing was over-applied.
""")

    st.markdown("""
---

### FuelEU penalty multiplier (constant within each step if deficit)
If the year ends with a **deficit** (negative final balance), a step-constant multiplier is applied:
""")
    st.latex(r"M_{\text{step}}=1+0.10\cdot(\text{Seed}-1)")
    st.markdown("""
Where **Seed = “Consecutive deficit years (seed)”** from the sidebar and the multiplier is held constant per regulatory step period in the results loop.
""")

    st.markdown("""
---

### Converting tCO₂e ↔ €/VLSFO-eq t (used by penalty)
The app converts a tCO₂e deficit to an equivalent **VLSFO-eq tonnes** using the attained intensity and an energy reference of **41,000 MJ/t**:
""")
    st.latex(r"\text{tCO₂e per VLSFO-eq t}=\frac{I_{\text{att}}\cdot 41{,}000}{10^{6}}")
    st.markdown("""
Then:
- **Penalty (EUR)** uses the user input **€/VLSFO-eq t** times the computed VLSFO-eq tonnes, times the step multiplier (if in deficit).
- **Credits (EUR)** use **€/tCO₂e** times positive balance (if in surplus).
""")

    st.markdown("""
---

### FuelEU cost model (EUR) used in the results table
""")
    st.latex(
        r"\text{Net\_Total\_Cost}(y)=\text{Penalty}(y)-\text{Credit}(y)+\text{BIO\_Premium}(y)+\text{Pooling\_Cost}(y)"
    )
    st.markdown("""
Where:
- **BIO_Premium(y)** = (total BIO tonnes in all segments) × (Premium BIO vs selected fossil)  
- **Pooling_Cost(y)** = PoolingApplied(y) × Pooling price [€/tCO₂e]
""")

    st.markdown("""
---

### EU ETS (shipping) — scope, BIO blend fossil share, emissions, cost
EU ETS in-scope masses are computed from **segment masses**:
- **Intra-EU:** 100%  
- **EU at-berth:** 100%  
- **Cross-border EU↔non-EU:** 50%

BIO is treated as a **delivered blend**:
- Pure BIO fraction = **Pure BIO in the blend mix (%)** → **0 ETS**
- Fossil share = remaining % → assigned to the fossil of **Bio Mix Type** (BIO/HSFO, BIO/LFO, BIO/MGO)

Tank-to-wake emission factors used (tCO₂ per t fuel):
- HSFO: 3.114  
- LFO:  3.151  
- MGO:  3.206

Coverage factor:
- **2025:** 70%  
- **2026+:** 100%

EU ETS cost:
""")
    st.latex(r"\text{ETS\_Cost}=\text{ETS\_Emissions}[tCO_2]\cdot \text{EUA\_Price}\,[€/tCO_2]")

    st.markdown("""
---

### Optimizer objective (per year): minimize **FuelEU + EU ETS**
For each year, the optimizer searches a fossil-to-BIO shift **x [t]**:
- Reduce the selected fossil (HSFO/LFO/MGO) segment-by-segment (priority: Intra-EU → EU berth → cross-border).
- Add BIO **in the same segments** energy-equivalently:
""")
    st.latex(r"\Delta m_{\text{BIO}}=x\cdot\frac{\text{LCV}_{\text{fossil}}}{\text{LCV}_{\text{BIO}}}")
    st.markdown("""
Objective evaluated for each candidate x:
- FuelEU: penalty − credits + pooling cost + BIO premium
- **Plus EU ETS cost recomputed on the candidate segments mix** (including BIO fossil share per blend settings)

The optimizer uses a **coarse grid** scan then a **golden-section refinement**.
""")

    st.markdown("""
---

### Interactive simulation: “BIO optimization vs Pooling only”
For a selected year and a BIO premium range:
- **BIO optimization curve:** runs the same “minimize FuelEU+ETS” optimization for each premium, with **pooling cost forced to 0** inside that simulation’s BIO route.
- **Pooling-only curve:** uses the **base-case** Final_Balance_tCO₂e (from first parameters) and assumes it is fully compensated by pooling at the user’s comparison price:
  - Pooling cost component = − Final_Balance × PoolingPriceCompare
  - Total = base ETS cost + (base BIO tonnes × premium) + pooling cost component

(So the pooling-only curve is a policy comparison construct, not the full FuelEU penalty/credit mechanics.)
""")

# Sidebar CSS (compact), top metric smaller value text
st.markdown("""
<style>
/* Sidebar container & general spacing */
section[data-testid="stSidebar"] div.block-container{
    padding-top:.6rem;
    padding-bottom:.6rem;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{
    gap:.8rem;
}
section[data-testid="stSidebar"] label{
    font-size:.95rem;
    margin-bottom:.2rem;
    font-weight:600;
}
section[data-testid="stSidebar"] input[type="text"],
section[data-testid="stSidebar"] input[type="number"]{
    height:2.0rem;
    min-height:2.0rem;
    padding:.32rem .55rem;
}

/* Cards ("bubbles") in the sidebar */
section[data-testid="stSidebar"] .card{
    padding:.65rem .75rem;
    border:1px solid #e5e7eb;
    border-radius:.6rem;
    background:#fbfbfb;
}
section[data-testid="stSidebar"] .card h4{
    margin:.15rem 0 1.10rem 0;
    font-size:1.0rem;
    font-weight:800;
}
section[data-testid="stSidebar"] .card .help{
    font-size:.86rem;
    color:#6b7280;
    margin-top:.20rem;
    margin-bottom:1.00rem;
}

/* Tables & metrics */
hr{
    border:none;
    border-top:1px solid #e5e7eb;
    margin:.4rem 0;
}
[data-testid="stMetricLabel"]{
    font-size:.95rem !important;
    font-weight:800 !important;
}
[data-testid="stMetricValue"]{
    font-size:.80rem !important;
    font-weight:700 !important;
    line-height:1.05 !important;
}
[data-testid="stDataFrame"] div[role="columnheader"],
[data-testid="stDataFrame"] div[role="gridcell"]{
    padding:2px 6px !important;
}
[data-testid="stDataFrame"]{
    font-size:0.85rem !important;
}

/* ───────── ETS-specific green highlight ───────── */
section[data-testid="stSidebar"] .ets-section{
    border:1px solid #bbf7d0;
    background:#f0fdf4;
    border-radius:.6rem;
    padding:.45rem .55rem;
    margin-top:.35rem;
}
section[data-testid="stSidebar"] .ets-section label{
    color:#15803d;
}
section[data-testid="stSidebar"] .ets-section input,
section[data-testid="stSidebar"] .ets-section select{
    border-color:#86efac;
    background:#ecfdf5;
}
</style>
""", unsafe_allow_html=True)


with st.sidebar:
    _ensure_segments_state()

    # 1) Segments builder
    st.markdown('<div class="card"><h4>Voyage segments</h4><div class="help">Add voyages and EU at-berth stays one by one. OPS appears only inside EU at-berth. Cross-border segments have a toggle for prioritized allocation (applies to all fuels by ascending WtW).</div>', unsafe_allow_html=True)
    col_add, col_clear = st.columns([1,1])
    with col_add:
        if st.button("➕ Add segment"):
            st.session_state["abs_segments"].append(_default_segment())
    with col_clear:
        if st.button("🗑️ Clear all"):
            st.session_state["abs_segments"] = []
    # Render segments (compact)
    to_remove: List[int] = []
    for i, seg in enumerate(st.session_state["abs_segments"]):
        with st.expander(f"Segment {i+1}", expanded=True):
            seg["type"] = st.selectbox("Type", SEG_TYPES, index=SEG_TYPES.index(seg.get("type", SEG_TYPES[0])), key=f"seg_type_{i}")
            # Toggle appears only for cross-border voyages
            if seg["type"] in ("EU→non-EU voyage", "non-EU→EU voyage"):
                seg["prio_on"] = st.checkbox("Apply prioritized allocation", value=bool(seg.get("prio_on", True)), key=f"seg_prio_{i}")
            cA, cB = st.columns(2)
            with cA:
                seg["HSFO_t"]  = float_text_input("HSFO [t]" , seg.get("HSFO_t", 0.0), key=f"seg_hsfo_{i}",  min_value=0.0)
                seg["MGO_t"]   = float_text_input("MGO [t]"  , seg.get("MGO_t",  0.0), key=f"seg_mgo_{i}",   min_value=0.0)
                seg["RFNBO_t"] = float_text_input("RFNBO [t]", seg.get("RFNBO_t",0.0), key=f"seg_rfn_{i}",   min_value=0.0)
            with cB:
                seg["LFO_t"]   = float_text_input("LFO [t]"  , seg.get("LFO_t",  0.0), key=f"seg_lfo_{i}",   min_value=0.0)
                seg["BIO_t"]   = float_text_input("BIO [t]"  , seg.get("BIO_t",  0.0), key=f"seg_bio_{i}",   min_value=0.0)
            # OPS appears only for EU at-berth
            if seg["type"] == "EU at-berth (port stay)":
                seg["OPS_kWh"] = float_text_input(
                    "EU OPS electricity (kWh)",
                    seg.get("OPS_kWh", 0.0),
                    key=f"seg_ops_{i}",
                    min_value=0.0
                )
                st.text_input(
                    "Electricity (MJ) (derived)",
                    value=us2(seg["OPS_kWh"] * 3.6),
                    disabled=True,
                    key=f"seg_ops_mj_{i}",
                )

            if st.button("Remove this segment", key=f"seg_remove_{i}"):
                to_remove.append(i)
    if to_remove:
        st.session_state["abs_segments"] = [s for j, s in enumerate(st.session_state["abs_segments"]) if j not in to_remove]
    st.markdown("</div>", unsafe_allow_html=True)

    # 2) Fuel properties
    st.markdown('<div class="card"><h4>Fuel properties</h4>', unsafe_allow_html=True)

    # — LCVs first (MJ/t) —
    st.markdown("**Lower Heating Values (LCV)** [MJ/t]")
    lcv_c1, lcv_c2, lcv_c3 = st.columns(3)
    with lcv_c1:
        LCV_HSFO  = float_text_input("HSFO LCV [MJ/t]" , _get(DEFAULTS, "LCV_HSFO" , 40_200.0), key="LCV_HSFO",  min_value=0.0)
    with lcv_c2:
        LCV_LFO   = float_text_input("LFO LCV [MJ/t]"  , _get(DEFAULTS, "LCV_LFO"  , 41_200.0), key="LCV_LFO",   min_value=0.0)
    with lcv_c3:
        LCV_MGO   = float_text_input("MGO LCV [MJ/t]"  , _get(DEFAULTS, "LCV_MGO"  , 42_700.0), key="LCV_MGO",   min_value=0.0)
    lcv_c4, lcv_c5 = st.columns(2)
    with lcv_c4:
        LCV_BIO   = float_text_input("BIO LCV [MJ/t]"  , _get(DEFAULTS, "LCV_BIO"  , 39_800.0), key="LCV_BIO",   min_value=0.0)
    with lcv_c5:
       LCV_RFNBO = float_text_input("RFNBO LCV [MJ/t]", _get(DEFAULTS, "LCV_RFNBO", 30_000.0), key="LCV_RFNBO", min_value=0.0)

    st.markdown("<hr style='margin:0.35rem 0;'/>", unsafe_allow_html=True)

    # — WtW after (gCO₂e/MJ) —
    st.markdown("**Well-to-Wake (WtW) intensities** [gCO₂e/MJ]")
    wtw_c1, wtw_c2, wtw_c3 = st.columns(3)
    with wtw_c1:
        WtW_HSFO  = float_text_input("HSFO WtW" , _get(DEFAULTS, "WtW_HSFO" , 91.74),  key="WtW_HSFO",  min_value=0.0)
    with wtw_c2:
        WtW_LFO   = float_text_input("LFO WtW"  , _get(DEFAULTS, "WtW_LFO"  , 91.39),  key="WtW_LFO",   min_value=0.0)
    with wtw_c3:
       WtW_MGO   = float_text_input("MGO WtW"  , _get(DEFAULTS, "WtW_MGO"  , 90.77),  key="WtW_MGO",   min_value=0.0)
    wtw_c4, wtw_c5 = st.columns(2)
    with wtw_c4:
       WtW_BIO   = float_text_input("BIO WtW"  , _get(DEFAULTS, "WtW_BIO"  , 70.37),  key="WtW_BIO",   min_value=0.0)
    with wtw_c5:
       WtW_RFNBO = float_text_input("RFNBO WtW", _get(DEFAULTS, "WtW_RFNBO", 20.00),  key="WtW_RFNBO", min_value=0.0)
    st.markdown("</div>", unsafe_allow_html=True)

    # 4) Other settings
    st.markdown('<div class="card"><h4>Other settings</h4>', unsafe_allow_html=True)
    consecutive_deficit_years_seed = int(st.number_input(
        "Consecutive deficit years (seed)",
        min_value=1,
        value=int(_get(DEFAULTS, "consecutive_deficit_years", 1)),
        step=1,
        key="consecutive_deficit_years"
    ))
    opt_fuels = ["HSFO", "LFO", "MGO"]
    try:
        _idx = opt_fuels.index(_get(DEFAULTS, "opt_reduce_fuel", "HSFO"))
    except ValueError:
        _idx = 0
    selected_fuel_for_opt = st.selectbox("Fuel to reduce (for optimization)", opt_fuels, index=_idx)

    # ── ETS: BIO blend settings (green highlighted block) ──
    st.markdown('<div class="ets-section">', unsafe_allow_html=True)

    pure_bio_pct = st.number_input(
        "Pure BIO in the blend mix (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(_get(DEFAULTS, "pure_bio_pct", 100.0)),
        step=1.0,
        key="pure_bio_pct",
        help="Percentage of pure sustainable BIO in the delivered fuel blend. "
             "Example: B30 → enter 30; B60 → enter 60. "
             "Used for ETS calculation of fossil share in the blend."
    )

    bio_mix_type = st.selectbox(
        "Bio Mix Type",
        options=["BIO/HSFO mix", "BIO/LFO mix", "BIO/MGO mix"],
        index=1,
        key="bio_mix_type",
        help="Select which fossil fuel is blended with BIO. "
             "Example: if fuel is B30-LFO, choose 'BIO/LFO mix'. "
             "Determines fossil CO₂ emissions under EU ETS."
    )

    # Warn if Fuel to reduce and Bio Mix Type are inconsistent (for blends < 100% BIO)
    if pure_bio_pct < 100.0:
        if selected_fuel_for_opt == "HSFO":
            expected_mix = "BIO/HSFO mix"
        elif selected_fuel_for_opt == "LFO":
            expected_mix = "BIO/LFO mix"
        else:  # selected_fuel_for_opt == "MGO"
            expected_mix = "BIO/MGO mix"

        if bio_mix_type != expected_mix:
            st.warning(
                f"For Fuel to reduce = {selected_fuel_for_opt}, "
                f"the ETS-consistent Bio Mix Type is '{expected_mix}'. "
                f"Currently selected: '{bio_mix_type}'.",
                icon="⚠️",
            )

    st.markdown('</div>', unsafe_allow_html=True)



    # 3) Market prices  (ALL in EUR)
    st.markdown('<div class="card"><h4>Market prices</h4>', unsafe_allow_html=True)
    credit_per_tco2e = float_text_input(
        "Credit price €/tCO₂e",
        _get(DEFAULTS, "credit_per_tco2e", 200.0),
        key="credit_per_tco2e_str", min_value=0.0
    )
    penalty_price_eur_per_vlsfo_t = float_text_input(
        "Penalty price €/VLSFO-eq t",
        _get(DEFAULTS, "penalty_price_eur_per_vlsfo_t", 2_400.0),
        key="penalty_per_vlsfo_t_str", min_value=0.0
    )
    bio_premium_label = f"Premium BIO vs {selected_fuel_for_opt} [EUR/ton]"
    bio_premium_eur_per_t = float_text_input(
        bio_premium_label,
        _get(DEFAULTS, "bio_premium_eur_per_t", _get(DEFAULTS, "bio_premium_usd_per_t", 300.0)),
        key="bio_premium_eur_per_t", min_value=0.0
    )

    # EU ETS — EUAs price per year
    eua_col_year, eua_col_price = st.columns([1, 1])
    with eua_col_year:
        eua_year_selection = st.selectbox(
            "EUAs year",
            options=["2025", "2026+"],
            key="eua_year_selection"
        )
    with eua_col_price:
        eua_price_eur_per_tco2 = float_text_input(
            "EUAs EUR price for year",
            _get(DEFAULTS, "eua_price_eur_per_tco2", 87.0),
            key="eua_price_eur_per_tco2",
            min_value=0.0
        )

    st.markdown("</div>", unsafe_allow_html=True)


    # 5) Banking & Pooling
    st.markdown('<div class="card"><h4>Banking & Pooling (tCO₂e)</h4>', unsafe_allow_html=True)
    pooling_price_eur_per_tco2e = float_text_input("Pooling price €/tCO₂e", _get(DEFAULTS, "pooling_price_eur_per_tco2e", 200.0), key="pooling_price_eur_per_tco2e", min_value=0.0)
    pooling_tco2e_input = float_text_input_signed("Pooling [tCO₂e]: + uptake, − provide", _get(DEFAULTS, "pooling_tco2e", 0.0), key="POOL_T")
    pooling_start_year = st.selectbox("Pooling starts from year", YEARS, index=YEARS.index(int(_get(DEFAULTS, "pooling_start_year", YEARS[0]))))
    banking_tco2e_input = float_text_input("Banking to next year [tCO₂e]", _get(DEFAULTS, "banking_tco2e", 0.0), key="BANK_T", min_value=0.0)
    banking_start_year = st.selectbox("Banking starts from year", YEARS, index=YEARS.index(int(_get(DEFAULTS, "banking_start_year", YEARS[0]))))
    st.markdown("</div>", unsafe_allow_html=True)

    # 6) Save
    if st.button("💾 Save current inputs as defaults"):
        defaults_to_save = {
            # Prices/settings (EUR-only)
            "credit_per_tco2e": credit_per_tco2e,
            "penalty_price_eur_per_vlsfo_t": penalty_price_eur_per_vlsfo_t,
            "bio_premium_eur_per_t": bio_premium_eur_per_t,
            "pooling_price_eur_per_tco2e": pooling_price_eur_per_tco2e,
            "banking_tco2e": banking_tco2e_input,
            "pooling_tco2e": pooling_tco2e_input,
            "pooling_start_year": int(pooling_start_year),
            "banking_start_year": int(banking_start_year),
            "consecutive_deficit_years": consecutive_deficit_years_seed,
            "opt_reduce_fuel": selected_fuel_for_opt,
            # Fuel props
            "LCV_HSFO": LCV_HSFO, "LCV_LFO": LCV_LFO, "LCV_MGO": LCV_MGO, "LCV_BIO": LCV_BIO, "LCV_RFNBO": LCV_RFNBO,
            "WtW_HSFO": WtW_HSFO, "WtW_LFO": WtW_LFO, "WtW_MGO": WtW_MGO, "WtW_BIO": WtW_BIO, "WtW_RFNBO": WtW_RFNBO,
            # Segments
            "abs_segments": st.session_state.get("abs_segments", []),
        }
        try:
            with open(DEFAULTS_PATH, "w", encoding="utf-8") as f:
                json.dump(defaults_to_save, f, indent=2)

            # keep DEFAULTS in sync for this run (safe)
            DEFAULTS["consecutive_deficit_years"] = int(consecutive_deficit_years_seed)

            st.success("Defaults saved.")
        except Exception as e:
            st.error(f"Could not save defaults: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Live LCV/WtW dicts (used throughout)
# ──────────────────────────────────────────────────────────────────────────────
LCV_HSFO = parse_us_any(st.session_state.get("LCV_HSFO", _get(DEFAULTS,"LCV_HSFO",40200.0)), 40200.0) 
LCV_LFO  = parse_us_any(st.session_state.get("LCV_LFO" , _get(DEFAULTS,"LCV_LFO" ,41200.0)), 41200.0)
LCV_MGO  = parse_us_any(st.session_state.get("LCV_MGO" , _get(DEFAULTS,"LCV_MGO" ,42700.0)), 42700.0)
LCV_BIO  = parse_us_any(st.session_state.get("LCV_BIO" , _get(DEFAULTS,"LCV_BIO" ,39800.0)), 39800.0)
LCV_RFNBO= parse_us_any(st.session_state.get("LCV_RFNBO", _get(DEFAULTS,"LCV_RFNBO",30000.0)),30000.0)
WtW_HSFO = parse_us_any(st.session_state.get("WtW_HSFO", _get(DEFAULTS,"WtW_HSFO",91.74)), 91.74)
WtW_LFO  = parse_us_any(st.session_state.get("WtW_LFO" , _get(DEFAULTS,"WtW_LFO" ,91.39)), 91.39)
WtW_MGO  = parse_us_any(st.session_state.get("WtW_MGO" , _get(DEFAULTS,"WtW_MGO" ,90.77)), 90.77)
WtW_BIO  = parse_us_any(st.session_state.get("WtW_BIO" , _get(DEFAULTS,"WtW_BIO" ,70.37)), 70.37)
WtW_RFNBO= parse_us_any(st.session_state.get("WtW_RFNBO", _get(DEFAULTS,"WtW_RFNBO",20.00)), 20.00)
wtw = {"HSFO": WtW_HSFO, "LFO": WtW_LFO, "MGO": WtW_MGO, "BIO": WtW_BIO, "RFNBO": WtW_RFNBO, "ELEC": 0.0}
LCVs_now = {"HSFO": LCV_HSFO, "LFO": LCV_LFO, "MGO": LCV_MGO, "BIO": LCV_BIO, "RFNBO": LCV_RFNBO}

# ──────────────────────────────────────────────────────────────────────────────
# Bucket totals (kept for optimizer & CSV endpoints)
# ──────────────────────────────────────────────────────────────────────────────
totals_mass, ops_kwh_total = _segments_totals_masses_and_ops()
ELEC_MJ_input = ops_kwh_total * 3.6

# Energies by buckets (for optimizer use; kept)
energies_extra_voy = _masses_to_energies(totals_mass["extra_voy"], LCVs_now)
energies_eu_berth  = _masses_to_energies(totals_mass["eu_berth"],  LCVs_now)
energies_intra_voy = _masses_to_energies(totals_mass["intra_voy"], LCVs_now)

# ──────────────────────────────────────────────────────────────────────────────
# Per-segment rendering + build combined sums (ALL and IN-SCOPE from segments)
# ──────────────────────────────────────────────────────────────────────────────
COLORS = {  # original palette
    "ELEC":  "#FACC15",
    "RFNBO": "#86EFAC",
    "BIO":   "#065F46",
    "MGO":   "#93C5FD",
    "LFO":   "#2563EB",
    "HSFO":  "#1E3A8A",
}
FUELS = ["RFNBO","BIO","HSFO","LFO","MGO"]

def _segment_energy_mj(seg: Dict[str, Any]) -> Dict[str,float]:
    return {
        "HSFO": compute_energy_MJ(seg.get("HSFO_t",0.0), LCV_HSFO),
        "LFO":  compute_energy_MJ(seg.get("LFO_t", 0.0), LCV_LFO),
        "MGO":  compute_energy_MJ(seg.get("MGO_t", 0.0), LCV_MGO),
        "BIO":  compute_energy_MJ(seg.get("BIO_t", 0.0), LCV_BIO),
        "RFNBO":compute_energy_MJ(seg.get("RFNBO_t",0.0), LCV_RFNBO),
    }

def _segment_scope_with_toggle(seg: Dict[str,Any], energies_all: Dict[str,float]) -> Tuple[Dict[str,float], float]:
    """
    Returns (in_scope_fuel_MJ_dict, elec_MJ_segment).
    • Intra-EU: 100%.
    • EU-berth: 100% + ELEC (kWh→MJ).
    • Cross-border: toggle OFF → 50% each fuel; toggle ON → prioritized_half_scope_all_fuels().
    """
    t = seg.get("type", SEG_TYPES[0])
    if t == "Intra-EU voyage":
        return dict(energies_all), 0.0
    if t == "EU at-berth (port stay)":
        return dict(energies_all), float(seg.get("OPS_kWh",0.0))*3.6
    # Cross-border
    prio_on = bool(seg.get("prio_on", True))
    if prio_on:
        scoped = prioritized_half_scope_all_fuels(energies_all, wtw)
        return scoped, 0.0
    else:
        return {k: 0.5*energies_all[k] for k in energies_all.keys()}, 0.0

def _has_prioritized_segments(segments: List[Dict[str, Any]]) -> bool:
    """
    True if the UI has at least one cross-border segment
    with 'Apply prioritized allocation' ticked.
    """
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
    Final global WtW-prioritized reallocation for the combined in-scope mix.

    • Keeps total in-scope energy (including ELEC) unchanged.
    • For fuels only, reassigns in-scope energy across fuels by ascending WtW,
      using up to 100% of each fuel's total segment energy (combined_all[f]).
    • Per-segment in-scope stacks remain unchanged; only the combined stack
      and attained intensity/costs use this rearranged mix.
    """
    scope_total = sum(combined_scope.values())
    if scope_total <= 0.0:
        return dict(combined_scope)

    elec_scope = float(combined_scope.get("ELEC", 0.0) or 0.0)
    fuel_budget = max(scope_total - elec_scope, 0.0)

    # Start result with electricity fixed
    result = {k: 0.0 for k in combined_scope.keys()}
    result["ELEC"] = elec_scope
    if fuel_budget <= 0.0:
        return result

    # Fuels sorted by ascending WtW
    fuels_sorted = sorted(FUELS, key=lambda f: wtw_dict.get(f, float("inf")))
    remaining = fuel_budget

    for f in fuels_sorted:
        if remaining <= 0.0:
            break
        avail = float(combined_all.get(f, 0.0) or 0.0)  # 100% of segment energy for fuel f
        if avail <= 0.0:
            continue
        take = min(avail, remaining)
        result[f] = take
        remaining -= take

    # Numerical safety: if tiny residue remains, add it to the last fuel that got some allocation
    if remaining > 1e-6:
        for f in reversed(fuels_sorted):
            if result.get(f, 0.0) > 0.0:
                result[f] += remaining
                break

    return result




def _stack_with_arrows(title: str, left_vals: Dict[str,float], right_vals: Dict[str,float], show_elec: bool):
    categories = ["All", "In-scope"]
    fuels_sorted = sorted(FUELS, key=lambda f: wtw.get(f, float("inf")))
    stack_layers = ([("ELEC","ELEC (OPS)")] if show_elec else []) + [(f, f) for f in fuels_sorted]

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
    total_all = sum(left_vals.get(k,0.0) for k,_ in stack_layers)
    total_scope = sum(right_vals.get(k,0.0) for k,_ in stack_layers)
    fig.add_annotation(x=categories[0], y=total_all,  text=f"{us2(total_all)} MJ", showarrow=False, yshift=10, font=dict(size=12))
    fig.add_annotation(x=categories[1], y=total_scope, text=f"{us2(total_scope)} MJ", showarrow=False, yshift=10, font=dict(size=12))

    # arrows + % retained
    cum_left = 0.0
    cum_right = 0.0
    for key, label in stack_layers:
        layer_left = float(left_vals.get(key, 0.0))
        layer_right = float(right_vals.get(key, 0.0))
        if layer_left <= 0.0 and layer_right <= 0.0:
            cum_left += layer_left; cum_right += layer_right
            continue
        y_center_left = cum_left + (layer_left / 2.0)
        y_center_right = cum_right + (layer_right / 2.0)
        fig.add_trace(go.Scatter(x=categories, y=[y_center_left, y_center_right], mode="lines",
                                 line=dict(dash="dot", width=2), hoverinfo="skip", showlegend=False))
        fig.add_annotation(x=categories[1], y=y_center_right, ax=categories[0], ay=y_center_left,
                           xref="x", yref="y", axref="x", ayref="y", text="", showarrow=True,
                           arrowhead=3, arrowsize=1.2, arrowwidth=2, arrowcolor="rgba(0,0,0,0.65)")
        pct = (layer_right / layer_left * 100.0) if layer_left > 0 else 100.0
        pct = max(min(pct, 100.0), 0.0)
        y_mid = 0.5 * (y_center_left + y_center_right)
        fig.add_annotation(xref="paper", yref="y", x=0.5, y=y_mid, text=f"{pct:.0f}%", showarrow=False,
                           font=dict(size=11, color="#374151"),
                           bgcolor="rgba(255,255,255,0.65)", bordercolor="rgba(0,0,0,0)", borderpad=1)
        cum_left += layer_left; cum_right += layer_right

    fig.update_layout(
        title=dict(text=title, x=0.02, y=0.95, font=dict(size=13)),
        barmode="stack", xaxis_title="", yaxis_title="Energy [MJ]", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
        margin=dict(l=40, r=20, t=50, b=20), bargap=0.35, height=260,
    )
    st.plotly_chart(fig, use_container_width=True)

# Build combined sums while rendering per-segment stacks
combined_all = {"ELEC":0.0, "RFNBO":0.0, "BIO":0.0, "HSFO":0.0, "LFO":0.0, "MGO":0.0}
combined_scope = {"ELEC":0.0, "RFNBO":0.0, "BIO":0.0, "HSFO":0.0, "LFO":0.0, "MGO":0.0}

st.markdown("### Per-segment energy (All vs In-scope)")
if not st.session_state["abs_segments"]:
    st.info("No segments yet. Add segments from the left sidebar.")
else:
    for i, seg in enumerate(st.session_state["abs_segments"]):
        energies_all = _segment_energy_mj(seg)
        energies_scope, elec_mj_seg = _segment_scope_with_toggle(seg, energies_all)
        left_vals = dict(energies_all)
        right_vals = dict(energies_scope)
        if seg["type"] == "EU at-berth (port stay)":
            left_vals["ELEC"] = elec_mj_seg
            right_vals["ELEC"] = elec_mj_seg
            show_elec = True
        else:
            show_elec = False
        _stack_with_arrows(f"Segment {i+1}: {seg.get('type','')}", left_vals, right_vals, show_elec)

        # accumulate to combined sums
        combined_all["ELEC"]  += left_vals.get("ELEC", 0.0)
        combined_scope["ELEC"]+= right_vals.get("ELEC", 0.0)
        for f in FUELS:
            combined_all[f]   += left_vals.get(f, 0.0)
            combined_scope[f] += right_vals.get(f, 0.0)

# ──────────────────────────────────────────────────────────────────────────────
# Combined (from segment sums) → metrics, derived prices, stacks, intensity
# ──────────────────────────────────────────────────────────────────────────────
# ── Final global WtW-prioritized rearrangement (for combined in-scope only) ──
if _has_prioritized_segments(st.session_state.get("abs_segments", [])):
    combined_scope_final = _global_rearrange_scope(combined_all, combined_scope, wtw)
else:
    # No prioritized allocation in UI → keep original summed in-scope mix
    combined_scope_final = dict(combined_scope)

E_total_MJ = sum(combined_all.values())
E_scope_MJ = sum(combined_scope_final.values())

# Attained GHG of combined in-scope mix (after global rearrangement)
num_phys = sum(
    combined_scope_final.get(k, 0.0) * wtw.get(k, 0.0)
    for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO", "ELEC"]
)
den_phys = E_scope_MJ
E_rfnbo_scope = combined_scope_final.get("RFNBO", 0.0)

def attained_intensity_for_year(y: int) -> float:
    if den_phys <= 0: return 0.0
    r = 2.0 if y <= 2033 else 1.0
    den_rwd = den_phys + (r - 1.0) * E_rfnbo_scope
    return num_phys / den_rwd if den_rwd > 0 else 0.0

# Derived price factor (non-zero): use preview r=2
if den_phys > 0:
    den_preview = den_phys + E_rfnbo_scope
    g_preview = num_phys / den_preview if den_preview > 0 else 0.0
else:
    g_preview = 0.0
if g_preview <= 0:
    g_preview = BASELINE_2020_GFI
tco2e_per_vlsfo_t = (g_preview * 41_000.0) / 1_000_000.0

# Headline metrics (smaller numbers)
st.subheader("Energy breakdown (MJ)")
cA, cB, cC, cD, cE, cF, cG, cH = st.columns(8)
with cA: st.metric("Total energy (all)", f"{us2(E_total_MJ)} MJ")
with cB: st.metric("In-scope energy", f"{us2(E_scope_MJ)} MJ")
with cC: st.metric("Fossil — all", f"{us2(combined_all['HSFO'] + combined_all['LFO'] + combined_all['MGO'])} MJ")
with cD: st.metric("BIO — all", f"{us2(combined_all['BIO'])} MJ")
with cE: st.metric("RFNBO — all", f"{us2(combined_all['RFNBO'])} MJ")
with cF: st.metric(
    "Fossil — in scope",
    f"{us2(combined_scope_final['HSFO'] + combined_scope_final['LFO'] + combined_scope_final['MGO'])} MJ"
)
with cG: st.metric(
    "BIO — in scope",
    f"{us2(combined_scope_final['BIO'])} MJ"
)
with cH: st.metric(
    "RFNBO — in scope",
    f"{us2(combined_scope_final['RFNBO'])} MJ"
)

# Derived prices card (EUR)
with st.sidebar:
    st.markdown('<div class="card"><h4>Derived prices</h4>', unsafe_allow_html=True)
    st.text_input("Credit price €/VLSFO-eq t (at current mix)", value=us2(credit_per_tco2e * tco2e_per_vlsfo_t), disabled=True)
    st.text_input("Penalty price €/tCO₂e (at current mix)", value=us2((penalty_price_eur_per_vlsfo_t / tco2e_per_vlsfo_t) if tco2e_per_vlsfo_t>0 else 0.0), disabled=True)
    st.markdown("</div>", unsafe_allow_html=True)

# Combined stacks from segment sums
st.markdown("### Combined energy (All segments)")
categories = ["All energy", "In-scope energy"]
fuels_sorted_global = sorted(FUELS, key=lambda f: wtw.get(f, float("inf")))
stack_layers_global = [("ELEC", "ELEC (OPS)")] + [(f, f) for f in fuels_sorted_global]

left_vals = {
    "ELEC":  combined_all.get("ELEC", 0.0),
    "RFNBO": combined_all.get("RFNBO", 0.0),
    "BIO":   combined_all.get("BIO",   0.0),
    "HSFO":  combined_all.get("HSFO",  0.0),
    "LFO":   combined_all.get("LFO",   0.0),
    "MGO":   combined_all.get("MGO",   0.0),
}
right_vals = {
    "ELEC":  combined_scope_final.get("ELEC",  0.0),
    "RFNBO": combined_scope_final.get("RFNBO", 0.0),
    "BIO":   combined_scope_final.get("BIO",   0.0),
    "HSFO":  combined_scope_final.get("HSFO",  0.0),
    "LFO":   combined_scope_final.get("LFO",   0.0),
    "MGO":   combined_scope_final.get("MGO",   0.0),
}

fig_stacks = go.Figure()
for key, label in stack_layers_global:
    fig_stacks.add_trace(
        go.Bar(
            x=categories,
            y=[left_vals.get(key, 0.0), right_vals.get(key, 0.0)],
            name=label,
            marker_color=COLORS.get(key, None),
            hovertemplate=f"{label}<br>%{{x}}<br>%{{y:,.2f}} MJ<extra></extra>",
        )
    )
total_all = sum(left_vals.values())
total_scope = sum(right_vals.values())
fig_stacks.add_annotation(x=categories[0], y=total_all,  text=f"{us2(total_all)} MJ",  showarrow=False, yshift=10, font=dict(size=12))
fig_stacks.add_annotation(x=categories[1], y=total_scope, text=f"{us2(total_scope)} MJ", showarrow=False, yshift=10, font=dict(size=12))

cum_left = 0.0
cum_right = 0.0
for key, label in stack_layers_global:
    layer_left = float(left_vals.get(key, 0.0))
    layer_right = float(right_vals.get(key, 0.0))
    if layer_left <= 0.0 and layer_right <= 0.0:
        cum_left += layer_left; cum_right += layer_right
        continue
    y_center_left = cum_left + (layer_left / 2.0)
    y_center_right = cum_right + (layer_right / 2.0)
    fig_stacks.add_trace(go.Scatter(x=categories, y=[y_center_left, y_center_right], mode="lines",
                                    line=dict(dash="dot", width=2), hoverinfo="skip", showlegend=False))
    fig_stacks.add_annotation(x=categories[1], y=y_center_right, ax=categories[0], ay=y_center_left,
                          xref="x", yref="y", axref="x", ayref="y", text="", showarrow=True,
                          arrowhead=3, arrowsize=1.2, arrowwidth=2, arrowcolor="rgba(0,0,0,0.65)")
    pct = (layer_right / layer_left * 100.0) if layer_left > 0 else 100.0
    pct = max(min(pct, 100.0), 0.0)
    y_mid = 0.5 * (y_center_left + y_center_right)
    fig_stacks.add_annotation(xref="paper", yref="y", x=0.5, y=y_mid, text=f"{pct:.0f}%", showarrow=False,
                              font=dict(size=11, color="#374151"),
                              bgcolor="rgba(255,255,255,0.65)", bordercolor="rgba(0,0,0,0)", borderpad=1)
    cum_left += layer_left; cum_right += layer_right

fig_stacks.update_layout(
    barmode="stack", xaxis_title="", yaxis_title="Energy [MJ]", hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
    margin=dict(l=40, r=20, t=50, b=20), bargap=0.35,
)
st.plotly_chart(fig_stacks, use_container_width=True)
st.caption("Combined right bar = sum of per-segment in-scope energies (with cross-border prioritized allocation toggle as selected; OPS from EU-berth only).")

# ──────────────────────────────────────────────────────────────────────────────
# GHG Intensity vs. FuelEU Limit (uses combined in-scope)
# ──────────────────────────────────────────────────────────────────────────────
st.markdown('### GHG Intensity vs FuelEU Limit (2025–2050)')
years = LIMITS_DF["Year"].tolist()
limit_series = LIMITS_DF["Limit_gCO2e_per_MJ"].tolist()
actual_series = [attained_intensity_for_year(y) for y in years]
step_years = [2025, 2030, 2035, 2040, 2045, 2050]
limit_text = [f"{limit_series[i]:,.2f}" if years[i] in step_years else "" for i in range(len(years))]
attained_text = [f"{actual_series[i]:,.2f}" if years[i] in step_years else "" for i in range(len(years))]

fig = go.Figure()
fig.add_trace(go.Scatter(x=years, y=limit_series, name="FuelEU Limit (step)",
                         mode="lines+markers+text", line=dict(shape="hv", width=3),
                         text=limit_text, textposition="bottom center", textfont=dict(size=12),
                         hovertemplate="Year=%{x}<br>Limit=%{y:,.2f} gCO₂e/MJ<extra></extra>"))
fig.add_trace(go.Scatter(x=years, y=actual_series, name="Attained GHG (combined in-scope)",
                         mode="lines+text", line=dict(dash="dash", width=3),
                         text=attained_text, textposition="top center", textfont=dict(size=12),
                         hovertemplate="Year=%{x}<br>Attained=%{y:,.2f} gCO₂e/MJ<extra></extra>"))
fig.update_yaxes(tickformat=",.2f")
fig.update_layout(xaxis_title="Year", yaxis_title="GHG Intensity [gCO₂e/MJ]",
                  hovermode="x unified",
                  legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                  margin=dict(l=40, r=20, t=50, b=40))
st.plotly_chart(fig, use_container_width=True)

# ──────────────────────────────────────────────────────────────────────────────
# Results — Banking/Pooling + optimizer (kept), using combined in-scope intensity
# ──────────────────────────────────────────────────────────────────────────────
st.header("Results (merged per-year table)")

cb_raw_t, carry_in_list, cb_eff_t = [], [], []
pool_applied, bank_applied = [], []
final_balance_t, penalties_eur, credits_eur, g_att_list = [], [], [], []
carry = 0.0
fixed_multiplier_by_step = {}

for _, row in LIMITS_DF.iterrows():
    year = int(row["Year"])
    g_target = float(row["Limit_gCO2e_per_MJ"])
    g_att = attained_intensity_for_year(year)
    g_att_list.append(g_att)

    CB_g = (g_target - g_att) * E_scope_MJ
    CB_t_raw = CB_g / 1e6
    cb_raw_t.append(CB_t_raw)

    cb_eff = CB_t_raw + carry
    carry_in_list.append(carry)
    cb_eff_t.append(cb_eff)

    # Pooling (uptake only fills a deficit; provide only from surplus)
    if year >= int(st.session_state.get("pooling_start_year", _get(DEFAULTS,"pooling_start_year",YEARS[0]))):
       pooling_tco2e_val = parse_us_any(st.session_state.get("POOL_T", _get(DEFAULTS,"pooling_tco2e",0.0)), 0.0)
       if pooling_tco2e_val >= 0:
           # uptake: cap by current deficit (negative cb_eff)
           pre_deficit = max(-cb_eff, 0.0)
           pool_use = min(pooling_tco2e_val, pre_deficit)
       else:
           # provide: cap by current surplus (positive cb_eff)
           provide_abs = abs(pooling_tco2e_val)
           pre_surplus = max(cb_eff, 0.0)
           pool_use = -min(provide_abs, pre_surplus)
    else:
        pool_use = 0.0

    # Banking
    if year >= int(st.session_state.get("banking_start_year", _get(DEFAULTS,"banking_start_year",YEARS[0]))):
        requested_bank = max(parse_us_any(st.session_state.get("BANK_T", _get(DEFAULTS,"banking_tco2e",0.0)),0.0), 0.0)
        pre_surplus = max(cb_eff, 0.0)
        bank_use = min(requested_bank, pre_surplus)
    else:
        bank_use = 0.0

    # Safety clamp
    final_bal = cb_eff + pool_use - bank_use
    if final_bal < 0:
        needed = -final_bal
        trim_bank = min(needed, bank_use)
        bank_use -= trim_bank
        needed -= trim_bank
        if needed > 0 and pool_use < 0:
            pool_use += needed
            needed = 0.0
        final_bal = cb_eff + pool_use - bank_use

    carry = bank_use

    # Constant within step multiplier
    if final_bal < 0:
       step_idx = _step_of_year(year)
       if step_idx not in fixed_multiplier_by_step:
          seed = max(int(consecutive_deficit_years_seed), 1)  # ← use the live widget value
          fixed_multiplier_by_step[step_idx] = 1.0 + (seed - 1) * 0.10
       mult = fixed_multiplier_by_step[step_idx]
    else:
        mult = 1.0

    # EUR (no FX)
    penalty_vlsfo = parse_us_any(st.session_state.get("penalty_per_vlsfo_t_str", _get(DEFAULTS,"penalty_price_eur_per_vlsfo_t",2400.0)), 2400.0)
    credit_per_tco2e_val = parse_us_any(st.session_state.get("credit_per_tco2e_str", _get(DEFAULTS,"credit_per_tco2e",200.0)), 200.0)
    if final_bal > 0:
        credit_val = final_bal * credit_per_tco2e_val
        penalty_val = 0.0
    elif final_bal < 0:
        penalty_val = euros_from_tco2e(-final_bal, g_att, penalty_vlsfo) * mult
        credit_val = 0.0
    else:
        credit_val = penalty_val = 0.0

    pool_applied.append(pool_use); bank_applied.append(bank_use)
    final_balance_t.append(final_bal)
    penalties_eur.append(penalty_val); credits_eur.append(credit_val)

# BIO premium & pooling cost series (EUR)
bio_mass_total_t_base = (totals_mass["intra_voy"]["BIO"] + totals_mass["extra_voy"]["BIO"] + totals_mass["eu_berth"]["BIO"])
bio_premium_eur_per_t_val = parse_us_any(st.session_state.get("bio_premium_eur_per_t", _get(DEFAULTS,"bio_premium_eur_per_t", _get(DEFAULTS,"bio_premium_usd_per_t",300.0))), 0.0)
bio_premium_cost_eur_col = [bio_mass_total_t_base * bio_premium_eur_per_t_val] * len(YEARS)
pooling_price_eur_per_tco2e_val = parse_us_any(st.session_state.get("pooling_price_eur_per_tco2e", _get(DEFAULTS,"pooling_price_eur_per_tco2e",200.0)), 200.0)
pooling_cost_eur_col = [pool_applied[i] * pooling_price_eur_per_tco2e_val for i in range(len(YEARS))]
net_total_cost_eur_col = [penalties_eur[i] - credits_eur[i] + bio_premium_cost_eur_col[i] + pooling_cost_eur_col[i] for i in range(len(YEARS))]

# ──────────────────────────────────────────────────────────────────────────────
# Optimizer utilities (kept; pooled allocator approximation)
# ──────────────────────────────────────────────────────────────────────────────
def _segment_opt_priority(seg: Dict[str, Any]) -> int:
    """
    Priority for where to remove the selected fossil fuel first
    when performing a HSFO→BIO (or LFO/MGO→BIO) swap.

    Rationale (ETS exposure):
      0 → Intra-EU voyage      (100% ETS scope)
      1 → EU at-berth (port)   (100% ETS scope)
      2 → Cross-border voyages (non-EU→EU, EU→non-EU) → 50% ETS scope
      3 → Anything else (fallback)
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
    x_decrease_t: float
) -> Tuple[List[Dict[str, Any]], float, float]:
    """
    Reduce the selected fossil fuel (HSFO/LFO/MGO) by x tonnes and
    add BIO energy-equivalently in the *same segments* where the
    fossil is removed.

    This preserves:
      • Total energy per segment [MJ]
      • Overall voyage profile (no artificial energy transfer between segments)

    Returns:
      (segments_modified, actual_fossil_decrease_t, bio_increase_t)
    """
    segs = copy.deepcopy(base_segments)
    x = max(0.0, float(x_decrease_t))

    # Total available mass of the selected fuel across all segments
    total_avail = sum(float(seg.get(f"{fuel}_t", 0.0) or 0.0) for seg in segs)
    if total_avail <= 0.0:
        return segs, 0.0, 0.0

    x = min(x, total_avail)
    remaining = x

    # Select the LCV for the chosen fossil
    if fuel == "HSFO":
        LCV_SEL = LCV_HSFO
    elif fuel == "LFO":
        LCV_SEL = LCV_LFO
    else:
        LCV_SEL = LCV_MGO

    if LCV_BIO <= 0.0 or LCV_SEL <= 0.0:
        # No meaningful energy-based shift possible
        return segs, 0.0, 0.0

    # Indices sorted by ETS-driven priority (see _segment_opt_priority)
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

        # 1) Reduce fossil in this segment
        seg[f"{fuel}_t"] = avail - take
        remaining -= take
        actual_dec += take

        # 2) Add BIO in the *same* segment, energy-equivalently
        bio_add_t = take * (LCV_SEL / LCV_BIO)
        if bio_add_t > 0.0:
            seg["BIO_t"] = float(seg.get("BIO_t", 0.0) or 0.0) + bio_add_t
            total_bio_added += bio_add_t

    if actual_dec <= 0.0:
        return segs, 0.0, 0.0

    bio_inc_t = actual_dec * (LCV_SEL / LCV_BIO)
    # total_bio_added should be ≈ bio_inc_t (small numerical differences possible)
    return segs, actual_dec, bio_inc_t


def _scope_from_segments(segments: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    """
    Use the same per-segment machinery as the main app to compute:
    - E_scope_x: total in-scope energy [MJ]
    - num_phys_x: numerator Σ(E_f_scope * WtW_f)
    - E_rfnbo_scope_x: in-scope RFNBO energy [MJ]

    and then apply the same final global WtW-prioritized rearrangement that is
    used for the combined in-scope stack in the main view.
    """
    combined_scope_x = {"ELEC": 0.0, "RFNBO": 0.0, "BIO": 0.0, "HSFO": 0.0, "LFO": 0.0, "MGO": 0.0}
    combined_all_x   = {"ELEC": 0.0, "RFNBO": 0.0, "BIO": 0.0, "HSFO": 0.0, "LFO": 0.0, "MGO": 0.0}

    for seg in segments:
        energies_all = _segment_energy_mj(seg)
        energies_scope, elec_mj_seg = _segment_scope_with_toggle(seg, energies_all)

        # 100% energies per fuel (for “100% of segment energy” cap)
        for f in FUELS:
            combined_all_x[f] += float(energies_all.get(f, 0.0) or 0.0)
        combined_all_x["ELEC"] += elec_mj_seg

        # In-scope sums (before global rearrangement)
        for f in FUELS:
            combined_scope_x[f] += float(energies_scope.get(f, 0.0) or 0.0)
        combined_scope_x["ELEC"] += elec_mj_seg

    E_scope_x_raw = sum(combined_scope_x.values())
    if E_scope_x_raw <= 0.0:
        return 0.0, 0.0, 0.0

    # Apply the same global rearrangement only if prioritized allocation is used in UI
    if _has_prioritized_segments(segments):
        combined_scope_final_x = _global_rearrange_scope(combined_all_x, combined_scope_x, wtw)
    else:
        combined_scope_final_x = combined_scope_x

    E_scope_x = sum(combined_scope_final_x.values())
    num_phys_x = sum(
        combined_scope_final_x.get(k, 0.0) * wtw.get(k, 0.0)
        for k in ["HSFO", "LFO", "MGO", "BIO", "RFNBO", "ELEC"]
    )
    E_rfnbo_scope_x = combined_scope_final_x.get("RFNBO", 0.0)
    return E_scope_x, num_phys_x, E_rfnbo_scope_x


def _scope_and_balance_from_segments(year_idx: int,
                                     segments_mod: List[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    """
    Helper for the optimizer:
    given a modified segments list, returns
    (g_att_x, E_scope_x, final_balance_tCO2e_x, pooling_used_tCO2e_x)
    for the year at index year_idx, using the same pooling/banking logic
    as the main results table.
    """
    year = YEARS[year_idx]
    E_scope_x, num_phys_x, E_rfnbo_scope_x = _scope_from_segments(segments_mod)
    if E_scope_x <= 0.0:
        return 0.0, 0.0, 0.0, 0.0

    # Rewarded denominator (RFNBO doubling until 2033)
    r = 2.0 if year <= 2033 else 1.0
    den_rwd_x = E_scope_x + (r - 1.0) * E_rfnbo_scope_x
    g_att_x = (num_phys_x / den_rwd_x) if den_rwd_x > 0.0 else 0.0

    # Raw & effective compliance balance
    g_target = float(LIMITS_DF["Limit_gCO2e_per_MJ"].iloc[year_idx])
    CB_g_x = (g_target - g_att_x) * E_scope_x
    CB_t_raw_x = CB_g_x / 1e6
    cb_eff_x = CB_t_raw_x + carry_in_list[year_idx]

    # Pooling (same logic as in the main loop)
    if year >= int(pooling_start_year):
        if pooling_tco2e_input >= 0:
            pre_deficit_x = max(-cb_eff_x, 0.0)
            pool_use_x = min(pooling_tco2e_input, pre_deficit_x)
        else:
            provide_abs = abs(pooling_tco2e_input)
            pre_surplus_x = max(cb_eff_x, 0.0)
            pool_use_x = -min(provide_abs, pre_surplus_x)
    else:
        pool_use_x = 0.0

    # Banking
    if year >= int(banking_start_year):
        pre_surplus = max(cb_eff_x, 0.0)
        requested_bank = max(banking_tco2e_input, 0.0)
        bank_use_x = min(requested_bank, pre_surplus)
    else:
        bank_use_x = 0.0

    # Clamp: cannot end more negative just because we banked/provided too much
    final_bal_x = cb_eff_x + pool_use_x - bank_use_x
    if final_bal_x < 0.0:
        needed = -final_bal_x
        trim_bank = min(needed, bank_use_x)
        bank_use_x -= trim_bank
        needed -= trim_bank
        if needed > 0.0 and pool_use_x < 0.0:
            pool_use_x += needed
        final_bal_x = cb_eff_x + pool_use_x - bank_use_x

    return g_att_x, E_scope_x, final_bal_x, pool_use_x

HSFO_voy_t = totals_mass["intra_voy"]["HSFO"] + totals_mass["extra_voy"]["HSFO"]
LFO_voy_t  = totals_mass["intra_voy"]["LFO"]  + totals_mass["extra_voy"]["LFO"]
MGO_voy_t  = totals_mass["intra_voy"]["MGO"]  + totals_mass["extra_voy"]["MGO"]
BIO_voy_t  = totals_mass["intra_voy"]["BIO"]  + totals_mass["extra_voy"]["BIO"]
RFNBO_voy_t= totals_mass["intra_voy"]["RFNBO"]+ totals_mass["extra_voy"]["RFNBO"]
HSFO_berth_t = totals_mass["eu_berth"]["HSFO"]
LFO_berth_t  = totals_mass["eu_berth"]["LFO"]
MGO_berth_t  = totals_mass["eu_berth"]["MGO"]
BIO_berth_t  = totals_mass["eu_berth"]["BIO"]
RFNBO_berth_t= totals_mass["eu_berth"]["RFNBO"]
ELEC_MJ = ELEC_MJ_input
# ──────────────────────────────────────────────────────────────────────────────
# EU ETS — Emissions and Cost (tCO2 and EUR)
# ──────────────────────────────────────────────────────────────────────────────

# Tank-to-wake emission factors [tCO2 / t fuel]
EF_HSFO_tco2_per_t = 3.114  # residual / HFO
EF_LFO_tco2_per_t  = 3.151  # typical light fuel oil
EF_MGO_tco2_per_t  = 3.206  # marine gas oil / diesel

# ── NEW: from 2026 EU ETS maritime includes CH4 + N2O (CO2e) ──
# GWP100 for CO2e conversion
GWP_CH4 = 28.0
GWP_N2O = 265.0

# Default non-CO2 TTW factors [t gas / t fuel] (keep as constants or expose to UI later)
EF_HSFO_tch4_per_t = 0.00005
EF_LFO_tch4_per_t  = 0.00005
EF_MGO_tch4_per_t  = 0.00005

EF_HSFO_tn2o_per_t = 0.00018
EF_LFO_tn2o_per_t  = 0.00018
EF_MGO_tn2o_per_t  = 0.00018

def ets_coverage_factor(year: int) -> float:
    # Phase-in for shipping (you start at 2025 anyway):
    # 2024: 40%, 2025: 70%, 2026+: 100%
    if year <= 2024:
        return 0.40
    if year == 2025:
        return 0.70
    return 1.00

def ets_geo_emissions_tco2e(ets_masses: Dict[str, float], year: int) -> Tuple[float, float, float, float]:
    """
    Returns (CO2_t, CH4_t, N2O_t, CO2e_t) in geographic scope (before coverage factor).
    2025: CO2 only. 2026+: CO2e = CO2 + CH4*GWP_CH4 + N2O*GWP_N2O.
    """
    co2_t = (
        ets_masses["HSFO"] * EF_HSFO_tco2_per_t +
        ets_masses["LFO"]  * EF_LFO_tco2_per_t  +
        ets_masses["MGO"]  * EF_MGO_tco2_per_t
    )

    if year >= 2026:
        ch4_t = (
            ets_masses["HSFO"] * EF_HSFO_tch4_per_t +
            ets_masses["LFO"]  * EF_LFO_tch4_per_t  +
            ets_masses["MGO"]  * EF_MGO_tch4_per_t
        )
        n2o_t = (
            ets_masses["HSFO"] * EF_HSFO_tn2o_per_t +
            ets_masses["LFO"]  * EF_LFO_tn2o_per_t  +
            ets_masses["MGO"]  * EF_MGO_tn2o_per_t
        )
        co2e_t = co2_t + ch4_t * GWP_CH4 + n2o_t * GWP_N2O
    else:
        ch4_t = 0.0
        n2o_t = 0.0
        co2e_t = co2_t

    return co2_t, ch4_t, n2o_t, co2e_t


def _ets_in_scope_masses(totals_mass: Dict[str, Dict[str, float]],
                         pure_bio_pct: float,
                         bio_mix_type: str) -> Dict[str, float]:
    """
    Compute in-scope fuel masses for EU ETS:
      • 100% of intra-EU voyages
      • 100% of EU at-berth
      • 50% of extra-EU voyages
    BIO_t is a blend: pure_bio_pct% = zero-rated; the rest is fossil
    of the selected 'Bio Mix Type'.
    """
    ets_masses = {f: 0.0 for f in ["HSFO", "LFO", "MGO"]}

    # 1) Fossil fuels directly
    for f in ["HSFO", "LFO", "MGO"]:
        ets_masses[f] = (
            totals_mass["intra_voy"][f] +
            totals_mass["eu_berth"][f] +
            0.5 * totals_mass["extra_voy"][f]
        )

    # 2) BIO blend — split into pure bio (0 ETS) + fossil share
    bio_in_scope_t = (
        totals_mass["intra_voy"]["BIO"] +
        totals_mass["eu_berth"]["BIO"] +
        0.5 * totals_mass["extra_voy"]["BIO"]
    )
    pure_bio_frac = max(0.0, min(float(pure_bio_pct) / 100.0, 1.0))
    fossil_share_t = bio_in_scope_t * (1.0 - pure_bio_frac)

    # Assign fossil share to the selected mix fossil fuel
    mix_type = (bio_mix_type or "").upper()
    if "HSFO" in mix_type:
        ets_masses["HSFO"] += fossil_share_t
    elif "LFO" in mix_type or "VLSFO" in mix_type:
        ets_masses["LFO"] += fossil_share_t
    elif "MGO" in mix_type:
        ets_masses["MGO"] += fossil_share_t
    else:
        # fallback: treat fossil part as MGO if type is unknown
        ets_masses["MGO"] += fossil_share_t

    return ets_masses

# Compute in-scope masses using current UI inputs
ets_masses = _ets_in_scope_masses(totals_mass, pure_bio_pct, bio_mix_type)

# ── ETS emissions/cost PER YEAR (2025–2050); 2026+ includes CH4+N2O as CO2e ──
ETS_Emissions_tCO2e_series: List[float] = []
ETS_Cost_EUR_series: List[float] = []

for y in YEARS:
    _, _, _, co2e_geo_t = ets_geo_emissions_tco2e(ets_masses, y)  # geo scope, before coverage
    cov = ets_coverage_factor(y)
    ETS_Emissions_tCO2e_series.append(co2e_geo_t * cov)
    ETS_Cost_EUR_series.append((co2e_geo_t * cov) * eua_price_eur_per_tco2)


def _ets_cost_from_segments(
    segments: List[Dict[str, Any]],
    pure_bio_pct: float,
    bio_mix_type: str,
    eua_price_eur_per_tco2: float,
    year: int | str,
) -> float:
    """
    EU ETS cost [EUR] from a given list of segments:
      • 100% intra-EU + 100% EU at-berth + 50% extra-EU
      • BIO fossil share handled via Bio Mix Type
      • Coverage factor by year
      • 2025: CO2 only; 2026+: CO2e (CO2+CH4+N2O)
    """

    # --- NEW: coerce year safely (handles "2025", "2026+", etc.) ---
    if isinstance(year, str):
        y = year.strip()
        if y.endswith("+"):
            y = y[:-1]
        try:
            year_i = int(y)
        except Exception:
            year_i = 2025
    else:
        year_i = int(year)

    totals_mass_local = {
        "intra_voy": {f: 0.0 for f in ["HSFO", "LFO", "MGO", "BIO"]},
        "extra_voy": {f: 0.0 for f in ["HSFO", "LFO", "MGO", "BIO"]},
        "eu_berth":  {f: 0.0 for f in ["HSFO", "LFO", "MGO", "BIO"]},
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
            totals_mass_local[bucket][f] += float(seg.get(f"{f}_t", 0.0) or 0.0)

    ets_masses_local = _ets_in_scope_masses(totals_mass_local, pure_bio_pct, bio_mix_type)

    # CO2e in geographic scope
    _, _, _, co2e_geo_t = ets_geo_emissions_tco2e(ets_masses_local, year_i)

    cov = ets_coverage_factor(year_i)
    return (co2e_geo_t * cov) * eua_price_eur_per_tco2


def scoped_and_intensity_from_masses(h_v, l_v, m_v, b_v, r_v, h_b, l_b, m_b, b_b, r_b, elec_MJ, wtw_dict, year) -> Tuple[float,float,float]:
    energies_v = {
        "HSFO": compute_energy_MJ(h_v, LCV_HSFO),
        "LFO":  compute_energy_MJ(l_v, LCV_LFO),
        "MGO":  compute_energy_MJ(m_v, LCV_MGO),
        "BIO":  compute_energy_MJ(b_v, LCV_BIO),
        "RFNBO":compute_energy_MJ(r_v, LCV_RFNBO),
    }
    energies_b = {
        "HSFO": compute_energy_MJ(h_b, LCV_HSFO),
        "LFO":  compute_energy_MJ(l_b, LCV_LFO),
        "MGO":  compute_energy_MJ(m_b, LCV_MGO),
        "BIO":  compute_energy_MJ(b_b, LCV_BIO),
        "RFNBO":compute_energy_MJ(r_b, LCV_RFNBO),
    }
    scoped_x = scoped_energies_extra_eu(energies_v, energies_b, elec_MJ, wtw_dict)
    E_scope_x = sum(scoped_x.values())
    num_phys_x = sum(scoped_x.get(k,0.0) * wtw_dict.get(k,0.0) for k in wtw_dict.keys())
    E_rfnbo_scope_x = scoped_x.get("RFNBO", 0.0)
    return E_scope_x, num_phys_x, E_rfnbo_scope_x

# ---- OPTIMIZER CHANGE 1: expand return values to include final balance and pooling used
def penalty_eur_with_masses_for_year(year_idx: int,
                                     h_v, l_v, m_v, b_v, r_v,
                                     h_b, l_b, m_b, b_b, r_b) -> Tuple[float, float, float, float]:
    year = YEARS[year_idx]
    g_target = LIMITS_DF["Limit_gCO2e_per_MJ"].iloc[year_idx]
    E_scope_x, num_phys_x, E_rfnbo_scope_x = scoped_and_intensity_from_masses(
        h_v, l_v, m_v, b_v, r_v, h_b, l_b, m_b, b_b, r_b, ELEC_MJ, wtw, year
    )
    if E_scope_x <= 0: 
        return 0.0, 0.0, 0.0, 0.0
    r = 2.0 if year <= 2033 else 1.0
    den_rwd_x = E_scope_x + (r - 1.0) * E_rfnbo_scope_x
    g_att_x = (num_phys_x / den_rwd_x) if den_rwd_x > 0 else 0.0

    CB_g_x = (g_target - g_att_x) * E_scope_x
    CB_t_raw_x = CB_g_x / 1e6
    cb_eff_x = CB_t_raw_x + carry_in_list[year_idx]

    if YEARS[year_idx] >= int(pooling_start_year):
        if pooling_tco2e_input >= 0:
            # uptake: cap by current deficit (negative cb_eff_x)
            pre_deficit_x = max(-cb_eff_x, 0.0)
            pool_use_x = min(pooling_tco2e_input, pre_deficit_x)
        else:
            # provide: cap by current surplus (positive cb_eff_x)
            provide_abs = abs(pooling_tco2e_input)
            pre_surplus_x = max(cb_eff_x, 0.0)
            pool_use_x = -min(provide_abs, pre_surplus_x)
    else:
        pool_use_x = 0.0

    if YEARS[year_idx] >= int(banking_start_year):
        pre_surplus = max(cb_eff_x, 0.0)
        requested_bank = max(banking_tco2e_input, 0.0)
        bank_use_x = min(requested_bank, pre_surplus)
    else:
        bank_use_x = 0.0

    final_bal_x = cb_eff_x + pool_use_x - bank_use_x
    if final_bal_x < 0:
        needed = -final_bal_x
        trim_bank = min(needed, bank_use_x); bank_use_x -= trim_bank; needed -= trim_bank
        if needed > 0 and pool_use_x < 0:
            pool_use_x += needed
        final_bal_x = cb_eff_x + pool_use_x - bank_use_x

    if final_bal_x < 0:
        step_idx = _step_of_year(year)
        start_count = max(int(consecutive_deficit_years_seed), 1)
        step_mult = 1.0 + (start_count - 1) * 0.10
        penalty_eur_x = euros_from_tco2e(-final_bal_x, g_att_x, penalty_price_eur_per_vlsfo_t) * step_mult
    else:
        penalty_eur_x = 0.0
    # return: penalty, attained, final balance (tCO2e), pooling applied (tCO2e)
    return penalty_eur_x, g_att_x, final_bal_x, pool_use_x

def masses_after_shift_generic(fuel: str, x_decrease_t: float) -> Tuple[float,float,float,float,float,float,float,float,float,float]:
    h_v, l_v, m_v, b_v, r_v = HSFO_voy_t, LFO_voy_t, MGO_voy_t, BIO_voy_t, RFNBO_voy_t
    h_b, l_b, m_b, b_b, r_b = HSFO_berth_t, LFO_berth_t, MGO_berth_t, BIO_berth_t, RFNBO_berth_t
    if fuel == "HSFO": s_v, s_b, LCV_S = h_v, h_b, LCV_HSFO
    elif fuel == "LFO": s_v, s_b, LCV_S = l_v, l_b, LCV_LFO
    else:              s_v, s_b, LCV_S = m_v, m_b, LCV_MGO
    x = max(0.0, float(x_decrease_t)); x = min(x, s_v + s_b)
    bio_increase_t = (x * LCV_S / LCV_BIO) if LCV_BIO > 0 else 0.0
    take_v = min(x, s_v); s_v -= take_v
    rem = x - take_v; s_b = max(0.0, s_b - rem)
    add_b = min(bio_increase_t, float("inf")); b_b += add_b
    rem_bio = bio_increase_t - add_b
    if rem_bio > 0: b_v += rem_bio
    if fuel == "HSFO": h_v, h_b = s_v, s_b
    elif fuel == "LFO": l_v, l_b = s_v, s_b
    else: m_v, m_b = s_v, s_b
    return h_v, l_v, m_v, b_v, r_v, h_b, l_b, m_b, b_b, r_b

# Optimizer search (coarse + fine)
# ---- OPTIMIZER CHANGE 2: include credits and pooling cost in candidate evaluation
credit_per_tco2e_val_opt = parse_us_any(st.session_state.get("credit_per_tco2e_str", _get(DEFAULTS,"credit_per_tco2e",200.0)), 200.0)

# Optimizer search — finer: dense grid + golden-section, and full cost (penalty − credits + pooling + bio premium)
credit_per_tco2e_val_opt = parse_us_any(
    st.session_state.get("credit_per_tco2e_str", _get(DEFAULTS, "credit_per_tco2e", 200.0)), 200.0
)
penalty_vlsfo_opt = parse_us_any(
    st.session_state.get("penalty_per_vlsfo_t_str", _get(DEFAULTS, "penalty_price_eur_per_vlsfo_t", 2400.0)), 2400.0
)

dec_opt_list, bio_inc_opt_list = [], []
for i in range(len(YEARS)):
    if selected_fuel_for_opt == "HSFO":
        total_avail, LCV_SEL = HSFO_voy_t + HSFO_berth_t, LCV_HSFO
    elif selected_fuel_for_opt == "LFO":
        total_avail, LCV_SEL = LFO_voy_t + LFO_berth_t, LCV_LFO
    else:
        total_avail, LCV_SEL = MGO_voy_t + MGO_berth_t, LCV_MGO

    x_max = total_avail
    if x_max <= 0 or LCV_BIO <= 0:
        dec_opt_list.append(0.0)
        bio_inc_opt_list.append(0.0)
        continue

    def _total_cost_for_x(x: float) -> float:
        """
        Objective for the optimizer: given a candidate fossil→BIO shift x [t],
        apply it segment-wise and compute the combined total cost:

          FuelEU penalty − FuelEU credits
        + BIO premium
        + FuelEU pooling cost
        + EU ETS cost

        using the same per-segment scope logic as the main results table.
        """
        # 1) Apply fossil→BIO shift on a copy of the current segments
        segments_mod, actual_dec, bio_inc_t = _apply_shift_to_segments(
            st.session_state["abs_segments"],
            selected_fuel_for_opt,
            x
        )

        # 2) Compute attained intensity and final balance for this candidate
        g_att_x, E_scope_x, final_bal_x, pool_use_x = _scope_and_balance_from_segments(i, segments_mod)
        if E_scope_x <= 0.0:
            return 0.0  # no in-scope energy → no cost

        # 3) Penalty / credits (FuelEU)
        if final_bal_x < 0:
            step_idx = _step_of_year(YEARS[i])
            start_count = max(int(consecutive_deficit_years_seed), 1)
            step_mult = 1.0 + (start_count - 1) * 0.10
            penalty_eur_x = euros_from_tco2e(-final_bal_x, g_att_x, penalty_vlsfo_opt) * step_mult
            credits_eur_x = 0.0
        else:
            penalty_eur_x = 0.0
            credits_eur_x = final_bal_x * credit_per_tco2e_val_opt

        # 4) Pooling cost, BIO premium & ETS cost at candidate mix
        pooling_cost_x = pool_use_x * pooling_price_eur_per_tco2e_val
        bio_total_t_x = sum(float(seg.get("BIO_t", 0.0) or 0.0) for seg in segments_mod)
        bio_premium_eur_x = bio_total_t_x * bio_premium_eur_per_t_val

        ets_cost_eur_x = _ets_cost_from_segments(
            segments_mod,
            pure_bio_pct,
            bio_mix_type,
            eua_price_eur_per_tco2,
            year=YEARS[i],
        )

        # 5) Objective: FuelEU + ETS total cost
        return (
            penalty_eur_x
            - credits_eur_x
            + bio_premium_eur_x
            + pooling_cost_x
            + ets_cost_eur_x
        )


    # A) dense coarse scan to bracket minimum
    steps_coarse = 200
    best_x, best_cost = 0.0, float("inf")
    for s in range(steps_coarse + 1):
        x = x_max * s / steps_coarse
        c = _total_cost_for_x(x)
        if c < best_cost:
            best_cost, best_x = c, x

    # bracket around best coarse point (±3 bins)
    bin_w = x_max / steps_coarse
    a = max(0.0, best_x - 3 * bin_w)
    b = min(x_max, best_x + 3 * bin_w)

    # B) golden-section refinement on [a, b]
    phi = (5 ** 0.5 - 1) / 2.0  # ≈0.618
    tol = max(x_max * 1e-5, 1e-4)  # tonnes

    c = b - phi * (b - a)
    d = a + phi * (b - a)
    fc = _total_cost_for_x(c)
    fd = _total_cost_for_x(d)

    it, max_iter = 0, 120
    while (b - a) > tol and it < max_iter:
        if fc <= fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = _total_cost_for_x(c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = _total_cost_for_x(d)
        it += 1

    dec_opt = (a + b) / 2.0
    bio_inc_opt = dec_opt * (LCV_SEL / LCV_BIO) if LCV_BIO > 0 else 0.0
    dec_opt_list.append(dec_opt)
    bio_inc_opt_list.append(bio_inc_opt)

# Recompute optimized cost columns (EUR) — now FuelEU + ETS
penalties_eur_opt_col, bio_premium_cost_eur_opt_col, total_cost_eur_opt_col = [], [], []
for i in range(len(YEARS)):
    x_opt = dec_opt_list[i]

    if x_opt <= 0.0 or LCV_BIO <= 0.0:
        # No feasible shift: optimized = base FuelEU + base ETS
        penalties_eur_opt = penalties_eur[i]
        bio_premium_eur_opt = bio_premium_cost_eur_col[i]
        credits_eur_opt = credits_eur[i]
        pooling_cost_eur_opt = pooling_cost_eur_col[i]
        ets_cost_eur_opt = ETS_Cost_EUR_series[i]  # base ETS for that year

        penalties_eur_opt_col.append(penalties_eur_opt)
        bio_premium_cost_eur_opt_col.append(bio_premium_eur_opt)
        total_cost_eur_opt_col.append(
            penalties_eur_opt
            - credits_eur_opt
            + bio_premium_eur_opt
            + pooling_cost_eur_opt
            + ets_cost_eur_opt
        )

    else:
        # Apply the optimal shift on a copy of the segments
        segments_opt, actual_dec_opt, bio_inc_opt = _apply_shift_to_segments(
            st.session_state["abs_segments"],
            selected_fuel_for_opt,
            x_opt
        )

        # Recompute intensity and balance for this candidate (FuelEU)
        g_att_opt, E_scope_opt, final_bal_x, pool_use_x = _scope_and_balance_from_segments(i, segments_opt)

        # Penalty / credits at optimum
        if final_bal_x < 0:
            step_idx = _step_of_year(YEARS[i])
            start_count = max(int(consecutive_deficit_years_seed), 1)
            step_mult = 1.0 + (start_count - 1) * 0.10
            penalties_eur_opt = euros_from_tco2e(-final_bal_x, g_att_opt, penalty_vlsfo_opt) * step_mult
            credits_eur_opt = 0.0
        else:
            penalties_eur_opt = 0.0
            credits_eur_opt = final_bal_x * credit_per_tco2e_val_opt

        # Pooling cost and BIO premium at optimum
        pooling_cost_eur_opt = pool_use_x * pooling_price_eur_per_tco2e_val
        bio_total_t_opt = sum(float(seg.get("BIO_t", 0.0) or 0.0) for seg in segments_opt)
        bio_premium_eur_opt = bio_total_t_opt * bio_premium_eur_per_t_val

        # ETS cost at optimum (from optimized segments)
        ets_cost_eur_opt = _ets_cost_from_segments(
            segments_opt,
            pure_bio_pct,
            bio_mix_type,
            eua_price_eur_per_tco2,
            year=YEARS[i],
        )

        penalties_eur_opt_col.append(penalties_eur_opt)
        bio_premium_cost_eur_opt_col.append(bio_premium_eur_opt)
        total_cost_eur_opt_col.append(
            penalties_eur_opt
            - credits_eur_opt
            + bio_premium_eur_opt
            + pooling_cost_eur_opt
            + ets_cost_eur_opt
        )

# ──────────────────────────────────────────────────────────────────────────────
# Table
# ──────────────────────────────────────────────────────────────────────────────
decrease_col_name = f"{selected_fuel_for_opt}_decrease(t)_for_Opt_Cost"
emissions_tco2e = num_phys / 1e6  # physical emissions for the in-scope mix (no RFNBO reward)

df_cost = pd.DataFrame({
    "Year": YEARS,
    "Reduction_%": LIMITS_DF["Reduction_%"].tolist(),
    "Limit_gCO2e_per_MJ": LIMITS_DF["Limit_gCO2e_per_MJ"].tolist(),
    "Actual_gCO2e_per_MJ": [attained_intensity_for_year(y) for y in YEARS],
    "Emissions_tCO2e": [emissions_tco2e]*len(YEARS),

    "Compliance_Balance_tCO2e": cb_raw_t,
    "CarryIn_Banked_tCO2e": carry_in_list,
    "Effective_Balance_tCO2e": cb_eff_t,
    "Banked_to_Next_Year_tCO2e": bank_applied,
    "Pooling_tCO2e_Applied": pool_applied,
    "Final_Balance_tCO2e": final_balance_t,

    "Pooling_Cost_EUR": pooling_cost_eur_col,
    "Penalty_EUR": penalties_eur,
    "Credit_EUR": credits_eur,
    "BIO Premium Cost_EUR": bio_premium_cost_eur_col,
    "Net_Total_Cost_EUR": net_total_cost_eur_col,

    decrease_col_name: dec_opt_list,
    "BIO_Increase(t)_For_Opt_Cost": bio_inc_opt_list,
    "Total_Cost_FUEL_EU_ETS_Opt": total_cost_eur_opt_col,
})
# ──────────────────────────────────────────────────────────────────────────────
# Insert EU ETS columns between Net_Total_Cost_EUR and *_decrease(t)_for_Opt_Cost
# and add combined FuelEU + EU ETS cost
# ──────────────────────────────────────────────────────────────────────────────
ets_emissions_series = ETS_Emissions_tCO2e_series
ets_cost_series      = ETS_Cost_EUR_series

# 1) ETS columns (light blue)
df_cost.insert(insert_pos, "ETS_Emissions_tCO2e", ets_emissions_series)
insert_pos += 1
df_cost.insert(insert_pos, "ETS_Cost_EUR", ets_cost_series)
insert_pos += 1


# 2) Combined FuelEU + EU ETS cost (between ETS_Cost_EUR and *_decrease column)
fuel_eu_plus_ets_series = [
    net_total_cost_eur_col[i] + ets_cost_series[i]
    for i in range(len(YEARS))
]
df_cost.insert(insert_pos, "FuelEU_+_EU_ETS_Cost", fuel_eu_plus_ets_series)

df_fmt = df_cost.copy()
for col in df_fmt.columns:
    if col != "Year":
        df_fmt[col] = df_fmt[col].apply(us2)

def _highlight_ets_columns(col):
    """Give ETS and combined-cost columns distinct background colors."""
    if col.name in ["ETS_Emissions_tCO2e", "ETS_Cost_EUR"]:
        # EU ETS columns: light blue
        return ["background-color: #e0f2fe; font-weight: 600;"] * len(col)
    if col.name == "FuelEU_+_EU_ETS_Cost":
        # Combined FuelEU + EU ETS cost: soft yellow (different from blue and white)
        return ["background-color: #fef9c3; font-weight: 600;"] * len(col)
    return [""] * len(col)


df_display = df_fmt.style.apply(_highlight_ets_columns, axis=0)

st.dataframe(
    df_display,
    use_container_width=True,
    column_order=[c for c in df_fmt.columns if c != "Reduction_%"]
)

# ──────────────────────────────────────────────────────────────────────────────
# Interactive simulation: BIO premium vs optimized cost vs pooling strategy
# ──────────────────────────────────────────────────────────────────────────────

def _total_cost_for_candidate_premium(
    year_idx: int,
    segments_mod: List[Dict[str, Any]],
    bio_premium_eur_per_t_candidate: float,
) -> float:
    """
    Total cost (FuelEU + ETS) for a given candidate segments mix and BIO premium,
    using the same core logic as the main optimizer but with:
      • pooling cost forced to 0 for this BIO-optimization simulation
      • carry-in and banking as in the main table (carry_in_list[...] reused)
    """
    g_att_x, E_scope_x, final_bal_x, pool_use_x = _scope_and_balance_from_segments(
        year_idx,
        segments_mod,
    )

    if E_scope_x <= 0.0:
        return 0.0

    year = YEARS[year_idx]

    # Penalty / credits
    if final_bal_x < 0:
        step_idx = _step_of_year(year)
        start_count = max(int(consecutive_deficit_years_seed), 1)
        step_mult = 1.0 + (start_count - 1) * 0.10
        penalty_eur_x = euros_from_tco2e(-final_bal_x, g_att_x, penalty_vlsfo_opt) * step_mult
        credits_eur_x = 0.0
    else:
        penalty_eur_x = 0.0
        credits_eur_x = final_bal_x * credit_per_tco2e_val_opt

    # For this simulation, we assume no pooling cost in the BIO-optimization route
    pooling_cost_x = 0.0

    # BIO premium cost (all BIO tonnes in the candidate mix)
    bio_total_t_x = sum(float(seg.get("BIO_t", 0.0) or 0.0) for seg in segments_mod)
    bio_premium_cost_x = bio_total_t_x * bio_premium_eur_per_t_candidate

    # ETS cost at candidate mix
    ets_cost_eur_x = _ets_cost_from_segments(
        segments_mod,
        pure_bio_pct,
        bio_mix_type,
        eua_price_eur_per_tco2,
        eua_year_selection,
    )

    # Total cost = FuelEU (penalty − credit + BIO premium + pooling) + ETS
    return penalty_eur_x - credits_eur_x + bio_premium_cost_x + pooling_cost_x + ets_cost_eur_x


def _optimized_total_cost_for_year_and_premium(
    year_idx: int,
    bio_premium_eur_per_t_candidate: float,
) -> Tuple[float, float]:
    """
    For a given year index and BIO premium [EUR/t], find the optimal fossil→BIO
    shift x [t] (reduce selected fuel, increase BIO energy-equivalently) that
    minimizes the total cost FuelEU + ETS.

    Returns (min_total_cost, x_opt).
    """
    # Total available mass of the selected fuel (voyage + berth)
    if selected_fuel_for_opt == "HSFO":
        total_avail = HSFO_voy_t + HSFO_berth_t
    elif selected_fuel_for_opt == "LFO":
        total_avail = LFO_voy_t + LFO_berth_t
        # note: LFO_voy_t / LFO_berth_t defined earlier from totals_mass
    else:
        total_avail = MGO_voy_t + MGO_berth_t

    # If no fossil available or no BIO LCV, optimization cannot change the mix
    if total_avail <= 0.0 or LCV_BIO <= 0.0:
        # Cost with current segments and given BIO premium
        cost_no_shift = _total_cost_for_candidate_premium(
            year_idx,
            st.session_state.get("abs_segments", []),
            bio_premium_eur_per_t_candidate,
        )
        return cost_no_shift, 0.0

    x_max = total_avail

    def _objective(x: float) -> float:
        segments_mod, _, _ = _apply_shift_to_segments(
            st.session_state["abs_segments"],
            selected_fuel_for_opt,
            x,
        )
        return _total_cost_for_candidate_premium(
            year_idx,
            segments_mod,
            bio_premium_eur_per_t_candidate,
        )

    # Coarse scan on [0, x_max]
    steps_coarse = 200
    best_x, best_cost = 0.0, float("inf")
    for s in range(steps_coarse + 1):
        x = x_max * s / steps_coarse
        c = _objective(x)
        if c < best_cost:
            best_cost, best_x = c, x

    # Golden-section refinement around the best coarse point (±3 bins)
    bin_w = x_max / steps_coarse
    a = max(0.0, best_x - 3.0 * bin_w)
    b = min(x_max, best_x + 3.0 * bin_w)

    phi = (5 ** 0.5 - 1) / 2.0  # ≈ 0.618
    tol = max(x_max * 1e-5, 1e-4)

    c = b - phi * (b - a)
    d = a + phi * (b - a)
    fc = _objective(c)
    fd = _objective(d)

    it, max_iter = 0, 120
    while (b - a) > tol and it < max_iter:
        if fc <= fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = _objective(c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = _objective(d)
        it += 1

    x_opt = (a + b) / 2.0
    cost_opt = _objective(x_opt)
    return cost_opt, x_opt


st.markdown("### Interactive simulation: Optimized FuelEU + EU ETS cost     BIO VS Pooling Policy")

if not st.session_state.get("abs_segments"):
    st.info("Add at least one voyage / berth segment in the sidebar to run the simulation.")
else:
    # Controls for the simulation
    with st.expander("Simulation controls", expanded=True):
        sim_year = st.selectbox(
            "Year for simulation",
            YEARS,
            index=0,
            key="sim_year_bio_premium",
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            premium_min = float_text_input(
                "BIO premium min [€/t]",
                0.0,
                key="sim_premium_min",
                min_value=0.0,
            )
        with c2:
            premium_max = float_text_input(
                "BIO premium max [€/t]",
                1_000.0,
                key="sim_premium_max",
                min_value=0.0,
            )
        with c3:
            premium_step = float_text_input(
                "BIO premium step [€/t]",
                50.0,
                key="sim_premium_step",
                min_value=1.0,
            )

        pooling_price_compare = float_text_input(
            "Pooling price for comparison [€/tCO₂e]",
            200.0,
            key="sim_pool_price",
            min_value=0.0,
        )

    # Basic guards
    if premium_step <= 0:
        st.warning("BIO premium step must be > 0 to run the simulation.")
    else:
        # Ensure min ≤ max
        if premium_max < premium_min:
            premium_min, premium_max = premium_max, premium_min

        # Build premium grid (X-axis)
        n_points = int((premium_max - premium_min) // premium_step) + 1
        if n_points <= 0:
            n_points = 1
        bio_premium_grid = [
            premium_min + i * premium_step
            for i in range(n_points)
        ]

        # Index for selected year
        try:
            year_idx_sim = YEARS.index(int(sim_year))
        except ValueError:
            year_idx_sim = 0

        # 1) BIO optimization route: for each BIO premium → optimized Total_Cost_FUEL_EU_ETS_Opt
        cost_opt_grid: List[float] = []
        for prem in bio_premium_grid:
            cost_opt, _ = _optimized_total_cost_for_year_and_premium(
                year_idx_sim,
                prem,
            )
            cost_opt_grid.append(cost_opt)

        # 2) Pure pooling route:
        #    Use Final_Balance_tCO2e from the first-parameters results table (no extra BIO optimization),
        #    and assume we compensate that balance entirely via pooling at the user-input price.
        try:
            row_sim = df_cost[df_cost["Year"] == int(sim_year)].iloc[0]
            final_balance_base = float(row_sim["Final_Balance_tCO2e"])
            ets_cost_base = float(row_sim["ETS_Cost_EUR"])
        except (IndexError, KeyError):
            final_balance_base = 0.0
            ets_cost_base = ETS_Cost_EUR_series[year_idx_sim]

        # Pooling component: pool_use = −Final_Balance ⇒ cost = pool_use * price = −Final_Balance * price
        pooling_cost_component = -final_balance_base * pooling_price_compare

        # Total cost for pooling route:
        #   = ETS (base, from first parameters)
        #   + BIO premium for existing BIO tonnes (no extra BIO optimization)
        #   + pooling cost to compensate the Final_Balance_tCO2e
        cost_pooling_grid: List[float] = [
            bio_mass_total_t_base * prem + ets_cost_base + pooling_cost_component
            for prem in bio_premium_grid
        ]

        # Build the interactive graph
        fig_sim = go.Figure()

        fig_sim.add_trace(
            go.Scatter(
                x=bio_premium_grid,
                y=cost_opt_grid,
                mode="lines+markers",
                name="BIO optimization (FuelEU + ETS)",
                hovertemplate=(
                    "Premium = %{x:,.0f} €/t<br>"
                    "Total cost = %{y:,.0f} EUR<extra></extra>"
                ),
            )
        )

        fig_sim.add_trace(
            go.Scatter(
                x=bio_premium_grid,
                y=cost_pooling_grid,
                mode="lines",
                name=f"Pooling only @ {pooling_price_compare:,.0f} €/tCO₂e",
                line=dict(dash="dash"),
                hovertemplate=(
                    "Premium = %{x:,.0f} €/t<br>"
                    "Total cost = %{y:,.0f} EUR<extra></extra>"
                ),
            )
        )

        # Vertical reference line at the current BIO premium from the sidebar (if within range)
        if premium_min <= bio_premium_eur_per_t_val <= premium_max:
            fig_sim.add_vline(
                x=bio_premium_eur_per_t_val,
                line=dict(dash="dot"),
                annotation_text="Current BIO premium",
                annotation_position="top left",
            )

        fig_sim.update_layout(
            xaxis_title="Premium BIO vs selected fuel [EUR/ton]",
            yaxis_title="Total_Cost_FUEL_EU_ETS_Opt [EUR]",
            hovermode="x unified",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1.0,
            ),
            margin=dict(l=40, r=20, t=40, b=40),
        )

        st.plotly_chart(fig_sim, use_container_width=True)

# Existing download and footer remain below the graph
st.download_button(
    "fueleu_voyage_segments_2025_2050_eur.csv",
    data=df_cost.to_csv(index=False),
    file_name="fueleu_results_2025_2050_eur.csv",
    mime="text/csv",
)
st.info("Public demo — non-production. Results are informational; no warranty.", icon="ℹ️")
show_trial_footer("Nikitas Eleftheriou", "1.1", "2025-12-12")
st.caption("Built with Streamlit • Hosting on Streamlit Community Cloud. By using this app you also accept Streamlit’s Terms and Privacy.")
