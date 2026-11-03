import tempfile
import unittest
from pathlib import Path

from analytics import (
    closing_line_value,
    expected_value,
    quarter_kelly_stake,
    wilson_interval,
)
from main import load_board


class AnalyticsFunctionTests(unittest.TestCase):
    def test_wilson_interval_known_value_and_zero_wins_edge(self):
        interval = wilson_interval(50, 100)
        self.assertIsNotNone(interval)
        self.assertAlmostEqual(interval[0], 0.4038315304, places=8)
        self.assertAlmostEqual(interval[1], 0.5961684696, places=8)

        zero_wins = wilson_interval(0, 100)
        self.assertIsNotNone(zero_wins)
        self.assertEqual(zero_wins[0], 0.0)
        self.assertAlmostEqual(zero_wins[1], 0.0369935, places=7)
        self.assertIsNone(wilson_interval(0, 0))

    def test_expected_value(self):
        self.assertAlmostEqual(expected_value(0.6, 2.0), 0.2)
        self.assertAlmostEqual(expected_value(0.4, 2.0), -0.2)

    def test_quarter_kelly_positive_and_negative_floor(self):
        self.assertAlmostEqual(quarter_kelly_stake(0.6, 2.0, 1000.0), 50.0)
        self.assertEqual(quarter_kelly_stake(0.4, 2.0, 1000.0), 0.0)

    def test_quarter_kelly_rejects_decimal_odds_at_or_below_one(self):
        self.assertIsNone(quarter_kelly_stake(0.6, 1.0, 1000.0))
        self.assertIsNone(quarter_kelly_stake(0.6, 0.99, 1000.0))

    def test_closing_line_value(self):
        self.assertAlmostEqual(closing_line_value(2.1, 2.0), 0.05)
        self.assertIsNone(closing_line_value(1.0, 2.0))

    def test_load_board_warns_when_optional_columns_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "board.csv"
            csv_path.write_text(
                "date,market,selection,result\n"
                "2025-02-01,Match Result,Arsenal,W\n",
                encoding="utf-8",
            )
            board = load_board(csv_path)

        self.assertEqual(len(board.rows), 1)
        for column in ("quoted_odds", "closing_odds", "risk_label"):
            self.assertTrue(
                any(f"Missing column '{column}'" in warning for warning in board.warnings),
                f"Expected a warning for missing optional column {column!r}",
            )


if __name__ == "__main__":
    unittest.main()
