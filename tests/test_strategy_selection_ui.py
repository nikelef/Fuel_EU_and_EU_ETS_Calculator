import unittest

from streamlit.testing.v1 import AppTest


class StrategySelectionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = AppTest.from_file("app.py", default_timeout=120)
        cls.app.session_state["_authenticated"] = True
        cls.app.session_state["subscriber_name"] = "test"
        cls.app.session_state["subscription_plan"] = "test"
        cls.app.run(timeout=120)

    def _select_strategy(self, name: str) -> None:
        selector = next(widget for widget in self.app.selectbox if widget.label == "Strategy detail")
        selector.select(name)
        self.app.run(timeout=120)

    def _metric_value(self, label: str) -> str:
        metric = next(item for item in self.app.metric if item.label == label)
        return metric.value

    def test_selected_strategy_controls_cockpit_and_pooling_values(self) -> None:
        self._select_strategy("Pay as-is")
        pay_as_is_npv = self._metric_value("Selected strategy NPV")
        pay_as_is_pool_need = self._metric_value("Pool buy need")

        self._select_strategy("Pool compliance")
        pool_compliance_npv = self._metric_value("Selected strategy NPV")
        pool_compliance_pool_need = self._metric_value("Pool buy need")

        self.assertNotEqual(pay_as_is_npv, pool_compliance_npv)
        self.assertEqual(pay_as_is_pool_need, "0 tCO2e")
        self.assertNotEqual(pool_compliance_pool_need, "0 tCO2e")


if __name__ == "__main__":
    unittest.main()
