"""Grant Scout & Drafter — the MVP module.

Why this is the right v0:
  - Highest tangible value (~20 hours/month per ED, easy line-item ROI)
  - Cleanest privacy boundary — public funder documents in, internal NPO
    program data + scrubbed PII out
  - Easiest to clear with a Board of Directors: the human ED still reviews
    and signs the application; the agent just writes the first 80%
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from ..db import connect
from ..llm import LLMResponse, complete
from .base import Agent

GRANT_WRITER_SYSTEM = """You are an experienced Canadian grant writer for nonprofits. \
You draft the first 80% of grant applications: clear theory of change, measurable \
outcomes, plain-language need statements, defensible budgets, and answers calibrated \
to the funder's stated priorities.

Hard rules:
1. Use ONLY facts present in the provided NPO program context. Do not invent \
   programs, populations served, dollar amounts, or staff counts. If a required \
   field has no supporting evidence in the context, write "[ED to confirm: ...]" \
   and continue — never fabricate.
2. Privacy: client and staff names may appear as placeholders like [NAME_1], \
   [EMAIL_1], [PHONE_1]. Treat each placeholder as a distinct real person. \
   Do not try to "fill them in" or guess their identities.
3. Quote the funder's own priority language back to them when it fits — funders \
   reward applicants who demonstrate close reading.
4. Outputs are first drafts for the Executive Director to revise, not final copy. \
   Mark uncertainty with [ED to confirm: ...] inline rather than smoothing it over.
"""


@dataclass(frozen=True)
class GrantDraft:
    id: str
    funder: str
    title: str
    body: str
    metadata: dict
    usage: LLMResponse


class GrantWriter(Agent):
    name = "grant_writer"

    def draft(
        self,
        *,
        funder: str,
        opportunity_title: str,
        funder_brief: str,
        ask_amount: str | None = None,
        program_focus: str | None = None,
        max_tokens: int = 16_000,
    ) -> GrantDraft:
        """Draft a full grant application from a funder brief + tenant program data.

        Workflow:
          1. Retrieve the tenant's most relevant internal docs (program descriptions,
             past evaluations, theory of change) from their isolated namespace.
          2. Scrub PII from both the funder brief and the retrieved docs.
          3. Send a single prompt-cache-friendly request: stable system + persona
             prefix, then the volatile per-application payload.
          4. Rehydrate any placeholders the model may have echoed (rare — but if
             the funder brief contained a contact name, we restore it).
          5. Persist the draft under the tenant_id for later review/diff.
        """
        retrieval_query = f"{opportunity_title}\n{program_focus or ''}\n{funder_brief}"
        hits = self._retrieve(retrieval_query, namespace="programs", k=6)
        # Fall back to default namespace if the tenant hasn't tagged programs yet.
        if not hits:
            hits = self._retrieve(retrieval_query, k=6)
        program_context = self._format_context(hits)

        (scrubbed_brief, scrubbed_context), mapping = self._scrub(
            funder_brief, program_context
        )

        user_payload = (
            f"# Funder\n{funder}\n\n"
            f"# Opportunity\n{opportunity_title}\n\n"
            f"# Ask amount\n{ask_amount or '[ED to confirm]'}\n\n"
            f"# Funder brief (verbatim, PII-scrubbed)\n{scrubbed_brief}\n\n"
            f"# NPO program context (top retrieved documents, PII-scrubbed)\n"
            f"{scrubbed_context}\n\n"
            f"# Task\n"
            f"Produce a complete first-draft grant application with these sections, in order:\n"
            f"  1. Executive Summary (≤200 words)\n"
            f"  2. Statement of Need\n"
            f"  3. Program / Project Description\n"
            f"  4. Theory of Change & Measurable Outcomes\n"
            f"  5. Implementation Plan & Timeline\n"
            f"  6. Budget Narrative (line items with rationale; mark TBD with "
            f"     [ED to confirm: ...])\n"
            f"  7. Organizational Capacity\n"
            f"  8. Evaluation & Reporting Plan\n"
            f"  9. Sustainability\n"
            f" 10. Alignment with Funder Priorities (quote the funder's own language)\n"
            f"\nUse Markdown headings. Be specific. Mark every assumption you "
            f"can't ground in the program context with [ED to confirm: ...].\n"
        )

        response = complete(
            system_prompt=GRANT_WRITER_SYSTEM,
            persona=self.context.persona,
            user_content=user_payload,
            max_tokens=max_tokens,
            use_thinking=True,
            effort="high",
        )

        body = self._rehydrate(response.text, mapping)

        draft_id = uuid.uuid4().hex
        metadata = {
            "ask_amount": ask_amount,
            "program_focus": program_focus,
            "retrieved_docs": [h.document.id for h in hits],
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cache_read_tokens": response.cache_read_tokens,
            "stop_reason": response.stop_reason,
        }

        with connect() as conn:
            conn.execute(
                "INSERT INTO grant_drafts (id, tenant_id, funder, title, body, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    draft_id,
                    self.context.tenant.id,
                    funder,
                    opportunity_title,
                    body,
                    json.dumps(metadata),
                ),
            )

        return GrantDraft(
            id=draft_id,
            funder=funder,
            title=opportunity_title,
            body=body,
            metadata=metadata,
            usage=response,
        )

    def list_drafts(self) -> list[dict]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, funder, title, created_at FROM grant_drafts "
                "WHERE tenant_id = ? ORDER BY created_at DESC",
                (self.context.tenant.id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_draft(self, draft_id: str) -> dict | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT id, funder, title, body, metadata, created_at FROM grant_drafts "
                "WHERE tenant_id = ? AND id = ?",
                (self.context.tenant.id, draft_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("metadata"):
            try:
                result["metadata"] = json.loads(result["metadata"])
            except json.JSONDecodeError:
                pass
        return result
