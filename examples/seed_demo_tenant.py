"""Seed a demo BCSS-style tenant with realistic program + policy docs.

Run:
    python -m examples.seed_demo_tenant

Prints the tenant's API key so you can hit the endpoints with it.
"""

from __future__ import annotations

from npo_agent.db import init_db
from npo_agent.tenancy import create_tenant
from npo_agent.vault import Vault

PROGRAM_DOCS = [
    (
        "24/7 Crisis Line",
        "Our crisis line takes ~3,000 calls/year across BC. Trained volunteer "
        "responders complete 60 hours of training. Average call duration is 18 "
        "minutes. We track outcomes via a post-call disposition code.",
    ),
    (
        "Family Support Groups",
        "Eight peer-led family support groups meet weekly across the Lower "
        "Mainland and Fraser Valley. ~400 family members attend annually.",
    ),
    (
        "Annual Outcomes 2024",
        "84% of family-support participants reported reduced caregiver burden "
        "after 12 weeks (UCLA Loneliness Scale). Crisis-line callers reporting "
        "active risk dropped from 22% on first call to 4% on follow-up.",
    ),
]

POLICY_DOCS = [
    (
        "Volunteer Onboarding Policy",
        "All new volunteers must complete WHMIS, Privacy Awareness, and Crisis "
        "De-escalation training within 30 days of their start date. Background "
        "checks are renewed every 3 years.",
    ),
    (
        "Privacy & PIPA Compliance Manual",
        "Client PII (names, contact, health information) must be stored only in "
        "the case management system. Email containing PII must be encrypted. "
        "Any breach must be reported to the Privacy Officer within 24 hours.",
    ),
    (
        "BC Societies Act Reporting",
        "Annual reports to the BC Registrar are due within 60 days of the AGM. "
        "Director changes must be filed within 15 days.",
    ),
]


def main() -> None:
    init_db()
    tenant, api_key = create_tenant("BCSS Demo Org", persona="clinical-empathetic")
    vault = Vault(tenant)

    for title, body in PROGRAM_DOCS:
        vault.add(title=title, body=body, namespace="programs")
    for title, body in POLICY_DOCS:
        vault.add(title=title, body=body, namespace="policies")

    print("Demo tenant created.")
    print(f"  tenant_id: {tenant.id}")
    print(f"  name:      {tenant.name}")
    print(f"  persona:   {tenant.persona}")
    print(f"  api_key:   {api_key}")
    print()
    print("Try it:")
    print(
        f'  curl -s -X POST http://localhost:8000/v1/agents/policy-navigator/ask '
        f'-H "X-API-Key: {api_key}" -H "Content-Type: application/json" '
        f'-d \'{{"question":"When do volunteers need WHMIS training?"}}\''
    )


if __name__ == "__main__":
    main()
