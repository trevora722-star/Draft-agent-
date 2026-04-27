from npo_agent.tenancy import create_tenant
from npo_agent.vault import Vault


def test_search_returns_relevant_doc(isolated_db):
    tenant, _ = create_tenant("BCSS")
    vault = Vault(tenant)
    vault.add(
        title="Volunteer Onboarding",
        body="All new volunteers must complete crisis line training.",
        namespace="policies",
    )
    vault.add(
        title="Annual Budget",
        body="Operating budget figures for fiscal year 2024.",
        namespace="programs",
    )

    hits = vault.search("crisis line training for volunteers", k=5)
    assert hits, "expected at least one search hit"
    assert hits[0].document.title == "Volunteer Onboarding"


def test_search_respects_namespace(isolated_db):
    tenant, _ = create_tenant("BCSS")
    vault = Vault(tenant)
    vault.add(title="Crisis Protocol", body="Crisis intervention details.", namespace="policies")
    vault.add(title="Crisis Outcomes 2024", body="Crisis program outcomes.", namespace="programs")

    policy_hits = vault.search("crisis", namespace="policies")
    assert all(hit.document.namespace == "policies" for hit in policy_hits)


def test_search_empty_corpus(isolated_db):
    tenant, _ = create_tenant("BCSS")
    assert Vault(tenant).search("anything") == []
