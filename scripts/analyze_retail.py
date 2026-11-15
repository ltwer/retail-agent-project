import argparse
from pathlib import Path
import sys

from retail_analysis import analyze_periods


def _money(value: float) -> str:
    return f"-£{abs(value):,.2f}" if value < 0 else f"£{value:,.2f}"


def _print_board(title: str, entries: list[dict], key: str) -> None:
    print(f"\n{title}")
    if not entries:
        print("  (无符合条件的项目)")
        return
    for entry in entries:
        label = entry[key]
        if key == "StockCode" and entry.get("Description"):
            label = f"{label} - {entry['Description']}"
        print(
            f"  {label}: 前期 {_money(entry['period1'])} | "
            f"后期 {_money(entry['period2'])} | 差额 {_money(entry['difference'])}"
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="比较 Online Retail 两个日期区间的销售表现。")
    parser.add_argument("csv_path", type=Path, help="交易明细 CSV 路径")
    parser.add_argument("period1_start", help="前期开始日期，含首日，格式 YYYY-MM-DD")
    parser.add_argument("period1_end", help="前期结束日期，含末日，格式 YYYY-MM-DD")
    parser.add_argument("period2_start", help="后期开始日期，含首日，格式 YYYY-MM-DD")
    parser.add_argument("period2_end", help="后期结束日期，含末日，格式 YYYY-MM-DD")
    args = parser.parse_args()

    result = analyze_periods(
        args.csv_path,
        args.period1_start,
        args.period1_end,
        args.period2_start,
        args.period2_end,
    )
    print(f"前期金额: {_money(result['period1_total'])}")
    print(f"后期金额: {_money(result['period2_total'])}")
    print(f"差额（后期 - 前期）: {_money(result['difference'])}")
    rate = result["change_rate"]
    print(f"变化率: {rate:.2%}" if rate is not None else "变化率: 不可计算（前期金额为零）")

    if result["fees"]:
        print("\n非商品费用（计入总金额，不纳入商品榜）")
        for code, fee in result["fees"].items():
            print(
                f"  {code} - {fee['description']}: 前期 {_money(fee['period1'])} | "
                f"后期 {_money(fee['period2'])} | 差额 {_money(fee['difference'])}"
            )

    _print_board("国家金额下降最多 5 项", result["country_decreases"], "Country")
    _print_board("国家金额增长最多 5 项", result["country_increases"], "Country")
    _print_board("商品金额下降最多 5 项", result["product_decreases"], "StockCode")
    _print_board("商品金额增长最多 5 项", result["product_increases"], "StockCode")


if __name__ == "__main__":
    main()
