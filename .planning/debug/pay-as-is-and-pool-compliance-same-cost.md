---
status: awaiting_human_verify
trigger: "In the strategy selection/ranking, `Pay as-is` cost is always identical to `Pool compliance`, but pool compliance should reflect pool purchases and avoided FuelEU penalty and therefore differ whenever pooling is economically/operationally used."
created: 2026-08-04T17:46:31.5147196+03:00
updated: 2026-08-04T18:06:30+03:00
---

## Current Focus

hypothesis: The fix is self-verified and ready for confirmation in the user's real Streamlit workflow.
test: User selects Pay as-is and Pool compliance in Strategy detail and confirms the displayed NPV and pooling values change accordingly.
expecting: Pay as-is shows zero pool purchase and its penalty-bearing NPV; Pool compliance shows positive pool purchase/cost, zero covered residual deficit, and a different NPV.
next_action: Await real-workflow confirmation; parent agent will publish and archive if confirmed.

## Symptoms

expected: Pool compliance uses pool surplus to cover FuelEU deficit, records pool bought and Pool_Net_Cost_EUR, avoids the corresponding FuelEU penalty, and produces a different compliance/total cost from Pay as-is when there is a deficit and pool price differs from the penalty-equivalent cost.
actual: Pay as-is and Pool compliance always show the same cost in strategy selection.
errors: No exception; incorrect equal strategy results.
reproduction: Run the app with a FuelEU-deficit portfolio and compare Pay as-is versus Pool compliance in the strategy ranking/selection.
started: Reported after recent signed FuelEU and ETS checkbox changes; whether it pre-existed is unknown.

## Eliminated

- hypothesis: Pool compliance is not constructed with pooling enabled.
  evidence: build_strategy_set explicitly sets pool_deficit true and pool_cap_tco2e for Pool compliance, while Pay as-is sets pool_deficit false.
  timestamp: 2026-08-04T17:58:00+03:00

- hypothesis: The simulation or aggregation collapses the two baseline strategies to equal values.
  evidence: Direct execution on the default deficit portfolio produced Pool compliance Total_NPV_EUR 21.6865M with 3,695.17 tCO2e bought and zero penalty, versus Pay as-is Total_NPV_EUR 22.8768M with zero pool bought and 2.3242M FuelEU penalty.
  timestamp: 2026-08-04T17:58:10+03:00

## Evidence

- timestamp: 2026-08-04T17:47:30+03:00
  checked: Project debug knowledge base
  found: No .planning/debug/knowledge-base.md exists.
  implication: There is no known-pattern diagnosis to prioritize.

- timestamp: 2026-08-04T17:47:40+03:00
  checked: Repository state before implementation work
  found: git status showed only the newly created .planning debug directory as untracked; no pre-existing code changes were present.
  implication: Investigation starts from clean main, while preserving the required debug artifact.

- timestamp: 2026-08-04T17:47:50+03:00
  checked: Codebase search for strategy names and pooling cost fields
  found: app.py constructs Pay as-is and Pool compliance near lines 1122-1134, computes Pool_Net_Cost_EUR near line 1035, aggregates it near line 1236, and ranks/displays strategies later in the same file.
  implication: The suspected execution path is localized to app.py; tests currently appear concentrated in tests/test_regulatory_costs.py.

- timestamp: 2026-08-04T17:54:20+03:00
  checked: Complete app.py execution path
  found: build_strategy_set creates distinct Pay as-is (pool_deficit false) and Pool compliance (pool_deficit true) dictionaries. simulate_strategy buys up to the negative effective balance, reduces the deficit before calling fueleu_penalty_eur, records Pool_Bought_tCO2e and Pool_Net_Cost_EUR, and aggregates both costs without overwriting them.
  implication: Static inspection does not support missing pool flags, missing pool costing, or aggregation collapse; runtime evidence is required.

- timestamp: 2026-08-04T17:54:35+03:00
  checked: Complete regulatory_costs.py and tests/test_regulatory_costs.py
  found: The penalty helper correctly returns a positive cost for positive deficit and zero for non-positive deficit. Existing tests cover signed segment display costs and ETS certification but do not exercise simulate_strategy, build_strategy_set, or evaluate_strategies.
  implication: The reported strategy regression is currently unprotected and may depend on app-level input/state behavior.

- timestamp: 2026-08-04T17:57:35+03:00
  checked: Direct strategy execution on current main with the default FuelEU-deficit portfolio
  found: Pool compliance Total NPV was approximately EUR 21.6865M, bought 3,695.17 tCO2e, incurred approximately EUR 0.8607M pool cost, and avoided all FuelEU penalty. Pay as-is Total NPV was approximately EUR 22.8768M, bought zero pool, and incurred approximately EUR 2.3242M FuelEU penalty.
  implication: Pool economics are correct and distinct before rendering; equal displayed selection results cannot originate in the cost engine.

- timestamp: 2026-08-04T17:58:15+03:00
  checked: Decision Cockpit and Pooling Desk selection bindings
  found: selected_strategy filters selected_detail only. Metrics k1-k5 read best instead of the selected strategy row, and the Pooling Desk immediately assigns selected_strategy = comparison_df.iloc[0][Strategy], discarding the user's selector choice.
  implication: Selecting Pay as-is or Pool compliance shows the same best-strategy headline/pooling values, which exactly explains the reported UI symptom.

- timestamp: 2026-08-04T18:01:00+03:00
  checked: New Streamlit AppTest regression on current main
  found: Selecting Pay as-is fails to expose a Selected strategy NPV metric because the cockpit only renders the globally optimized NPV; the test errors before comparison, reproducing the disconnected selector contract.
  implication: The regression test fails before the fix for the expected reason and will directly verify selection-dependent cockpit and pooling values afterward.

- timestamp: 2026-08-04T18:05:00+03:00
  checked: Full unittest suite after the fix
  found: All 9 tests passed, including the AppTest that switches from Pay as-is to Pool compliance, verifies different selected-strategy NPVs, verifies zero pool need for Pay as-is, and verifies positive pool need for Pool compliance.
  implication: The original selector symptom is reproduced and corrected, while existing signed FuelEU and ETS behavior remains green.

- timestamp: 2026-08-04T18:05:10+03:00
  checked: Python compilation after the fix
  found: app.py, regulatory_costs.py, tests/test_regulatory_costs.py, and tests/test_strategy_selection_ui.py compiled successfully.
  implication: The implementation and regression test contain no Python syntax errors.

- timestamp: 2026-08-04T18:06:15+03:00
  checked: Final git diff and whitespace validation
  found: git diff --check passed. The only worktree changes are app.py, the new tests/test_strategy_selection_ui.py, and the required .planning debug session.
  implication: The patch is minimal, targeted, and contains no whitespace errors or unrelated edits.

## Resolution

root_cause: The UI selector was disconnected from the displayed cost metrics. Decision Cockpit summary metrics used the globally best row regardless of selected_strategy, and Pooling Desk overwrote selected_strategy with the best row, so both selector choices appeared to have the same cost even though the engine results differed.
fix: Derive selected_row from the Strategy detail selection; bind cockpit NPV, savings, deficit, and pool metrics to selected_row; and reuse selected_strategy in the Pooling Desk instead of overwriting it with the best-ranked strategy.
verification: AppTest passes after switching both baseline strategies and observes different selected NPVs plus correct zero/positive pooling need; all 9 unit tests pass; Python compilation passes.
files_changed: [app.py, tests/test_strategy_selection_ui.py]
