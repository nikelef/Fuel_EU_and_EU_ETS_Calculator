VLSFO_REFERENCE_LCV_MJ_T = 41_000.0


def ets_emissions_factor_tco2e_per_t(
    co2_t_t: float,
    ch4_t_t: float,
    n2o_t_t: float,
    gwp_ch4: float,
    gwp_n2o: float,
    include_nonco2: bool,
    ets_zero_if_certified: bool,
) -> float:
    """Return the ETS CO2e factor, applying certified zero-rating when selected."""
    if ets_zero_if_certified:
        return 0.0
    ch4_co2e = ch4_t_t * gwp_ch4 if include_nonco2 else 0.0
    n2o_co2e = n2o_t_t * gwp_n2o if include_nonco2 else 0.0
    return co2_t_t + ch4_co2e + n2o_co2e


def fueleu_penalty_eur(
    deficit_tco2e: float,
    attained_g: float,
    penalty_eur_vlsfo_t: float,
) -> float:
    """Return the FuelEU penalty for a non-negative compliance deficit."""
    if deficit_tco2e <= 0 or attained_g <= 0 or penalty_eur_vlsfo_t <= 0:
        return 0.0
    tco2e_per_vlsfo_t = attained_g * VLSFO_REFERENCE_LCV_MJ_T / 1_000_000.0
    if tco2e_per_vlsfo_t <= 0:
        return 0.0
    return (deficit_tco2e / tco2e_per_vlsfo_t) * penalty_eur_vlsfo_t


def fueleu_signed_cost_eur(
    signed_deficit_tco2e: float,
    attained_g: float,
    penalty_eur_vlsfo_t: float,
) -> float:
    """Value a deficit positively and a surplus negatively at the penalty rate."""
    magnitude = fueleu_penalty_eur(
        abs(signed_deficit_tco2e),
        attained_g,
        penalty_eur_vlsfo_t,
    )
    return -magnitude if signed_deficit_tco2e < 0 else magnitude


def portfolio_segment_cost_values(
    compliance_balance_tco2e: float,
    attained_g: float,
    penalty_eur_vlsfo_t: float,
    ets_covered_tco2e: float,
    eua_price_eur_tco2e: float,
) -> dict[str, float]:
    """Return signed FuelEU and EU ETS values for a portfolio result row."""
    signed_deficit = -compliance_balance_tco2e
    fueleu_cost = fueleu_signed_cost_eur(
        signed_deficit,
        attained_g,
        penalty_eur_vlsfo_t,
    )
    ets_cost = ets_covered_tco2e * eua_price_eur_tco2e
    return {
        "signed_deficit_tco2e": signed_deficit,
        "fueleu_cost_eur": fueleu_cost,
        "ets_cost_eur": ets_cost,
        "total_regulatory_cost_eur": fueleu_cost + ets_cost,
    }
