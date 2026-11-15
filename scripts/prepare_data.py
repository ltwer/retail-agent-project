import argparse
import csv
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook


COLUMNS = (
    "InvoiceNo",
    "InvoiceDate",
    "StockCode",
    "Description",
    "Quantity",
    "UnitPrice",
    "Country",
)
START_DATE = date(2011, 9, 12)
END_DATE = date(2011, 10, 2)


def prepare_data(source_path: Path, output_path: Path) -> int:
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        headers = next(rows, None)
        if headers is None:
            raise ValueError("The Excel worksheet is empty.")

        header_positions = {name: index for index, name in enumerate(headers)}
        missing = [name for name in COLUMNS if name not in header_positions]
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(COLUMNS)
            for row in rows:
                invoice_date = row[header_positions["InvoiceDate"]]
                if not isinstance(invoice_date, (date, datetime)):
                    continue
                transaction_date = invoice_date.date() if isinstance(invoice_date, datetime) else invoice_date
                if not START_DATE <= transaction_date <= END_DATE:
                    continue

                values = [row[header_positions[name]] for name in COLUMNS]
                if isinstance(invoice_date, datetime):
                    values[1] = invoice_date.isoformat(sep=" ")
                else:
                    values[1] = invoice_date.isoformat()
                writer.writerow(values)
                count += 1
        return count
    finally:
        workbook.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Online Retail transactions from 2011-09-12 through 2011-10-02."
    )
    parser.add_argument("excel_path", type=Path, help="Path to the source .xlsx file")
    parser.add_argument("csv_path", type=Path, help="Path for the output UTF-8 CSV file")
    args = parser.parse_args()

    count = prepare_data(args.excel_path, args.csv_path)
    print(f"Wrote {count} data rows to {args.csv_path}")


if __name__ == "__main__":
    main()
