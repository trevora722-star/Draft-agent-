"""Clinical report generator.

Reads data/daily_logs.json and data/incidents.json and produces a clean
Markdown report (report_YYYY-MM-DD.md) summarizing 7-day or 30-day trends
for sharing with a doctor or psychologist.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .storage import (
    DailyLog,
    Incident,
    ensure_data_dir,
    load_daily_logs,
    load_incidents,
)

DISCLAIMER = (
    "*This report is generated for personal tracking and clinical collaboration "
    "with licensed medical professionals.*"
)


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _within_window(ts: str, days: int, today: date) -> bool:
    parsed = _parse_ts(ts)
    return parsed is not None and parsed.date() >= today - timedelta(days=days - 1)


def build_report(
    logs: list[DailyLog],
    incidents: list[Incident],
    days: int = 7,
    today: date | None = None,
) -> str:
    """Build the Markdown report text from in-memory data (pure, testable)."""
    today = today or date.today()
    logs = [e for e in logs if _within_window(e.timestamp, days, today)]
    incidents = [i for i in incidents if _within_window(i.timestamp, days, today)]

    lines = [
        f"# Recovery Tracker Report — {today.isoformat()}",
        "",
        DISCLAIMER,
        "",
        f"**Reporting window:** last {days} days "
        f"({(today - timedelta(days=days - 1)).isoformat()} to {today.isoformat()})",
        "",
        "## Summary",
        "",
    ]

    if not logs and not incidents:
        lines += ["No data was recorded in this window.", ""]
        return "\n".join(lines)

    lines.append(f"- Daily check-ins completed: **{len(logs)}** of {days} days")
    lines.append(f"- Impulse (circuit-breaker) events logged: **{len(incidents)}**")
    lines.append("")

    lines += ["## Cognitive & Emotional Trends", ""]
    if logs:
        avg_fog = sum(e.brain_fog for e in logs) / len(logs)
        avg_stress = sum(e.stress for e in logs) / len(logs)
        compliant = sum(1 for e in logs if e.boundaries_kept)
        compliance_rate = 100.0 * compliant / len(logs)
        impulse_days = sum(1 for e in logs if e.impulses_occurred)
        lines += [
            "| Metric | Value |",
            "| --- | --- |",
            f"| Average brain fatigue / brain fog (1-10) | {avg_fog:.1f} |",
            f"| Average stress and anxiety (1-10) | {avg_stress:.1f} |",
            f"| Digital boundary compliance rate | {compliance_rate:.0f}% ({compliant}/{len(logs)} days) |",
            f"| Days with novelty-seeking impulses | {impulse_days}/{len(logs)} |",
            "",
        ]
        activities = [e.healthy_dopamine_activities for e in logs if e.healthy_dopamine_activities.strip()]
        if activities:
            lines += ["**Healthy dopamine activities logged:**", ""]
            lines += [f"- {a}" for a in activities]
            lines.append("")
    else:
        lines += ["No daily check-ins recorded in this window.", ""]

    lines += ["## Impulse Events (Circuit-Breaker)", ""]
    if incidents:
        passed = sum(1 for i in incidents if i.impulse_passed)
        completed = sum(1 for i in incidents if i.completed_protocol)
        lines += [
            f"- Impulses that passed after the delay protocol: **{passed}/{len(incidents)}**",
            f"- Full 10-minute protocols completed: **{completed}/{len(incidents)}**",
            "",
        ]
        emotional = Counter(i.emotional_state.strip().lower() for i in incidents if i.emotional_state.strip())
        if emotional:
            lines += ["**Emotional states preceding impulses:**", ""]
            lines += [f"- {state}: {count}×" for state, count in emotional.most_common()]
            lines.append("")
        triggers = Counter(i.trigger.strip().lower() for i in incidents if i.trigger.strip())
        if triggers:
            lines += ["**Reported triggers:**", ""]
            lines += [f"- {trig}: {count}×" for trig, count in triggers.most_common()]
            lines.append("")
    else:
        lines += ["No impulse events recorded in this window.", ""]

    return "\n".join(lines)


def write_report(
    days: int = 7,
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    today: date | None = None,
) -> Path:
    """Generate and write report_YYYY-MM-DD.md; returns the file path."""
    today = today or date.today()
    text = build_report(load_daily_logs(data_dir), load_incidents(data_dir), days, today)
    out_dir = Path(output_dir) if output_dir else ensure_data_dir(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{today.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    return path


def run_report_generator(console: Console | None = None, data_dir: Path | None = None) -> Path:
    """Interactive flow: pick a window, generate the report, show the path."""
    console = console or Console()
    choice = Prompt.ask(
        "[bold]Report window[/bold]", choices=["7", "30"], default="7"
    )
    path = write_report(days=int(choice), data_dir=data_dir)
    console.print(
        Panel(
            f"Report written to:\n[bold cyan]{path}[/bold cyan]\n\n"
            "Share it with your doctor or psychologist at your next visit.",
            title="[bold green]Report Generated[/bold green]",
            border_style="green",
        )
    )
    return path
