"""Interactive daily check-in and symptom tracker.

Asks a short series of scaled and yes/no questions and appends the entry,
timestamped, to data/daily_logs.json.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from .storage import DailyLog, now_iso, save_daily_log


def _ask_scale(question: str) -> int:
    """Ask a 1-10 scaled question, re-prompting until valid."""
    while True:
        value = IntPrompt.ask(f"{question} [dim](1-10)[/dim]")
        if 1 <= value <= 10:
            return value
        Console().print("[red]Please enter a number between 1 and 10.[/red]")


def run_daily_checkin(console: Console | None = None, data_dir: Path | None = None) -> DailyLog:
    """Run the daily check-in and save the entry."""
    console = console or Console()

    console.print(
        Panel(
            "A two-minute honest snapshot of today. No judgment — just data\n"
            "you and your care team can use to see patterns over time.",
            title="[bold cyan]Daily Check-In[/bold cyan]",
            border_style="cyan",
        )
    )

    brain_fog = _ask_scale("[bold]Brain fatigue / TBI brain fog today[/bold]")
    stress = _ask_scale("[bold]Stress and anxiety today[/bold]")
    boundaries_kept = Confirm.ask("[bold]Did you stick to your digital boundaries today?[/bold]")
    impulses_occurred = Confirm.ask(
        "[bold]Did any sudden novelty-seeking impulses occur today?[/bold]", default=False
    )
    impulse_notes = ""
    if impulses_occurred:
        impulse_notes = Prompt.ask("  Briefly describe what happened", default="")
    activities = Prompt.ask(
        "[bold]What healthy dopamine activities did you complete today?[/bold] "
        "(exercise, music, time outside...)",
        default="",
    )

    entry = DailyLog(
        timestamp=now_iso(),
        brain_fog=brain_fog,
        stress=stress,
        boundaries_kept=boundaries_kept,
        impulses_occurred=impulses_occurred,
        impulse_notes=impulse_notes,
        healthy_dopamine_activities=activities,
    )
    save_daily_log(entry, data_dir)

    summary = Table(title="Today's Entry", show_header=False, border_style="dim")
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Brain fog", f"{brain_fog}/10")
    summary.add_row("Stress / anxiety", f"{stress}/10")
    summary.add_row("Boundaries kept", "Yes" if boundaries_kept else "No")
    summary.add_row("Impulses today", "Yes" if impulses_occurred else "No")
    if impulse_notes:
        summary.add_row("Impulse notes", impulse_notes)
    if activities:
        summary.add_row("Healthy dopamine", activities)
    console.print(summary)
    console.print("[green]Saved. Showing up daily is the whole game — well done.[/green]")
    return entry
