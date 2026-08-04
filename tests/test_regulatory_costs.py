import unittest

from regulatory_costs import (
    ets_emissions_factor_tco2e_per_t,
    fueleu_penalty_eur,
    fueleu_signed_cost_eur,
    portfolio_segment_cost_values,
)


class FuelEUSignedCostTests(unittest.TestCase):
    def test_deficit_is_a_positive_cost(self) -> None:
        cost = fueleu_signed_cost_eur(125.0, 80.0, 2_400.0)

        self.assertGreater(cost, 0.0)
        self.assertAlmostEqual(cost, fueleu_penalty_eur(125.0, 80.0, 2_400.0))

    def test_surplus_is_an_equal_negative_benefit(self) -> None:
        deficit_cost = fueleu_signed_cost_eur(125.0, 80.0, 2_400.0)
        surplus_benefit = fueleu_signed_cost_eur(-125.0, 80.0, 2_400.0)

        self.assertLess(surplus_benefit, 0.0)
        self.assertAlmostEqual(surplus_benefit, -deficit_cost)

    def test_strategy_penalty_remains_zero_without_a_deficit(self) -> None:
        self.assertEqual(fueleu_penalty_eur(-125.0, 80.0, 2_400.0), 0.0)
        self.assertEqual(fueleu_penalty_eur(0.0, 80.0, 2_400.0), 0.0)

    def test_invalid_conversion_inputs_return_zero(self) -> None:
        self.assertEqual(fueleu_signed_cost_eur(-125.0, 0.0, 2_400.0), 0.0)
        self.assertEqual(fueleu_signed_cost_eur(-125.0, 80.0, 0.0), 0.0)

    def test_surplus_reduces_total_segment_regulatory_cost(self) -> None:
        costs = portfolio_segment_cost_values(
            compliance_balance_tco2e=125.0,
            attained_g=80.0,
            penalty_eur_vlsfo_t=2_400.0,
            ets_covered_tco2e=1_000.0,
            eua_price_eur_tco2e=90.0,
        )

        self.assertEqual(costs["signed_deficit_tco2e"], -125.0)
        self.assertLess(costs["fueleu_cost_eur"], 0.0)
        self.assertEqual(
            costs["total_regulatory_cost_eur"],
            costs["ets_cost_eur"] + costs["fueleu_cost_eur"],
        )
        self.assertLess(costs["total_regulatory_cost_eur"], costs["ets_cost_eur"])


class ETSCertifiedZeroRatingTests(unittest.TestCase):
    def test_checked_fuel_has_zero_ets_factor(self) -> None:
        factor = ets_emissions_factor_tco2e_per_t(
            co2_t_t=3.114,
            ch4_t_t=0.001,
            n2o_t_t=0.002,
            gwp_ch4=28.0,
            gwp_n2o=265.0,
            include_nonco2=True,
            ets_zero_if_certified=True,
        )

        self.assertEqual(factor, 0.0)

    def test_unchecked_fuel_uses_all_ets_factors_from_2026(self) -> None:
        factor = ets_emissions_factor_tco2e_per_t(
            co2_t_t=3.114,
            ch4_t_t=0.001,
            n2o_t_t=0.002,
            gwp_ch4=28.0,
            gwp_n2o=265.0,
            include_nonco2=True,
            ets_zero_if_certified=False,
        )

        self.assertAlmostEqual(factor, 3.114 + (0.001 * 28.0) + (0.002 * 265.0))

    def test_unchecked_fuel_uses_only_co2_before_2026(self) -> None:
        factor = ets_emissions_factor_tco2e_per_t(
            co2_t_t=3.114,
            ch4_t_t=0.001,
            n2o_t_t=0.002,
            gwp_ch4=28.0,
            gwp_n2o=265.0,
            include_nonco2=False,
            ets_zero_if_certified=False,
        )

        self.assertEqual(factor, 3.114)


if __name__ == "__main__":
    unittest.main()
