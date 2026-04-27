"""Policy Navigator — internal Q&A over the NPO's HR / Safety / Compliance corpus.

Cheaper to run than Grant Writer (Haiku, no thinking) because the answers
are short and grounded in retrieved snippets. The point isn't to reason —
it's to surface the right paragraph from the org's own policy binder.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings
from ..llm import LLMResponse, complete
from .base import Agent

POLICY_NAVIGATOR_SYSTEM = """You are an internal-facing policy navigator for a \
Canadian nonprofit. Staff ask you questions about HR, Safety, and BC Societies Act \
compliance. You answer ONLY from the policy excerpts provided in the context.

Hard rules:
1. If the answer is not present in the provided excerpts, respond exactly: \
   "I don't see that in the policies I have access to. Escalate to leadership."
2. Always cite the document by its title in brackets, e.g. [Doc 2: Volunteer Handbook].
3. Keep answers under 200 words. Lead with the operative answer; put detail second.
4. If the question asks for legal advice, refuse and recommend the staff speak \
   with the Executive Director or legal counsel.
"""


@dataclass(frozen=True)
class PolicyAnswer:
    answer: str
    citations: list[str]
    usage: LLMResponse


class PolicyNavigator(Agent):
    name = "policy_navigator"

    def ask(self, question: str, *, k: int = 4) -> PolicyAnswer:
        hits = self._retrieve(question, namespace="policies", k=k)
        if not hits:
            # Try the default namespace as a fallback before giving up.
            hits = self._retrieve(question, k=k)

        context = self._format_context(hits)
        (scrubbed_question, scrubbed_context), mapping = self._scrub(question, context)

        user_payload = (
            f"# Staff question\n{scrubbed_question}\n\n"
            f"# Policy excerpts (from this NPO's internal policies, PII-scrubbed)\n"
            f"{scrubbed_context}\n\n"
            f"Answer the staff question using ONLY the excerpts above. "
            f"Cite documents by their bracketed reference, e.g. [Doc 1]."
        )

        response = complete(
            system_prompt=POLICY_NAVIGATOR_SYSTEM,
            persona=self.context.persona,
            user_content=user_payload,
            model=get_settings().model_fast,
            max_tokens=1_024,
            use_thinking=False,  # Haiku doesn't support adaptive thinking
        )
        answer = self._rehydrate(response.text, mapping)
        return PolicyAnswer(
            answer=answer,
            citations=[hit.document.title for hit in hits],
            usage=response,
        )
