# coParenter Compliance Tracker

Local, agentic legal-discovery pipeline that parses a raw **coParenter** app
export, tests every event against the controlling **Court Order**, and produces
an emotion-free, lawyer-ready compliance spreadsheet.

## Folder tree

```
compliance-tracker/
├── inputs/                 # place court_order.txt and coparenter_log.pdf here
├── outputs/                # generated .xlsx / .csv deliverables land here
├── requirements.txt
└── main.py                 # principal execution engine
```

`inputs/` and `outputs/` are git-ignored — case evidence never gets committed.

## Setup

```bash
cd compliance-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...     # or authenticate once via `ant auth login`
```

## Run

1. Save the controlling order's text as `inputs/court_order.txt` (plain UTF-8).
2. Save the coParenter export as `inputs/coparenter_log.pdf`.
3. Run:

```bash
python main.py
# or with explicit paths:
python main.py --pdf path/to/log.pdf --order path/to/order.txt --outputs outputs/
```

## What it does

1. **Extraction** — `pypdf` (with automatic `pdfplumber` fallback) pulls text
   page-by-page; every line is tagged with its source page (`[Log Page 12]`)
   so results always map back to the evidence.
2. **Token-safe chunking** — the log is split into ~12,000-token chunks and
   processed sequentially, so massive exports never hit context or timeout
   limits.
3. **Agentic evaluation** — each chunk plus the Court Order rulebook goes to
   Claude (`claude-opus-5`) with **schema-enforced structured outputs**
   (Pydantic → native JSON-schema enforcement), acting as a strictly objective
   family-law discovery expert. All emotional/subjective vocabulary is stripped;
   each event gets exactly one status:
   `Compliant`, `Violation: Late/No-Show`, `Violation: Response Delay`,
   `Violation: Non-Payment`, `Violation: Gatekeeping`, or `Unclassified/Neutral`.
   The rulebook is prompt-cached, so chunks 2+ read it at ~10% input cost.
4. **Spreadsheet deployment** — all chunks aggregate into one pandas
   DataFrame, sorted chronologically, exported as a formatted `.xlsx`
   (styled header, wrapped text, violation rows tinted red, compliant rows
   green, frozen header, autofilter) plus a plain `.csv` twin.

Columns, in order: **Timestamp** (`YYYY-MM-DD HH:MM`), **Event Type**,
**Actor**, **Court Order Requirement**, **Actual Event**, **Compliance
Status**, **Fact-Based Justification**, **Evidence Reference**.

## Error handling / fallbacks

- Missing input files, empty rulebook → clear actionable error, non-zero exit.
- Missing/invalid API credentials → explicit setup instructions.
- Empty or image-only PDF pages → warned and skipped (OCR hint printed).
- Messy app date layouts → multi-format normalizer with `dateutil` fuzzy
  fallback; unparseable timestamps are kept verbatim (evidence is never
  discarded) and sink to the bottom of the sort.
- Transient API errors (rate limits, 5xx, network) → exponential-backoff
  retries; a chunk that ultimately fails becomes a visible "Processing Gap"
  row citing its page range, so nothing disappears silently.
