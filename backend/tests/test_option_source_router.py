import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.source_router import option_candidate_sources, resolve_option_source


class OptionSourceRouterTest(unittest.TestCase):
    def test_hk_stays_futu(self):
        self.assertEqual(resolve_option_source("00700.HK", options_source="auto"), "futu")
        self.assertEqual(option_candidate_sources("00700.HK", "futu"), ["futu"])

    def test_us_auto_opend_down_uses_cboe(self):
        with patch("app.data.source_router.is_opend_reachable", return_value=False):
            self.assertEqual(resolve_option_source("AAPL", options_source="auto"), "cboe")
            self.assertEqual(option_candidate_sources("AAPL", "cboe"), ["cboe"])

    def test_us_auto_opend_up_prefers_futu_then_cboe(self):
        with patch("app.data.source_router.is_opend_reachable", return_value=True):
            self.assertEqual(resolve_option_source("TSLA", options_source="auto"), "futu")
            self.assertEqual(option_candidate_sources("TSLA", "futu"), ["futu", "cboe"])

    def test_force_cboe_skips_opend(self):
        with patch("app.data.source_router.is_opend_reachable", return_value=True):
            self.assertEqual(resolve_option_source("AAPL", options_source="cboe"), "cboe")
            self.assertEqual(option_candidate_sources("AAPL", "cboe"), ["cboe"])

    def test_force_futu_still_falls_back_on_us(self):
        self.assertEqual(resolve_option_source("AAPL", options_source="futu"), "futu")
        self.assertEqual(option_candidate_sources("AAPL", "futu"), ["futu", "cboe"])

    def test_load_expirations_uses_cboe_when_opend_down(self):
        from app.api import options as opt

        def boom(*_a, **_k):
            raise AssertionError("futu should not run")

        with patch("app.data.source_router.is_opend_reachable", return_value=False), \
             patch.object(opt, "_futu_load_option_expirations", side_effect=boom), \
             patch("app.data.cboe_options.load_expirations", return_value=["2026-09-11", "2026-09-18"]):
            exps, src = opt._load_option_expirations_src("AAPL", "127.0.0.1", 11111)
            self.assertEqual(src, "cboe")
            self.assertEqual(exps[0], "2026-09-11")


if __name__ == "__main__":
    unittest.main()
