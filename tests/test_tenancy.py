"""Tenant isolation is the single most important invariant in this codebase.

If any test in this file regresses, do not ship.
"""

from npo_agent.tenancy import authenticate, create_tenant, get_tenant
from npo_agent.vault import Vault


def test_create_and_authenticate_roundtrip(isolated_db):
    tenant, api_key = create_tenant("BCSS", persona="clinical-empathetic")
    fetched = authenticate(api_key)
    assert fetched is not None
    assert fetched.id == tenant.id
    assert fetched.name == "BCSS"
    assert fetched.persona == "clinical-empathetic"


def test_authenticate_rejects_wrong_key(isolated_db):
    create_tenant("BCSS")
    assert authenticate("wrong-key") is None
    assert authenticate("") is None


def test_namespaces_are_per_tenant(isolated_db):
    bcss, _ = create_tenant("BCSS")
    foodbank, _ = create_tenant("Greater Vancouver Food Bank")
    assert bcss.namespace != foodbank.namespace
    assert bcss.namespace.startswith("tenant:")


def test_vault_isolation_documents(isolated_db):
    """The whole pitch hinges on this: NPO A cannot see NPO B's documents."""
    bcss, _ = create_tenant("BCSS")
    foodbank, _ = create_tenant("Greater Vancouver Food Bank")

    Vault(bcss).add(title="Crisis Protocol", body="Internal BCSS crisis line procedure.")
    Vault(foodbank).add(title="Volunteer Handbook", body="Food bank volunteer manual.")

    bcss_docs = Vault(bcss).list()
    foodbank_docs = Vault(foodbank).list()

    assert len(bcss_docs) == 1
    assert len(foodbank_docs) == 1
    assert bcss_docs[0].title == "Crisis Protocol"
    assert foodbank_docs[0].title == "Volunteer Handbook"

    # Cross-tenant search must return nothing — an LLM-emitted query against
    # the wrong vault must not surface another tenant's content.
    bcss_hits = Vault(bcss).search("volunteer manual")
    for hit in bcss_hits:
        assert "Food bank" not in hit.document.body


def test_vault_namespace_filtering(isolated_db):
    bcss, _ = create_tenant("BCSS")
    vault = Vault(bcss)
    vault.add(title="Suicide Risk Protocol", body="Clinical risk assessment.", namespace="policies")
    vault.add(title="Annual Report 2024", body="Outcomes summary.", namespace="programs")

    policies = vault.list(namespace="policies")
    programs = vault.list(namespace="programs")
    assert len(policies) == 1 and policies[0].title == "Suicide Risk Protocol"
    assert len(programs) == 1 and programs[0].title == "Annual Report 2024"


def test_get_tenant_returns_none_for_unknown_id(isolated_db):
    assert get_tenant("nonexistent") is None
