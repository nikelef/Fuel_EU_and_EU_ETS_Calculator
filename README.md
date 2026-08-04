# Maritime Carbon Cost Optimizer

Subscription-ready Streamlit app for minimizing a shipowner's combined **FuelEU Maritime** and **EU ETS** exposure. The app compares fuel switching, pooling, banking, OPS berth electrification, and paying the residual regulatory cost across a 2025-2050 horizon.

> Planning model only. Final compliance must follow the binding legal texts, verified monitoring plans, verifier instructions, and company-approved methodology.

## What Changed In v4.0

- Rebuilt the UI as a decision cockpit rather than a simple calculator.
- Added a deterministic strategy engine that ranks:
  - pay as-is;
  - buy FuelEU pool surplus;
  - switch fossil fuel to biofuel or RFNBO blends;
  - combine fuel switching with residual pooling;
  - add OPS berth electrification where relevant.
- Added portfolio-level voyage inputs with per-trip fuel quantities and annual trip scaling.
- Added editable fuel library with LCV, WtW intensity, ETS CO2/CH4/N2O factors, price, RFNBO flag, and supply caps.
- Added **HSFO** as a first-class fuel option and changed the sample vessel profile to burn HSFO on the main legs.
- Expanded the default alternative-fuel library with HVO, FAME, UCOME, bio-methanol, advanced bio-oil, LBM, e-methanol, e-ammonia, and RFNBO hydrogen.
- Added sidebar help tooltips for the economic and optimizer inputs.
- Exposed fuel prices in the sidebar; these prices feed the `Total cost including fuel` objective.
- Added Portfolio-tab current FuelEU/EU ETS cost graphics by segment across the selected period.
- Portfolio segment results retain the FuelEU compliance sign: deficits/costs are positive, while surpluses and their penalty-equivalent benefit are negative and reduce net regulatory cost.
- Added Portfolio-tab total yearly cost chart with regulatory cost, fuel cost, pooling cost, and total cost.
- Added Portfolio-tab GFI line chart showing attained GFI against the FuelEU limit across the selected period.
- Set the default selected period to 2025-2030; all charts/tables use that period except the GFI chart, which remains fixed at 2025-2050.
- Reworded strategy labels so fuel-switch options read as explicit replacements, for example `Replace 15% of HSFO with 100% FAME`.
- Added graphs for strategy ranking, annual cost stack, FuelEU intensity pathway, energy mix, pooling need, and regulatory timeline.
- Added a subscription/export tab with scenario save/load and decision-case JSON export.

## Included Regulatory Scope

Regulatory baseline checked on **2026-05-21** against official European Commission, EUR-Lex, and EMSA material.

Implemented in the cost engine:

- FuelEU Maritime Regulation (EU) 2023/1805 GHG-intensity pathway from the 91.16 gCO2e/MJ reference:
  - 2025: 2%
  - 2030: 6%
  - 2035: 14.5%
  - 2040: 31%
  - 2045: 62%
  - 2050: 80%
- FuelEU WtW fuel intensity and scoped energy by route:
  - 100% intra-EU/EEA and EU port stay;
  - 50% EU/non-EU cross-border voyages;
  - optional low-carbon-first allocation for FuelEU cross-border energy.
- FuelEU RFNBO reward factor modeled as 2 through 2033 and 1 from 2034.
- FuelEU pooling economics as a private surplus transaction.
- FuelEU banking as carry-forward of positive compliance balance.
- EU ETS maritime scope:
  - 100% intra-EU and EU port emissions;
  - 50% EU/non-EU voyage emissions.
- EU ETS phase-in in this 2025-2050 model:
  - 2025 emissions: 70%;
  - 2026 onward: 100%.
- EU ETS gases:
  - CO2 only for 2025 in this app;
  - CO2 + CH4 + N2O from 2026 onward, using editable GWP100 assumptions.
  - the `ETS zero if certified` fuel checkbox zero-rates that fuel's ETS CO2e factor when valid certification evidence applies.
- OPS as an operational fuel-replacement strategy for berth energy.
- HSFO economics as a normal fuel option in the editable fuel library.
- Fuel prices by fuel type. Fuel cost is calculated from annual tonnes consumed, editable `Price_EUR_t`, and the annual fuel price escalation assumption.

Depicted but not fully monetized:

- FuelEU borrowing.
- FuelEU RFNBO subtarget penalties.
- FuelEU OPS port-call penalties and exemptions.
- Verifier workflows, FuelEU database submission, monitoring-plan approvals, and company-level ETS administration.
- RED/RFNBO sustainability evidence validation.
- Norway/Iceland FuelEU EEA timing delays, neighboring container-transhipment port rules, ship/voyage exemptions, and derogations.

Official sources linked inside the app:

- European Commission FuelEU Maritime
- European Commission FuelEU Q&A
- Regulation (EU) 2023/1805
- Commission Implementing Regulation (EU) 2026/394 on the FuelEU database
- European Commission EU ETS maritime
- EMSA ETS FAQ
- Regulation (EU) 2023/957 on MRV updates
- Alternative Fuels Infrastructure material

## Run Locally

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL printed by Streamlit, usually `http://localhost:8501`.

## Commercial Deployment Notes

The app is subscription-ready, not payment-provider-complete. For sale to third parties:

- Deploy behind SSO or managed Streamlit authentication.
- Store tenant scenarios in a database instead of the local `.carbon_optimizer_scenarios.json` file.
- Connect Stripe, Paddle, or enterprise invoicing for subscription status.
- Move emissions factors and fuel prices into a governed data-maintenance workflow.
- Add verifier-approved exports if the product will be used beyond decision support.

## Repository Structure

```text
app.py              Main Streamlit app and strategy engine
requirements.txt    Python dependencies
runtime.txt         Python runtime for hosted environments
README.md           This guide
```

## Disclaimer

This app is an analytical planning tool. It is not legal, financial, or verifier advice. Fuel factors, ETS factors, RFNBO/biofuel certification status, and voyage eligibility must be verified for the specific vessel, monitoring plan, reporting year, and administering authority.
