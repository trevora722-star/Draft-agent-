"""Tone-of-voice presets per tenant.

Each NPO gets to pick a persona during onboarding. The persona text is appended
to every system prompt so the model writes in the voice the org's board has
already approved.
"""

PERSONAS: dict[str, str] = {
    "clinical-empathetic": (
        "Write in a tone that is clinical yet empathetic. Lead with evidence "
        "and outcomes. Acknowledge lived experience without dramatizing it. "
        "Avoid jargon that excludes lay readers; define clinical terms inline. "
        "Suitable for mental-health and crisis-services nonprofits (e.g., BCSS)."
    ),
    "energetic-casual": (
        "Write in an energetic, casual, optimistic tone. Use second person and "
        "active verbs. Celebrate wins and community. Suitable for youth, sports, "
        "and arts nonprofits."
    ),
    "formal-institutional": (
        "Write in a formal, institutional voice suitable for board reports, "
        "provincial funders, and federal grants. Cite outcomes precisely and "
        "use measured, neutral language."
    ),
    "warm-grassroots": (
        "Write in a warm, grassroots, plain-language voice. Center the people "
        "served. Avoid corporate phrasing. Suitable for food banks, shelters, "
        "and neighborhood-scale orgs."
    ),
}

DEFAULT_PERSONA = "clinical-empathetic"


def persona_text(name: str) -> str:
    return PERSONAS.get(name, PERSONAS[DEFAULT_PERSONA])
