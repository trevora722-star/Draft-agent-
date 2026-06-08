"""Coach — the member-facing AI personal trainer.

Three jobs:
  * build_program — a structured, equipment-aware training plan from the
    member's profile and the gear that actually exists at their home gym.
  * substitute   — the in-gym "the rack's taken, what do I do?" quick swap.
  * chat         — free-form Q&A grounded in the member's plan + their gym.

Safety is not optional in a fitness product. Two hard rules are enforced in
code, not just prompt text:
  1. Equipment-awareness: the plan is built only from the location's retrieved
     equipment list, so the Coach can't prescribe a machine the gym lacks.
  2. Injury gate: any member message that trips `detect_injury` is escalated to
     a human and the Coach does NOT give training advice on it. This is the
     warm-handoff that keeps the product on the right side of "wellness
     coaching, not medical advice."
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings
from .. import fitness
from ..fitness import Member
from ..llm import LLMResponse, complete
from .base import Agent

COACH_SYSTEM = """You are a certified personal trainer and strength coach inside a \
gym's branded member app. You design and adapt training for everyday gym members.

Hard rules — these are not negotiable:
1. EQUIPMENT: Prescribe ONLY exercises that can be performed with the equipment \
   listed in the provided gym context. If a movement needs gear that isn't listed, \
   pick an alternative that works with what's available. Never assume a machine \
   exists.
2. SCOPE: You give fitness and general wellness coaching, NOT medical advice. You \
   do not diagnose, treat, or rehabilitate injuries. If the member describes pain, \
   injury, dizziness, chest symptoms, or anything clinical, do not coach through \
   it — tell them to stop, rest, and speak with a qualified health professional, \
   and let them know a team member has been notified.
3. PRIVACY: Member details may appear as placeholders like [NAME_1] or [EMAIL_1]. \
   Treat each as a real person; never try to guess the identity behind a placeholder.
4. HONESTY: If the gym context doesn't contain what you'd need (e.g. no equipment \
   listed), say so plainly rather than inventing a fully-kitted gym.

Be specific and practical: sets, reps, rest, and a sensible weekly split matched to \
the member's experience level and stated goals. Always include a brief warm-up and a \
one-line safety note to stop if anything sharp or painful occurs.
"""

# Templated safe response for the injury gate. Deterministic on purpose: when a
# member reports pain we must NOT route to a generative model that might coach
# through it. The persona-voiced empathy is a fair trade for guaranteed safety.
_INJURY_RESPONSE = (
    "It sounds like you might be dealing with pain or an injury — I'm not able to "
    "coach you through that safely. Please stop training the affected area, rest, "
    "and check in with a doctor or physiotherapist before your next session. "
    "I've flagged this to the gym team so a trainer can follow up with you."
)


@dataclass(frozen=True)
class CoachReply:
    text: str
    escalated: bool
    used_llm: bool
    citations: list[str]
    usage: LLMResponse | None = None


@dataclass(frozen=True)
class ProgramResult:
    program_id: str
    body: str
    equipment_docs: list[str]
    usage: LLMResponse


class Coach(Agent):
    name = "coach"

    # ---- equipment-aware retrieval ---------------------------------------

    def _gym_context(self, member: Member, query: str, *, k: int = 8) -> tuple[str, list[str]]:
        """Retrieve the member's home-location equipment + the exercise library."""
        hits = []
        if member.home_location_id:
            hits += self._retrieve(
                query,
                namespace=fitness.location_namespace(member.home_location_id, "equipment"),
                k=k,
            )
        hits += self._retrieve(query, namespace=fitness.EXERCISE_LIBRARY_NS, k=k)
        context = self._format_context(hits)
        return context, [h.document.title for h in hits]

    # ---- program builder --------------------------------------------------

    def build_program(self, member: Member, *, weeks: int = 4) -> ProgramResult:
        """Generate and persist an equipment-aware training plan (Opus + thinking)."""
        query = (
            f"{member.goals or 'general fitness'} {member.experience} "
            f"workout equipment for {member.target_visits_per_week} days per week"
        )
        gym_context, titles = self._gym_context(member, query)

        (scrubbed_profile, scrubbed_gym), mapping = self._scrub(
            member.profile_text(), gym_context
        )

        user_payload = (
            f"# Member profile (PII-scrubbed)\n{scrubbed_profile}\n\n"
            f"# Gym equipment & exercise library available to this member "
            f"(PII-scrubbed)\n{scrubbed_gym}\n\n"
            f"# Task\n"
            f"Design a {weeks}-week training program for this member, using ONLY "
            f"the equipment above. Structure it as:\n"
            f"  1. Overview (goal, weekly split, how to progress)\n"
            f"  2. Each training day: warm-up, main lifts (sets × reps, rest, a "
            f"starting-load cue), accessories, cool-down\n"
            f"  3. Progression plan across the {weeks} weeks\n"
            f"  4. A short 'stop and ask a professional if…' safety note\n"
            f"Use Markdown headings. If the member reported injuries, work AROUND "
            f"them conservatively and add a note to clear it with a professional — "
            f"do not prescribe rehab.\n"
        )

        response = complete(
            system_prompt=COACH_SYSTEM,
            persona=self.context.persona,
            user_content=user_payload,
            max_tokens=16_000,
            use_thinking=True,
            effort="high",
        )
        body = self._rehydrate(response.text, mapping)
        program = fitness.save_program(self.context.tenant, member.id, body)
        return ProgramResult(
            program_id=program.id,
            body=body,
            equipment_docs=titles,
            usage=response,
        )

    # ---- quick in-gym substitution ---------------------------------------

    def substitute(self, member: Member, exercise: str) -> CoachReply:
        """Fast swap for a busy machine (Haiku, no thinking)."""
        gym_context, titles = self._gym_context(member, exercise, k=8)
        (scrubbed_ex, scrubbed_gym), mapping = self._scrub(exercise, gym_context)
        user_payload = (
            f"# Unavailable exercise\n{scrubbed_ex}\n\n"
            f"# Equipment available right now (PII-scrubbed)\n{scrubbed_gym}\n\n"
            f"Suggest 2-3 substitute exercises that hit the same muscles using ONLY "
            f"the available equipment. For each: name, sets × reps, and one form cue. "
            f"Keep it under 150 words — the member is standing in the gym."
        )
        response = complete(
            system_prompt=COACH_SYSTEM,
            persona=self.context.persona,
            user_content=user_payload,
            model=get_settings().model_fast,
            max_tokens=1_024,
            use_thinking=False,
        )
        return CoachReply(
            text=self._rehydrate(response.text, mapping),
            escalated=False,
            used_llm=True,
            citations=titles,
            usage=response,
        )

    # ---- free-form chat ---------------------------------------------------

    def chat(self, member: Member, message: str) -> CoachReply:
        """Answer a member message. Injury language short-circuits to escalation."""
        signal = fitness.detect_injury(message)
        if signal.detected:
            fitness.create_escalation(
                self.context.tenant,
                member.id,
                reason="member_reported_pain",
                detail=f"Triggered by terms {signal.terms} in chat.",
            )
            return CoachReply(
                text=_INJURY_RESPONSE, escalated=True, used_llm=False, citations=[]
            )

        gym_context, titles = self._gym_context(member, message, k=6)
        program = fitness.latest_program(self.context.tenant, member.id)
        program_text = program.body if program else "(No saved program yet.)"

        (scrubbed_msg, scrubbed_gym, scrubbed_prog), mapping = self._scrub(
            message, gym_context, program_text
        )
        user_payload = (
            f"# Member's current program (PII-scrubbed)\n{scrubbed_prog}\n\n"
            f"# Gym equipment & exercises available (PII-scrubbed)\n{scrubbed_gym}\n\n"
            f"# Member's message\n{scrubbed_msg}\n\n"
            f"Answer as their coach, grounded in their program and the equipment "
            f"above. Stay within fitness/wellness coaching."
        )
        response = complete(
            system_prompt=COACH_SYSTEM,
            persona=self.context.persona,
            user_content=user_payload,
            model=get_settings().model_fast,
            max_tokens=1_500,
            use_thinking=False,
        )
        return CoachReply(
            text=self._rehydrate(response.text, mapping),
            escalated=False,
            used_llm=True,
            citations=titles,
            usage=response,
        )
