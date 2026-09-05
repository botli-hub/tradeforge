import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.cboe_options import (
    chain_from_payload,
    expirations_from_payload,
    occ_to_code,
    parse_occ,
    row_to_contract,
)


SAMPLE = {
    "timestamp": "2026-09-05T13:00:00Z",
    "data": {
        "symbol": "AAPL",
        "current_price": 320.01,
        "options": [
            {
                "option": "AAPL260911P00320000",
                "bid": 4.6,
                "ask": 4.8,
                "last_trade_price": 4.69,
                "iv": 0.2663,
                "delta": -0.4956,
                "gamma": 0.034,
                "theta": -0.3524,
                "vega": 0.1767,
                "open_interest": 1939,
                "volume": 3945,
                "theo": 4.7242,
            },
            {
                "option": "AAPL260911C00320000",
                "bid": 4.4,
                "ask": 4.6,
                "last_trade_price": 4.5,
                "iv": 0.25,
                "delta": 0.51,
                "gamma": 0.033,
                "theta": -0.35,
                "vega": 0.17,
                "open_interest": 800,
                "volume": 120,
                "theo": 4.5,
            },
            {
                "option": "AAPL260904P00320000",  # past expiry vs today=2026-09-05
                "bid": 0.16,
                "ask": 0.22,
                "last_trade_price": 0.24,
                "iv": 0.18,
                "delta": -0.56,
                "gamma": 0.3,
                "theta": -0.3,
                "vega": 0.01,
                "open_interest": 10,
                "volume": 1,
            },
        ],
    },
}


class CboeOptionsTest(unittest.TestCase):
    def test_parse_occ(self):
        root, exp, side, strike = parse_occ("AAPL260911P00320000")
        self.assertEqual(root, "AAPL")
        self.assertEqual(exp, "2026-09-11")
        self.assertEqual(side, "PUT")
        self.assertEqual(strike, 320.0)
        self.assertEqual(occ_to_code("AAPL260911P00320000"), "US.AAPL260911P00320000")
        self.assertEqual(occ_to_code("US.AAPL260911C00225000"), "US.AAPL260911C00225000")

    def test_expirations_drop_past(self):
        exps = expirations_from_payload(SAMPLE, today=date(2026, 9, 5))
        self.assertEqual(exps, ["2026-09-11"])

    def test_chain_maps_quotes_and_greeks(self):
        chain = chain_from_payload(SAMPLE, "2026-09-11", "AAPL", today=date(2026, 9, 5))
        self.assertEqual(chain["adapter"], "cboe")
        self.assertTrue(chain["delayed"])
        self.assertEqual(chain["spot_price"], 320.01)
        self.assertEqual(chain["days_to_expiry"], 6)
        self.assertEqual(len(chain["contracts"]), 2)
        put = next(c for c in chain["contracts"] if c["option_type"] == "PUT")
        self.assertEqual(put["option_symbol"], "US.AAPL260911P00320000")
        self.assertEqual(put["strike"], 320.0)
        self.assertEqual(put["bid"], 4.6)
        self.assertEqual(put["ask"], 4.8)
        self.assertEqual(put["last"], 4.69)
        self.assertAlmostEqual(put["iv"], 0.2663, places=4)
        self.assertAlmostEqual(put["delta"], -0.4956, places=4)
        self.assertEqual(put["delta_source"], "cboe")
        self.assertEqual(put["open_interest"], 1939)

    def test_mid_when_last_missing(self):
        row = {
            "option": "AAPL260918P00300000",
            "bid": 2.0,
            "ask": 2.4,
            "last_trade_price": 0,
            "iv": 0.3,
            "delta": -0.3,
            "gamma": 0.02,
            "theta": -0.1,
            "vega": 0.1,
            "open_interest": 1,
            "volume": 0,
        }
        c = row_to_contract(row, underlying="AAPL", spot=320, today=date(2026, 9, 5))
        self.assertIsNotNone(c)
        self.assertEqual(c["last"], 2.2)


if __name__ == "__main__":
    unittest.main()
