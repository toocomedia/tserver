"""
tools/ai_plan_tester/cli.py — Command-line interface for the AI Plan & Spec Dev Tester.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List

from database import AsyncSessionLocal
from tools.ai_plan_tester.catalog import (
    BenchmarkApp,
    find_app_by_slug,
    get_catalog,
    resolve_app_target,
)
from tools.ai_plan_tester.reporter import (
    format_app_report_text,
    format_scorecard_table,
    save_app_output_files,
)
from tools.ai_plan_tester.runner import RunResult, run_ai_test


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Plan & Spec Dev Tester (100% Safe Dry-Run Plan & YAML Generator)"
    )
    parser.add_argument(
        "--all", action="store_true", help="Test all benchmark applications in catalog"
    )
    parser.add_argument(
        "--app", type=str, help="Test specific benchmark app by slug or name (e.g. shynet, umami, ghost)"
    )
    parser.add_argument(
        "--url", type=str, help="Test an ad-hoc custom Git repository URL"
    )
    parser.add_argument(
        "--image", type=str, help="Test an ad-hoc custom Docker image reference"
    )
    parser.add_argument(
        "--tier", type=int, choices=[1, 2, 3, 4], help="Filter catalog by tier (1: Single, 2: DB, 3: Compose, 4: Git)"
    )
    parser.add_argument(
        "--offline", action="store_true", help="Run in fast offline simulation mode (no LLM tokens spent)"
    )
    parser.add_argument(
        "--output", type=str, default="reports/output", help="Directory to save generated YAML, JSON, and reports"
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    targets: List[BenchmarkApp] = []

    if args.url:
        targets.append(resolve_app_target(args.url))
    elif args.image:
        targets.append(resolve_app_target(args.image))
    elif args.app:
        matched = find_app_by_slug(args.app)
        if not matched:
            targets.append(resolve_app_target(args.app))
        else:
            targets.append(matched)
    elif args.tier:
        targets.extend(get_catalog(tier=args.tier))
    elif args.all:
        targets.extend(get_catalog())
    else:
        # Default: pick representative apps from each tier
        targets = [
            find_app_by_slug("vaultwarden") or get_catalog()[0],
            find_app_by_slug("umami") or get_catalog()[1],
            find_app_by_slug("shynet") or get_catalog()[2],
        ]

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 80)
    print("  AI PLAN & SPEC DEV TESTER (100% DRY-RUN — ZERO DEPLOYMENTS)")
    print(f"  Targets to test: {len(targets)} | Mode: {'OFFLINE SIMULATION' if args.offline else 'ACTIVE PANEL AI'}")
    print(f"  Output directory: {output_dir.resolve()}")
    print("=" * 80 + "\n")

    results: List[RunResult] = []
    async with AsyncSessionLocal() as db:
        for idx, app in enumerate(targets, start=1):
            print(f"[{idx}/{len(targets)}] Testing: {app.name} ({app.target})...", end="", flush=True)
            res = await run_ai_test(app, db, offline=args.offline)
            results.append(res)
            saved = save_app_output_files(res, output_dir)
            status = res.validation.status if res.validation else "FAIL"
            print(f" [{status}] ({res.duration_ms}ms)")

    # Print summary scorecard
    scorecard = format_scorecard_table(results)
    print("\n" + scorecard + "\n")

    # Write summary scorecard file
    summary_path = output_dir / "scorecard_summary.txt"
    summary_path.write_text(scorecard, encoding="utf-8")
    print(f"Saved summary scorecard to: {summary_path}")

    # If single target was tested, print the report directly
    if len(results) == 1:
        print("\n" + format_app_report_text(results[0]))

    # Exit code: 0 if all PASS, 1 if any FAIL
    has_failure = any((not r.validation or r.validation.status != "PASS") for r in results)
    return 1 if has_failure else 0


def main() -> None:
    code = asyncio.run(main_async())
    sys.exit(code)


if __name__ == "__main__":
    main()
