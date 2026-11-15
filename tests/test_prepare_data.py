import csv
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "prepare_data.py"
EXPECTED_COLUMNS = [
    "InvoiceNo",
    "InvoiceDate",
    "StockCode",
    "Description",
    "Quantity",
    "UnitPrice",
    "Country",
]


class PrepareDataTests(unittest.TestCase):
    def test_exports_inclusive_date_window_without_filtering_transaction_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.xlsx"
            output = Path(temp_dir) / "output.csv"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(EXPECTED_COLUMNS)
            sheet.append(["C1", datetime(2011, 9, 11, 23, 59), "A", "before", 1, 1, "UK"])
            sheet.append(["C2", datetime(2011, 9, 12), "B", "cancel", -2, 0, "UK"])
            sheet.append(["3", datetime(2011, 10, 2, 23, 59), "C", "last day", 1, -1, "France"])
            sheet.append(["4", datetime(2011, 10, 3), "D", "after", 1, 1, "UK"])
            workbook.save(source)

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(source), str(output)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with output.open(encoding="utf-8", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                rows = list(reader)

            self.assertEqual(reader.fieldnames, EXPECTED_COLUMNS)
            self.assertEqual([row["InvoiceNo"] for row in rows], ["C2", "3"])
            self.assertEqual([row["Quantity"] for row in rows], ["-2", "1"])
            self.assertEqual([row["UnitPrice"] for row in rows], ["0", "-1"])
            self.assertTrue(rows[0]["InvoiceDate"].startswith("2011-09-12"))
            self.assertTrue(rows[1]["InvoiceDate"].startswith("2011-10-02"))


if __name__ == "__main__":
    unittest.main()
