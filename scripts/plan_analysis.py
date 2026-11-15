import argparse
import json
import sys
from pathlib import Path

from planning_agent import (
    ModelConfigurationError,
    PlannerRequestError,
    plan_question,
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="将中文零售分析问题转换为结构化分析计划。")
    parser.add_argument("csv_path", type=Path, help="本地零售交易 CSV 路径")
    parser.add_argument("question", nargs="+", help="中文分析问题")
    args = parser.parse_args()

    try:
        plan = plan_question(" ".join(args.question), args.csv_path)
    except ModelConfigurationError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except (PlannerRequestError, ValueError) as exc:
        print(f"规划失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
