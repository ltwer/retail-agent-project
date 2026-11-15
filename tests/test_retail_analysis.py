import tempfile
import unittest
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from retail_analysis import analyze_periods


class RetailAnalysisTests(unittest.TestCase):
    def test_summarizes_periods_and_excludes_fee_codes_only_from_product_ranking(self):
        rows = [
            ["1", "2011-09-19 10:00:00", "A", "Item A", 2, 10, "UK"],
            ["2", "2011-09-25 23:59:00", "POST", "POSTAGE", 1, 3, "UK"],
            ["3", "2011-09-26 00:00:00", "A", "Item A", 1, 10, "UK"],
            ["4", "2011-10-02 23:59:00", "B", "Item B", 1, 8, "France"],
            ["C5", "2011-09-20 12:00:00", "A", "cancelled", 99, 99, "UK"],
            ["6", "2011-09-21 12:00:00", "A", "zero quantity", 0, 99, "UK"],
            ["7", "2011-09-22 12:00:00", "A", "zero price", 99, 0, "UK"],
            ["8", "2011-10-03 00:00:00", "B", "outside", 1, 99, "France"],
        ]
        columns = [
            "InvoiceNo", "InvoiceDate", "StockCode", "Description",
            "Quantity", "UnitPrice", "Country",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "retail.csv"
            pd.DataFrame(rows, columns=columns).to_csv(csv_path, index=False)

            result = analyze_periods(
                csv_path,
                "2011-09-19", "2011-09-25",
                "2011-09-26", "2011-10-02",
            )

        self.assertEqual(result["period1_total"], 23.0)
        self.assertEqual(result["period2_total"], 18.0)
        self.assertEqual(result["difference"], -5.0)
        self.assertEqual(result["change_rate"], -5 / 23)
        self.assertEqual(result["fees"]["POST"]["period1"], 3.0)
        self.assertEqual(result["product_decreases"][0]["StockCode"], "A")
        self.assertEqual(result["product_decreases"][0]["difference"], -10.0)

    def test_zero_previous_total_has_undefined_change_rate(self):
        rows = [["1", "2011-09-26", "A", "Item A", 1, 5, "UK"]]
        columns = [
            "InvoiceNo", "InvoiceDate", "StockCode", "Description",
            "Quantity", "UnitPrice", "Country",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "retail.csv"
            pd.DataFrame(rows, columns=columns).to_csv(csv_path, index=False)
            result = analyze_periods(
                csv_path,
                "2011-09-19", "2011-09-25",
                "2011-09-26", "2011-10-02",
            )

        self.assertIsNone(result["change_rate"])

    def test_acceptance_case_matches_expected_totals_and_leading_decreases(self):
        project_root = Path(__file__).resolve().parents[1]
        result = analyze_periods(
            project_root / "data" / "retail_demo.csv",
            "2011-09-19", "2011-09-25",
            "2011-09-26", "2011-10-02",
        )

        self.assertEqual(round(result["period1_total"], 2), 338771.79)
        self.assertEqual(round(result["period2_total"], 2), 211800.22)
        self.assertEqual(round(result["difference"], 2), -126971.57)
        self.assertEqual(result["country_decreases"][0]["Country"], "United Kingdom")
        self.assertEqual(round(result["country_decreases"][0]["difference"], 2), -87415.11)
        self.assertEqual(result["product_decreases"][0]["StockCode"], "23243")
        self.assertEqual(round(result["product_decreases"][0]["difference"], 2), -8311.28)


if __name__ == "__main__":
    unittest.main()
