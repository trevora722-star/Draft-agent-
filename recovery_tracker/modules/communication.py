"""Non-defensive communication assistant for relationship transparency.

The user drafts (or pastes) a message intended for their spouse. The draft is
checked against four strict criteria, and specific suggestions are returned
before sending:

1. Takes full accountability without making excuses.
2. Avoids defensive or minimizing language.
3. Prioritizes empathy for the spouse's feelings.
4. Is clear, direct, and actionable.

A local rule-based analysis always runs. If the ``anthropic`` package is
installed and ``ANTHROPIC_API_KEY`` is set, an optional LLM review can be
requested for deeper feedback.
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

CRITERIA = [
    "Takes full accountability without excuses",
    "Avoids defensive or minimizing language",
    "Prioritizes empathy for spouse's feelings",
    "Clear, direct, and actionable",
]

# Phrases that shift blame or excuse the behavior.
EXCUSE_PATTERNS = [
    "because i was", "i couldn't help", "couldn't help it", "it wasn't my fault",
    "not my fault", "the stress made me", "you made me", "if you hadn't",
    "i only did it because", "everyone does", "i was just", "it just happened",
]

# Defensive or minimizing phrases.
DEFENSIVE_PATTERNS = [
    "it's not a big deal", "not that bad", "it's not that serious", "calm down",
    "overreacting", "you always", "you never", "at least i", "it was only",
    "it was just", "honestly it", "to be fair", "in my defense", "but you",
    "whatever", "here we go again",
]

# Markers of owning the behavior.
ACCOUNTABILITY_PATTERNS = [
    "i take full responsibility", "i take responsibility", "my responsibility",
    "i was wrong", "i broke", "i chose", "i made the choice", "my fault",
    "i'm sorry", "i am sorry", "i apologize", "i let you down", "i lied",
    "i hid", "i crossed",
]

# Markers of empathy toward the spouse.
EMPATHY_PATTERNS = [
    "you feel", "you felt", "you must feel", "your trust", "hurt you",
    "i understand", "i can imagine", "i hear you", "your feelings",
    "you deserve", "for you", "what this did to you", "i see how",
]

# Markers of clear, forward-looking action.
ACTION_PATTERNS = [
    "i will", "i'm going to", "i am going to", "going forward", "next time i",
    "my plan is", "i've set up", "i have set up", "tonight i", "tomorrow i",
    "from now on", "i commit", "you can check", "i'll show you",
]


def _find_matches(text: str, patterns: list[str]) -> list[str]:
    lowered = text.lower()
    return [p for p in patterns if p in lowered]


def analyze_draft(text: str) -> dict:
    """Analyze a draft message against the four criteria.

    Returns a dict with per-criterion results:
    ``{"criteria": [{"name", "passed", "suggestions": [...]}, ...], "score": int}``
    """
    text = text.strip()
    results = []

    excuses = _find_matches(text, EXCUSE_PATTERNS)
    accountability = _find_matches(text, ACCOUNTABILITY_PATTERNS)
    suggestions = []
    if excuses:
        suggestions.append(
            f"Remove excuse language ({', '.join(repr(e) for e in excuses)}). "
            "State what you did without a 'because'."
        )
    if not accountability:
        suggestions.append(
            "Name the behavior and own it directly, e.g. 'I broke my boundary and "
            "I take full responsibility for that.'"
        )
    results.append({
        "name": CRITERIA[0],
        "passed": bool(accountability) and not excuses,
        "suggestions": suggestions,
    })

    defensive = _find_matches(text, DEFENSIVE_PATTERNS)
    suggestions = []
    if defensive:
        suggestions.append(
            f"Defensive/minimizing phrases found ({', '.join(repr(d) for d in defensive)}). "
            "Cut them — they shift focus away from repair."
        )
    results.append({
        "name": CRITERIA[1],
        "passed": not defensive,
        "suggestions": suggestions,
    })

    empathy = _find_matches(text, EMPATHY_PATTERNS)
    suggestions = []
    if not empathy:
        suggestions.append(
            "Acknowledge your spouse's experience explicitly, e.g. 'I understand this "
            "hurt you and damaged your trust.'"
        )
    results.append({
        "name": CRITERIA[2],
        "passed": bool(empathy),
        "suggestions": suggestions,
    })

    actions = _find_matches(text, ACTION_PATTERNS)
    suggestions = []
    if not actions:
        suggestions.append(
            "End with one concrete, checkable commitment, e.g. 'Tonight I will show "
            "you my screen-time report.'"
        )
    if len(text) < 40:
        suggestions.append("The message is very short — say what happened, how it affected them, and what you'll do.")
    if len(text) > 1200:
        suggestions.append("The message is long — trim it so the accountability and the commitment stand out.")
    results.append({
        "name": CRITERIA[3],
        "passed": bool(actions) and 40 <= len(text) <= 1200,
        "suggestions": suggestions,
    })

    return {"criteria": results, "score": sum(1 for r in results if r["passed"])}


def llm_review(text: str) -> str | None:
    """Optional deeper review via the Anthropic API. Returns None if unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=2048,
            system=(
                "You are a communication coach helping someone in recovery write a "
                "transparent, non-defensive message to their spouse. Evaluate the draft "
                "against: (1) full accountability without excuses, (2) no defensive or "
                "minimizing language, (3) empathy for the spouse's feelings, (4) clear, "
                "direct, actionable. Give brief, specific suggestions and a revised "
                "version. Keep the person's own voice — do not make it sound scripted."
            ),
            messages=[{"role": "user", "content": f"Here is my draft:\n\n{text}"}],
        )
        if response.stop_reason == "refusal":
            return None
        return next((b.text for b in response.content if b.type == "text"), None)
    except Exception:
        return None


def run_communication_assistant(console: Console | None = None) -> dict | None:
    """Interactive flow: collect a draft, analyze it, show suggestions."""
    console = console or Console()

    console.print(
        Panel(
            "Draft or paste the message you want to send to your spouse.\n"
            "Finish by entering an empty line twice (or type END on its own line).",
            title="[bold magenta]Communication Partner[/bold magenta]",
            border_style="magenta",
        )
    )

    lines: list[str] = []
    blank_streak = 0
    while True:
        try:
            line = console.input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().upper() == "END":
            break
        if not line.strip():
            blank_streak += 1
            if blank_streak >= 2 and lines:
                break
            continue
        blank_streak = 0
        lines.append(line)

    draft = "\n".join(lines).strip()
    if not draft:
        console.print("[yellow]No draft entered — returning to menu.[/yellow]")
        return None

    result = analyze_draft(draft)

    table = Table(title="Draft Review", border_style="magenta")
    table.add_column("Criterion", style="bold")
    table.add_column("Result", justify="center")
    for item in result["criteria"]:
        table.add_row(item["name"], "[green]✓ pass[/green]" if item["passed"] else "[red]✗ needs work[/red]")
    console.print(table)
    console.print(f"Score: [bold]{result['score']}/4[/bold]")

    all_suggestions = [s for item in result["criteria"] for s in item["suggestions"]]
    if all_suggestions:
        console.print(
            Panel(
                "\n\n".join(f"• {s}" for s in all_suggestions),
                title="Suggestions before sending",
                border_style="yellow",
            )
        )
    else:
        console.print("[green]This reads as accountable, empathetic, and actionable. Send it.[/green]")

    if os.environ.get("ANTHROPIC_API_KEY"):
        if Confirm.ask("Get a deeper AI-assisted review of this draft?", default=False):
            console.print("[dim]Asking Claude for a review...[/dim]")
            review = llm_review(draft)
            if review:
                console.print(Panel(review, title="AI Review", border_style="blue"))
            else:
                console.print("[yellow]AI review unavailable right now — the local review above still applies.[/yellow]")

    return result
