"""PII anonymization — the "dealbreaker" layer for partners with PIPA / FOIPPA exposure.

Scrubs Canadian-relevant PII (names, emails, phones, SINs, postal codes,
PHNs, addresses, DOBs) before any text reaches the LLM. Replacements are
deterministic placeholders so the model can still reason about distinct
people without seeing identities.

The scrubber is intentionally conservative — false positives are preferable
to PII leaks. Where the model needs to write personalized output (e.g. a donor
letter), the rehydrate() pass restores names from a per-call salt map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canadian patterns. These are deliberately narrow — over-matching is fine,
# under-matching is the failure mode that loses customers.
EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
PHONE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)
SIN = re.compile(r"(?<!\d)\d{3}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")
POSTAL_CODE = re.compile(r"\b[A-CEGHJ-NPRSTVXY]\d[A-CEGHJ-NPRSTV-Z][ -]?\d[A-CEGHJ-NPRSTV-Z]\d\b")
# BC Personal Health Number — 10 digits, often shown grouped
BC_PHN = re.compile(r"(?<!\d)9\d{9}(?!\d)")
DATE_OF_BIRTH = re.compile(
    r"\b(?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])[-/](?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])\b"
)

# Streets — matches "123 Main Street" style. Deliberately conservative on
# unit numbers to avoid eating ordinary phrases like "Section 5".
STREET = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|"
    r"Drive|Dr\.?|Lane|Ln\.?|Way|Place|Pl\.?|Court|Crescent|Cres\.?)\b",
    re.IGNORECASE,
)

# Person-name detection — proper-noun bigrams. The LLM tolerates false positives
# on terms like "British Columbia" or "Crisis Line"; we exempt a small allowlist
# of org-relevant phrases to keep grant text readable.
NAME = re.compile(r"\b[A-Z][a-z]{1,15}\s+[A-Z][a-z]{1,15}(?:\s+[A-Z][a-z]{1,15})?\b")
NAME_ALLOWLIST = {
    "British Columbia",
    "Canada Revenue",
    "Crisis Line",
    "Mental Health",
    "Substance Use",
    "Indigenous Peoples",
    "First Nations",
    "Lower Mainland",
    "Vancouver Island",
    "Fraser Valley",
    "Greater Vancouver",
    "Service Canada",
    "Public Health",
    "Statistics Canada",
}


@dataclass
class ScrubResult:
    text: str
    # Map of placeholder -> original value. Held only in memory; never persisted.
    # Lets agents that produce outward-facing copy (e.g. donor letters) rehydrate
    # the names after the model has reasoned about them as anonymous tokens.
    mapping: dict[str, str] = field(default_factory=dict)

    def rehydrate(self, output: str) -> str:
        """Substitute placeholders back to originals in model output."""
        for placeholder, original in self.mapping.items():
            output = output.replace(placeholder, original)
        return output


def _replace_with_counter(
    text: str,
    pattern: re.Pattern[str],
    label: str,
    counters: dict[str, int],
    mapping: dict[str, str],
    *,
    skip: set[str] | None = None,
) -> str:
    skip = skip or set()

    def repl(match: re.Match[str]) -> str:
        original = match.group(0)
        if original in skip:
            return original
        if original in mapping.values():
            # Reuse existing placeholder for same value within one pass.
            for placeholder, value in mapping.items():
                if value == original:
                    return placeholder
        counters[label] = counters.get(label, 0) + 1
        placeholder = f"[{label}_{counters[label]}]"
        mapping[placeholder] = original
        return placeholder

    return pattern.sub(repl, text)


def scrub(text: str) -> ScrubResult:
    """Replace PII in text with deterministic placeholders.

    Order matters: SIN/PHN before generic numbers, names last so structured
    identifiers don't get eaten by the name pattern.
    """
    counters: dict[str, int] = {}
    mapping: dict[str, str] = {}

    text = _replace_with_counter(text, EMAIL, "EMAIL", counters, mapping)
    text = _replace_with_counter(text, SIN, "SIN", counters, mapping)
    text = _replace_with_counter(text, BC_PHN, "PHN", counters, mapping)
    text = _replace_with_counter(text, PHONE, "PHONE", counters, mapping)
    text = _replace_with_counter(text, POSTAL_CODE, "POSTAL", counters, mapping)
    text = _replace_with_counter(text, DATE_OF_BIRTH, "DOB", counters, mapping)
    text = _replace_with_counter(text, STREET, "ADDRESS", counters, mapping)
    text = _replace_with_counter(
        text, NAME, "NAME", counters, mapping, skip=NAME_ALLOWLIST
    )

    return ScrubResult(text=text, mapping=mapping)


def has_pii(text: str) -> bool:
    """Quick check used by API endpoints to flag unsafe inbound payloads."""
    for pattern in (EMAIL, SIN, BC_PHN, PHONE, POSTAL_CODE, DATE_OF_BIRTH):
        if pattern.search(text):
            return True
    return False
