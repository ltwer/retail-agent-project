from pathlib import Path

import pandas as pd

from planning_agent import load_csv_metadata
from retail_analysis import REQUIRED_COLUMNS


class CsvValidationError(ValueError):
    pass


def validate_csv(csv_path: str | Path):
    """Validate the local CSV contract before starting a model-backed run."""
    path = Path(csv_path)
    if not path.is_file():
        raise CsvValidationError(f"找不到 CSV 文件：{path}")

    try:
        columns = set(pd.read_csv(path, nrows=0).columns)
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise CsvValidationError("无法读取 CSV 表头，请确认文件编码和格式。") from exc

    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise CsvValidationError("CSV 缺少工作流所需字段：" + ", ".join(missing))

    try:
        return load_csv_metadata(path)
    except (OSError, ValueError, KeyError, pd.errors.ParserError) as exc:
        raise CsvValidationError(f"无法从 InvoiceDate 读取可用日期范围：{exc}") from exc
