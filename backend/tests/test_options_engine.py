import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.options import calculate_payoff


class OptionsEngineTest(unittest.TestCase):
    def test_bull_call_spread_summary(self):
        result = calculate_payoff(
            strategy='bull_call_spread',
            underlying_price=105,
            legs_payload=[
                {'option_type': 'CALL', 'side': 'LONG', 'strike': 100, 'premium': 5, 'quantity': 1},
                {'option_type': 'CALL', 'side': 'SHORT', 'strike': 110, 'premium': 2, 'quantity': 1},
            ],
        )

        self.assertEqual(result['summary']['max_profit'], 700.0)
        self.assertEqual(result['summary']['max_loss'], 300.0)
        self.assertEqual(result['summary']['breakeven_points'], [103.0])
        self.assertTrue(len(result['points']) > 10)


if __name__ == '__main__':
    unittest.main()
