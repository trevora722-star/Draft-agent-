"""Seed a realistic FitCoach demo tenant for a multi-location gym walkthrough.

Creates one ownership-group tenant ("Anytime Coach Demo"), one fully-equipped
location, an exercise library, and a roster of members with synthetic check-in
histories spanning the risk spectrum — from a consistent regular to a member who
has never badged in. The risk scoring and the owner dashboard work with no
Anthropic key; only program generation, chat, and nudges call the model.

Run it via `npo-agent seed-fitness-demo`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import fitness
from .tenancy import Tenant, create_tenant
from .vault import Vault

DEMO_TENANT_NAME = "Anytime Coach Demo"

_EQUIPMENT = """Free weights: adjustable dumbbells 2.5–50kg, Olympic barbells, bumper \
plates, EZ-curl bar, fixed-weight kettlebells 8–32kg. Racks: 3 squat racks with \
safeties, 1 flat bench, 2 adjustable benches. Machines: leg press, lat pulldown, \
seated cable row, cable crossover (dual adjustable pulleys), leg curl, leg \
extension, Smith machine, pec deck. Cardio: 6 treadmills, 4 rowers, 3 assault \
bikes, 2 ellipticals. Accessories: resistance bands, pull-up bar, dip station, \
medicine balls, foam rollers, TRX straps. (No leg-press calf block; no hack squat \
machine.)"""

_EXERCISES = [
    ("Lower body — squat pattern",
     "Back squat, goblet squat, leg press, Bulgarian split squat (dumbbell), "
     "walking lunge, leg extension. Cues: brace, control the descent, knees track toes."),
    ("Lower body — hinge pattern",
     "Romanian deadlift (barbell or dumbbell), conventional deadlift, kettlebell "
     "swing, hip thrust, seated leg curl, back extension. Cues: hinge at hips, neutral spine."),
    ("Upper body — horizontal push",
     "Barbell bench press, dumbbell bench press, push-up, pec deck, cable crossover. "
     "Cues: shoulder blades retracted, elbows ~45°."),
    ("Upper body — horizontal pull",
     "Seated cable row, dumbbell row, chest-supported row, TRX row. Cues: pull to "
     "lower ribs, squeeze shoulder blades."),
    ("Upper body — vertical push/pull",
     "Overhead dumbbell press, lat pulldown, pull-up (assisted with band), dip "
     "(dip station). Cues: ribs down on press, full range on pulldown."),
    ("Conditioning",
     "Rower intervals, assault bike sprints, treadmill incline walk, kettlebell "
     "circuits. Cues: pick a pace you can repeat."),
]


@dataclass(frozen=True)
class DemoMember:
    name: str
    goals: str
    experience: str
    target: int
    consent_contact: bool
    # check-in offsets in days-ago; empty list = never visited
    visit_days_ago: list[int]
    injuries: str | None = None


def _spread(weeks: int, per_week: int, *, start_days_ago: int = 0) -> list[int]:
    """Generate days-ago offsets for `per_week` visits across `weeks`, recent first."""
    out: list[int] = []
    for w in range(weeks):
        base = start_days_ago + w * 7
        # space visits within the week (e.g. Mon/Wed/Fri → +0/+2/+4)
        for i in range(per_week):
            out.append(base + i * 2)
    return out


_DEMO_MEMBERS = [
    DemoMember(
        name="Jordan Reyes", goals="build strength, squat 1.5x bodyweight",
        experience="intermediate", target=3, consent_contact=True,
        visit_days_ago=_spread(4, 3),  # steady 3x/week → low risk
    ),
    DemoMember(
        name="Priya Anand", goals="general fitness and energy",
        experience="beginner", target=3, consent_contact=True,
        visit_days_ago=_spread(2, 3, start_days_ago=8),  # regular then dropped ~8d → medium
    ),
    DemoMember(
        name="Marcus Lee", goals="lose 10kg, improve conditioning",
        experience="beginner", target=4, consent_contact=True,
        visit_days_ago=[24, 26, 28, 31],  # ghosted ~24d ago → high + escalation
    ),
    DemoMember(
        name="Sofia Nilsson", goals="tone up before summer",
        experience="beginner", target=3, consent_contact=False,
        visit_days_ago=[],  # signed up, never came → high, but no contact consent
    ),
    DemoMember(
        name="Dev Patel", goals="weekend training around shift work",
        experience="intermediate", target=2, consent_contact=True,
        visit_days_ago=_spread(4, 1),  # 1x/week vs target 2 → medium
    ),
    DemoMember(
        name="Hannah Brooks", goals="return to lifting post-baby, careful with back",
        experience="returning", target=3, consent_contact=True,
        visit_days_ago=_spread(4, 3, start_days_ago=1),  # consistent → low
        injuries="mild lower-back stiffness (cleared by physio 6 months ago)",
    ),
]


def seed_fitness_demo() -> dict:
    """Create the demo tenant + location + members. Idempotent-ish: each call
    mints a fresh tenant (new API key) so demos don't collide."""
    tenant, api_key = create_tenant(DEMO_TENANT_NAME, persona="coach-hype")

    location = fitness.create_location(
        tenant,
        "Anytime Fitness — Kelowna (Rutland)",
        address="Rutland Rd N, Kelowna, BC",
    )

    vault = Vault(tenant)
    vault.add(
        title="Rutland location equipment list",
        body=_EQUIPMENT,
        namespace=fitness.location_namespace(location.id, "equipment"),
    )
    for title, body in _EXERCISES:
        vault.add(title=title, body=body, namespace=fitness.EXERCISE_LIBRARY_NS)
    vault.add(
        title="Member policies",
        body=(
            "24/7 keyfob access. Membership freeze up to 3 months/year with notice. "
            "Guest passes: members may bring a guest twice per month. Cancellation "
            "requires 30 days written notice."
        ),
        namespace=fitness.location_namespace(location.id, "policies"),
    )

    today = date.today()
    members = []
    for dm in _DEMO_MEMBERS:
        member = fitness.create_member(
            tenant,
            dm.name,
            home_location_id=location.id,
            goals=dm.goals,
            experience=dm.experience,
            injuries=dm.injuries,
            target_visits_per_week=dm.target,
            consent_contact=dm.consent_contact,
        )
        timestamps = [(today - timedelta(days=d)).isoformat() for d in dm.visit_days_ago]
        fitness.record_checkins(tenant, member.id, timestamps, location_id=location.id)
        members.append(member)

    return {
        "tenant_id": tenant.id,
        "api_key": api_key,
        "location_id": location.id,
        "members": [{"id": m.id, "name": m.name} for m in members],
    }
