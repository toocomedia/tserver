"""
tools/ai_plan_tester/reporter.py — Format reports, scorecards, and exported YAML/JSON files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from tools.ai_plan_tester.runner import RunResult


def format_app_report_text(result: RunResult) -> str:
    """Generates comprehensive human-readable text report for a single app run."""
    val = result.validation
    status_str = val.status if val else ("ERROR" if result.error else "UNKNOWN")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "=" * 80,
        f"AI PLAN & SPEC TEST REPORT: {result.app.name.upper()}",
        f"Generated: {now_str}",
        f"Active AI Provider: {result.provider_name} ({result.model_name})",
        f"Target: {result.app.target} (Source: {result.app.source_type})",
        f"Status: [{status_str}] (Total Duration: {result.duration_ms}ms)",
        "=" * 80,
        "",
        "[1. AI SEARCH & TOOL EXECUTION TRACE]",
        "-" * 80,
    ]

    if result.activities:
        for idx, act in enumerate(result.activities, start=1):
            lines.append(f"  {idx}. Tool: {act.tool} -> {act.status.upper()} (+{act.timestamp_ms}ms)")
            if act.label:
                lines.append(f"     Label: {act.label}")
            if act.detail:
                lines.append(f"     Detail: {act.detail}")
    else:
        lines.append("  (No intermediate tool events recorded)")

    if result.turn1_response:
        lines.extend([
            "",
            "[Turn 1 Response Snippet]:",
            "  " + result.turn1_response.strip().replace("\n", "\n  ")[:400] + ("..." if len(result.turn1_response) > 400 else ""),
        ])

    if result.turn2_response:
        lines.extend([
            "",
            "[Turn 2 Response Snippet]:",
            "  " + result.turn2_response.strip().replace("\n", "\n  ")[:400] + ("..." if len(result.turn2_response) > 400 else ""),
        ])

    if result.error:
        lines.extend([
            "",
            "[EXECUTION ERROR]:",
            f"  {result.error}",
        ])

    lines.extend([
        "",
        "[2. GENERATED PLAN (JSON)]",
        "-" * 80,
        json.dumps(result.plan_data, indent=2),
        "",
        "[3. EXPORTED DOCKER COMPOSE (YAML)]",
        "-" * 80,
        val.compose_yaml.strip() if (val and val.compose_yaml) else "# (No compose YAML generated)",
        "",
        "[4. VALIDATION RESULTS & ISSUES TO FIX]",
        "-" * 80,
    ])

    if val:
        lines.extend([
            f"Detected Services: {', '.join(val.detected_services) or 'None'}",
            f"Detected Internal Port: {val.detected_port or 'Unspecified'}",
            f"Detected Database Provider: {val.detected_database}",
            f"Total Errors: {val.error_count} | Total Warnings: {val.warning_count}",
            "",
        ])
        if not val.issues:
            lines.append("  [OK] No issues detected. Plan conforms to security, schema, and port policies.")
        else:
            for issue in val.issues:
                prefix = f"[{issue.severity}]"
                lines.append(f"{prefix} Field: {issue.field}")
                lines.append(f"     Problem:  {issue.message}")
                lines.append(f"     FIX HERE: {issue.fix_advice}")
                lines.append("")
    else:
        lines.append("  [ERROR] Validation was not performed due to plan generation failure.")

    lines.append("=" * 80)
    return "\n".join(lines)


def save_app_output_files(result: RunResult, base_dir: Path) -> Dict[str, Path]:
    """Saves docker-compose.yml, app_spec.json, and ai_trace_and_fixes.txt."""
    app_dir = base_dir / result.app.slug
    app_dir.mkdir(parents=True, exist_ok=True)

    saved: Dict[str, Path] = {}
    val = result.validation

    # 1. docker-compose.yml
    compose_path = app_dir / "docker-compose.yml"
    compose_content = val.compose_yaml if (val and val.compose_yaml) else "# No compose generated\n"
    compose_path.write_text(compose_content, encoding="utf-8")
    saved["compose_yaml"] = compose_path

    # 2. app_spec.json
    json_path = app_dir / "app_spec.json"
    json_path.write_text(json.dumps(result.plan_data, indent=2), encoding="utf-8")
    saved["plan_json"] = json_path

    # 3. ai_trace_and_fixes.txt
    txt_path = app_dir / "ai_trace_and_fixes.txt"
    report_text = format_app_report_text(result)
    txt_path.write_text(report_text, encoding="utf-8")
    saved["report_txt"] = txt_path

    # 4. complete_raw_log.txt
    if result.raw_log:
        raw_log_path = app_dir / "complete_raw_log.txt"
        raw_log_path.write_text(result.raw_log, encoding="utf-8")
        saved["raw_log"] = raw_log_path

    return saved


def format_scorecard_table(results: List[RunResult]) -> str:
    """Generates ASCII summary table across all benchmark results."""
    header = (
        f"{'APP / SLUG':<22} | {'TIER':<4} | {'STATUS':<6} | {'PORT':<5} | "
        f"{'DATABASE':<11} | {'ERRORS':<6} | {'TIME':<7}"
    )
    separator = "-" * len(header)
    rows = [separator, header, separator]

    total_pass = 0
    total_fail = 0

    for r in results:
        val = r.validation
        status = val.status if val else "FAIL"
        if status == "PASS":
            total_pass += 1
        else:
            total_fail += 1

        port_str = str(val.detected_port) if (val and val.detected_port) else "-"
        db_str = val.detected_database if val else "-"
        err_str = str(val.error_count) if val else "1"
        time_str = f"{r.duration_ms}ms"

        rows.append(
            f"{r.app.slug:<22} | {r.app.tier:<4} | {status:<6} | {port_str:<5} | "
            f"{db_str:<11} | {err_str:<6} | {time_str:<7}"
        )

    rows.extend([
        separator,
        f"TOTAL APPS: {len(results)} | PASSED: {total_pass} | FAILED: {total_fail}",
        separator,
    ])
    return "\n".join(rows)
