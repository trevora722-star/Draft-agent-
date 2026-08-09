#!/usr/bin/env python3
"""recovery_tracker — daily self-accountability dashboard and impulse circuit-breaker.

A local, interactive CLI for managing executive dysfunction, TBI recovery
symptoms, and ADHD. All data stays on this machine, in JSON files under
``recovery_tracker/data/``.

Run:  python app.py
Fast demo countdowns:  RECOVERY_TRACKER_FAST=1 python app.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

REQUIRED = ["rich", "pydantic"]
OPTIONAL = ["anthropic"]  # for LLM-assisted communication review (needs ANTHROPIC_API_KEY)


def ensure_dependencies() -> None:
    """Install missing dependencies automatically on first run."""
    missing = [pkg for pkg in REQUIRED if importlib.util.find_spec(pkg) is None]
    if missing:
        print(f"Installing required dependencies: {', '.join(missing)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *missing])
    for pkg in OPTIONAL:
        if importlib.util.find_spec(pkg) is None:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                )
            except subprocess.CalledProcessError:
                print(f"(optional dependency '{pkg}' could not be installed — continuing without it)")


ensure_dependencies()

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Prompt  # noqa: E402
from rich.table import Table  # noqa: E402

from modules import circuit_breaker, communication, daily_logger, reports  # noqa: E402
from modules.storage import ensure_data_dir, load_daily_logs, load_incidents  # noqa: E402

console = Console()

# RECOVERY_TRACKER_FAST=1 shortens the 10-minute countdown to ~15s for demos/tests.
FAST_MODE = os.environ.get("RECOVERY_TRACKER_FAST") == "1"
COUNTDOWN_SECONDS = 15 if FAST_MODE else circuit_breaker.TOTAL_SECONDS


def show_recent(console: Console) -> None:
    """Quick glance at recent check-ins and incidents."""
    logs = load_daily_logs()[-7:]
    incidents = load_incidents()[-5:]

    if not logs and not incidents:
        console.print("[yellow]No data yet — start with a daily check-in.[/yellow]")
        return

    if logs:
        table = Table(title="Recent Daily Check-Ins", border_style="cyan")
        table.add_column("When", style="dim")
        table.add_column("Fog", justify="center")
        table.add_column("Stress", justify="center")
        table.add_column("Boundaries", justify="center")
        table.add_column("Impulses", justify="center")
        for entry in logs:
            table.add_row(
                entry.timestamp[:16].replace("T", " "),
                f"{entry.brain_fog}/10",
                f"{entry.stress}/10",
                "[green]yes[/green]" if entry.boundaries_kept else "[red]no[/red]",
                "[red]yes[/red]" if entry.impulses_occurred else "[green]no[/green]",
            )
        console.print(table)

    if incidents:
        table = Table(title="Recent Impulse Events", border_style="red")
        table.add_column("When", style="dim")
        table.add_column("Trigger")
        table.add_column("Emotional state")
        table.add_column("Passed?", justify="center")
        for inc in incidents:
            table.add_row(
                inc.timestamp[:16].replace("T", " "),
                inc.trigger,
                inc.emotional_state,
                "[green]yes[/green]" if inc.impulse_passed else "[yellow]no[/yellow]",
            )
        console.print(table)


def main() -> None:
    ensure_data_dir()
    console.print(
        Panel(
            "[bold]Recovery Tracker[/bold]\n"
            "Daily self-accountability dashboard & impulse circuit-breaker\n"
            "[dim]All data stays local, in recovery_tracker/data/[/dim]"
            + ("\n[yellow]FAST MODE: countdowns shortened for demo[/yellow]" if FAST_MODE else ""),
            border_style="bold cyan",
        )
    )

    while True:
        menu = Table(show_header=False, border_style="dim", padding=(0, 2))
        menu.add_column(style="bold cyan", justify="right")
        menu.add_column()
        menu.add_row("1", "🚨 Urge circuit-breaker (10-minute delay protocol)")
        menu.add_row("2", "📝 Daily check-in & symptom tracker")
        menu.add_row("3", "💬 Communication partner (message review)")
        menu.add_row("4", "📄 Generate clinical report (7/30-day trends)")
        menu.add_row("5", "📊 View recent data")
        menu.add_row("q", "Quit")
        console.print(menu)

        choice = Prompt.ask("Choose", choices=["1", "2", "3", "4", "5", "q"], default="q")
        console.print()
        try:
            if choice == "1":
                circuit_breaker.run_circuit_breaker(console, total_seconds=COUNTDOWN_SECONDS)
            elif choice == "2":
                daily_logger.run_daily_checkin(console)
            elif choice == "3":
                communication.run_communication_assistant(console)
            elif choice == "4":
                reports.run_report_generator(console)
            elif choice == "5":
                show_recent(console)
            else:
                console.print("[cyan]One day at a time. See you tomorrow.[/cyan]")
                break
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled — back to menu.[/yellow]")
        console.print()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[cyan]Goodbye.[/cyan]")
