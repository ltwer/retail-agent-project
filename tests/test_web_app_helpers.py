import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from web_app_helpers import CsvValidationError, validate_csv


class WebAppHelperTests(unittest.TestCase):
    def test_validates_required_columns_and_returns_local_date_range(self):
        project_root = Path(__file__).resolve().parents[1]
        metadata = validate_csv(project_root / "data" / "retail_demo.csv")

        self.assertEqual(metadata.min_date.isoformat(), "2011-09-12")
        self.assertEqual(metadata.max_date.isoformat(), "2011-10-02")

    def test_rejects_csv_missing_workflow_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.csv"
            pd.DataFrame({"InvoiceDate": ["2011-09-12"]}).to_csv(path, index=False)

            with self.assertRaisesRegex(CsvValidationError, "InvoiceNo"):
                validate_csv(path)

    def test_uploaded_copy_uses_the_same_csv_contract_and_metadata(self):
        project_root = Path(__file__).resolve().parents[1]
        source = project_root / "data" / "retail_demo.csv"
        with tempfile.TemporaryDirectory() as temp_dir:
            uploaded = Path(temp_dir) / "uploaded.csv"
            uploaded.write_bytes(source.read_bytes())

            self.assertEqual(validate_csv(uploaded), validate_csv(source))


if __name__ == "__main__":
    unittest.main()
