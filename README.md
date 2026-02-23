# FuelEU Maritime — Voyage Segments + EU ETS
**Streamlit planning tool (2025–2050)** for FuelEU Maritime GHG-intensity compliance modeling, EU ETS (maritime) cost estimation, and **multi-fuel optimization** (fossil replacement with 1–3 alternative fuels).

> **Disclaimer:** This is a planning / decision-support tool. Final compliance must follow the binding legal texts, implementing acts, and your company-approved methodology. Outputs may contain errors. No warranty.

---

## Highlights (v3.0 — 2026-02-14)
- **Segments editor** based on `st.data_editor` (fast, scalable, add/remove rows cleanly).
- **Fuel library** expanded (BIO, RFNBO + 2 custom fuels by default) and **editable** (LCV, WtW, ETS factors, prices).
- **Multi-fuel optimizer**: replace one fossil fuel with **1–3 alternative fuels** energy-equivalently (BIO / RFNBO / CUSTOM_A / CUSTOM_B, etc.).
- **Time-consistent banking/carry** per scenario (avoids inconsistent carry-in reuse).
- **EU ETS**:
  - Geographic scope: 100% (Intra-EU & EU at-berth), 50% (cross-border EU↔non-EU).
  - Coverage factor: **2025 = 70%**, **2026+ = 100%** (as implemented in this model).
  - Emissions in **tCO₂e from 2026+** (CO₂ + CH₄ + N₂O using user-set GWP100).
  - **BIO blend handling preserved**: user selects “pure BIO %” and assigns fossil share to HSFO/LFO/MGO.
- **Scenario manager** (save/load/export/import/delete) + **input snapshot export** (JSON).
- **Policy comparison** (Base vs Optimizer) with clearer charts, 5-year window selector, and CSV downloads.

---

## Repository structure
This repo is intentionally minimal:
