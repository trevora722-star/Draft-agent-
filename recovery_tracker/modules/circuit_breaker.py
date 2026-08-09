"""Urge circuit-breaker: 10-minute delay protocol with physical grounding steps.

Activated the moment an impulse or craving for novel digital activity hits.
Runs a Rich live countdown split across three sequential grounding phases,
then logs the incident to data/incidents.json.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.prompt import Confirm, Prompt
from rich.text import Text

from .storage import Incident, now_iso, save_incident

TOTAL_SECONDS = 600  # 10 minutes

GROUNDING_STEPS = [
    (
        "Step 1 of 3 — Physical state check",
        "Splash ice water on your face or do 20 pushups.\n"
        "Get your body involved: cold water, pushups, a brisk walk in place.\n"
        "The goal is a hard physical interrupt of the urge loop.",
    ),
    (
        "Step 2 of 3 — Change your environment",
        "Step away from screens and move into a shared living space.\n"
        "Leave the room where the impulse hit. Put the device down.\n"
        "Proximity to other people weakens the pull of the urge.",
    ),
    (
        "Step 3 of 3 — Reflect",
        "What emotional state (boredom, stress, fatigue) preceded this impulse?\n"
        "Sit with the question for the rest of the countdown.\n"
        "You'll be asked to name it when the timer ends.",
    ),
]


def _format_clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _render_phase(title: str, body: str, remaining: int, total: int) -> Panel:
    clock = Text(_format_clock(remaining), style="bold cyan", justify="center")
    clock.stylize("bold red" if remaining <= 60 else "bold cyan")
    bar = ProgressBar(total=total, completed=total - remaining, width=40)
    content = Group(
        Align.center(Text(title, style="bold yellow")),
        Text(""),
        Align.center(Text(body, justify="center")),
        Text(""),
        Align.center(clock),
        Align.center(bar),
    )
    return Panel(
        content,
        title="[bold red]URGE CIRCUIT-BREAKER[/bold red]",
        subtitle="The urge is a wave — it peaks and passes. Ride it out.",
        border_style="red",
    )


def _countdown_phase(console: Console, title: str, body: str, seconds: int) -> None:
    """Run one live-updating countdown segment."""
    from rich.live import Live

    with Live(console=console, refresh_per_second=4, transient=True) as live:
        for remaining in range(seconds, 0, -1):
            live.update(_render_phase(title, body, remaining, seconds))
            time.sleep(1)
        live.update(_render_phase(title, body, 0, seconds))
    console.print(f"[green]✓[/green] {title} complete.")


def run_circuit_breaker(
    console: Console | None = None,
    data_dir: Path | None = None,
    total_seconds: int = TOTAL_SECONDS,
) -> Incident | None:
    """Run the full 10-minute urge-delay protocol and log the incident."""
    console = console or Console()

    console.print(
        Panel(
            "You felt an impulse and you activated the circuit-breaker.\n"
            "That is already a win. The next 10 minutes belong to you, not the urge.",
            title="[bold]Circuit-Breaker Engaged[/bold]",
            border_style="cyan",
        )
    )

    trigger = Prompt.ask(
        "[bold]What triggered this urge?[/bold] (a thought, notification, boredom...)",
        default="unknown",
    )

    phase_seconds = max(total_seconds // len(GROUNDING_STEPS), 1)
    completed_protocol = True
    try:
        for title, body in GROUNDING_STEPS:
            _countdown_phase(console, title, body, phase_seconds)
    except KeyboardInterrupt:
        completed_protocol = False
        console.print("\n[yellow]Countdown interrupted early.[/yellow] Logging it honestly still counts.")

    emotional_state = Prompt.ask(
        "[bold]What emotional state preceded this impulse?[/bold] (boredom, stress, fatigue, loneliness...)",
        default="unsure",
    )
    impulse_passed = Confirm.ask("[bold]Has the impulse passed (or weakened)?[/bold]", default=True)
    notes = Prompt.ask("Any other notes (optional)", default="")

    incident = Incident(
        timestamp=now_iso(),
        trigger=trigger,
        emotional_state=emotional_state,
        impulse_passed=impulse_passed,
        completed_protocol=completed_protocol,
        duration_seconds=total_seconds,
        notes=notes,
    )
    save_incident(incident, data_dir)

    if impulse_passed:
        console.print("[bold green]The wave passed. Incident logged — this data helps your recovery.[/bold green]")
    else:
        console.print(
            "[bold yellow]The urge is still there — that's okay.[/bold yellow] "
            "Consider telling your spouse or re-running the breaker. Incident logged."
        )
    return incident
