"""Accountability — the retention engine and the genuinely *agentic* component.

It runs a goal-directed loop, not a chatbot:
  * GOAL    — each member hits their target weekly cadence.
  * OBSERVE — `fitness.assess()` scores churn risk from the check-in feed.
  * ACT     — for at-risk members who've consented to contact, compose a
              personalized nudge (LLM) and log it; for sustained high risk,
              escalate to a human trainer.

This loop is what justifies a recurring subscription rather than a one-time app
sale: it keeps working between the member's sessions. The owner-facing value is
churn reduction — a lapsing member caught at "medium" is far cheaper to win back
than one who has already cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..config import get_settings
from .. import fitness
from ..fitness import Member, RiskAssessment
from ..llm import LLMResponse, complete
from .base import Agent

ACCOUNTABILITY_SYSTEM = """You write short, warm re-engagement messages for gym \
members who are starting to drift, on behalf of their gym's coaching app.

Rules:
1. One message, 2-3 sentences, friendly and specific. No guilt, no shame, no \
   pressure tactics.
2. Reference a concrete, low-friction next step (a single short session, a class, \
   a favourite lift). Make showing up feel easy.
3. Never mention "churn", "risk score", "at-risk", or any internal metric. The \
   member must never feel surveilled.
4. Member details may appear as placeholders like [NAME_1]; treat them as a real \
   person and don't guess identities.
5. No medical or injury advice. If you don't know specifics, keep it general and \
   encouraging.
"""

# Sustained high risk over this many days with no visit → loop a human in rather
# than keep nudging into the void.
_ESCALATE_DAYS_SINCE = 21


@dataclass(frozen=True)
class Nudge:
    member_id: str
    band: str
    score: float
    message: str
    sent: bool
    escalated: bool
    usage: LLMResponse | None = None


@dataclass(frozen=True)
class SweepResult:
    assessed: int
    nudged: int
    escalated: int
    skipped_no_consent: int
    nudges: list[Nudge]


class Accountability(Agent):
    name = "accountability"

    # ---- scoring (delegates to the pure domain function) -----------------

    def assess(self, member: Member, *, today: date | None = None) -> RiskAssessment:
        return fitness.assess(self.context.tenant, member, today=today)

    # ---- nudge composition -----------------------------------------------

    def compose_nudge(self, member: Member, assessment: RiskAssessment) -> Nudge:
        """Write a re-engagement message for one at-risk member (Haiku)."""
        gap = (
            f"about {assessment.days_since_last} days"
            if assessment.days_since_last is not None
            else "a while"
        )
        (scrubbed_profile,), mapping = self._scrub(
            f"Name: {member.name}\nGoals: {member.goals or 'general fitness'}\n"
            f"Experience: {member.experience}"
        )
        user_payload = (
            f"# Member (PII-scrubbed)\n{scrubbed_profile}\n\n"
            f"# Situation\nIt has been {gap} since their last visit. Their goal is "
            f"{member.target_visits_per_week} sessions a week.\n\n"
            f"Write a single warm nudge to help them get back in this week."
        )
        response = complete(
            system_prompt=ACCOUNTABILITY_SYSTEM,
            persona=self.context.persona,
            user_content=user_payload,
            model=get_settings().model_fast,
            max_tokens=400,
            use_thinking=False,
        )
        message = self._rehydrate(response.text, mapping)
        return Nudge(
            member_id=member.id,
            band=assessment.band,
            score=assessment.score,
            message=message,
            sent=False,
            escalated=False,
            usage=response,
        )

    # ---- the loop ---------------------------------------------------------

    def run_sweep(self, *, today: date | None = None, send: bool = True) -> SweepResult:
        """Assess every member; nudge the at-risk who consented; escalate the worst.

        This is the scheduled job. In production it runs nightly; in the demo a
        button triggers it. It only contacts members who set consent_contact —
        the PIPEDA boundary is enforced here, in code.
        """
        members = fitness.list_members(self.context.tenant)
        nudges: list[Nudge] = []
        nudged = escalated = skipped = 0

        for member in members:
            assessment = self.assess(member, today=today)

            # Escalate sustained high risk to a human regardless of nudging.
            if (
                assessment.days_since_last is not None
                and assessment.days_since_last >= _ESCALATE_DAYS_SINCE
            ):
                fitness.create_escalation(
                    self.context.tenant,
                    member.id,
                    reason="prolonged_absence",
                    detail=(
                        f"No visit in {assessment.days_since_last} days "
                        f"(risk {assessment.score})."
                    ),
                )
                escalated += 1

            if assessment.band == "low":
                continue
            if not member.consent_contact:
                skipped += 1
                continue

            nudge = self.compose_nudge(member, assessment)
            if send:
                fitness.log_nudge(
                    self.context.tenant,
                    member.id,
                    body=nudge.message,
                    risk_at_send=assessment.score,
                )
                nudge = Nudge(
                    member_id=nudge.member_id,
                    band=nudge.band,
                    score=nudge.score,
                    message=nudge.message,
                    sent=True,
                    escalated=False,
                    usage=nudge.usage,
                )
            nudged += 1
            nudges.append(nudge)

        return SweepResult(
            assessed=len(members),
            nudged=nudged,
            escalated=escalated,
            skipped_no_consent=skipped,
            nudges=nudges,
        )
