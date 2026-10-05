"""Write ``verify-report.md`` and ``verify-report.json``; apply human acceptances."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from galley.verify.checks import CheckResult

REPORT_JSON = "verify-report.json"
REPORT_MD = "verify-report.md"
ACCEPT_FILE = "verify-accept.yaml"
# A numeric mismatch may only be accepted by a named person, never by a tool.
NOT_A_PERSON = re.compile(r"claude|galley|assistant|\bai\b|\bbot\b|agent|auto", re.IGNORECASE)
ICONS = {
    "pass": "PASS",
    "warn": "PASS (see notes)",
    "fail": "FAIL",
    "advisory": "ADVISORY",
    "skipped": "SKIPPED",
}


def apply_acceptances(checks: list[CheckResult], paper_dir: Path) -> list[str]:
    """Mark findings listed in ``verify-accept.yaml`` as accepted; return rejected entries."""
    path = paper_dir / ACCEPT_FILE
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    entries = raw.get("accept", []) if isinstance(raw, dict) else raw
    by_id = {f.id: f for check in checks for f in check.findings}
    rejected: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or "id" not in entry:
            rejected.append(f"malformed entry: {entry!r}")
            continue
        finding = by_id.get(str(entry["id"]))
        if finding is None:
            continue  # refers to a difference that no longer exists
        approver = str(entry.get("approved-by", "")).strip()
        if finding.numeric and (not approver or NOT_A_PERSON.search(approver)):
            rejected.append(f"{finding.id}: a numeric mismatch needs `approved-by` naming a person")
            continue
        finding.accepted = True
        finding.accepted_reason = str(entry.get("reason", "")) + (
            f" (approved by {approver})" if approver else ""
        )
    return rejected


def overall_status(checks: list[CheckResult]) -> str:
    return "fail" if any(c.status == "fail" for c in checks) else "pass"


def build_report(
    checks: list[CheckResult],
    *,
    source: Path,
    paper_dir: Path,
    pdf: Path,
    thresholds: dict[str, Any],
    rejected: list[str],
    visual: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    return {
        "status": overall_status(checks),
        "source": source.name,
        "pdf": pdf.name,
        "paper": paper_dir.name,
        "checks": [c.as_dict() for c in checks],
        "thresholds": thresholds,
        "rejected-acceptances": rejected,
        "visual": visual,
        "generated-at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ") or " "


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Verify report",
        "",
        f"**{report['status'].upper()}**: `{report['source']}` against `{report['pdf']}` "
        f"({report['generated-at']})",
        "",
        "| Check | Result | Summary |",
        "| --- | --- | --- |",
    ]
    for check in report["checks"]:
        kind = "" if check["hard"] else " (advisory)"
        lines.append(
            f"| {check['title']}{kind} | {ICONS[check['status']]} | {_cell(check['summary'])} |"
        )
    for check in report["checks"]:
        if not check["findings"]:
            continue
        lines += [
            "",
            f"## {check['title']}",
            "",
            "| Id | What | Where | Source | Galley PDF |",
            "| --- | --- | --- | --- | --- |",
        ]
        for finding in check["findings"]:
            what = finding["message"]
            if finding["accepted"]:
                what += f" — accepted: {finding['accepted_reason']}"
            elif not finding["fails"]:
                what += " (does not fail the check)"
            lines.append(
                f"| `{finding['id']}` | {_cell(what)} | {_cell(finding['location'])} "
                f"| {_cell(finding['source'])} | {_cell(finding['candidate'])} |"
            )
    if report["rejected-acceptances"]:
        lines += ["", "## Acceptances not applied", ""]
        lines += [f"- {entry}" for entry in report["rejected-acceptances"]]
    if report["visual"]:
        lines += [
            "",
            "## Visual comparison (advisory)",
            "",
            "Layout changes on purpose, so these are for a human to compare:",
            "",
        ]
        lines += [f"- `{name}`" for name in report["visual"]]
    lines += [
        "",
        "To accept a difference, add its id to `verify-accept.yaml` with a `reason`. "
        "Numeric differences also need `approved-by` naming the person who checked it.",
        "",
    ]
    return "\n".join(lines)


def write_reports(report: dict[str, Any], paper_dir: Path) -> tuple[Path, Path]:
    json_path = paper_dir / REPORT_JSON
    md_path = paper_dir / REPORT_MD
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path, json_path
