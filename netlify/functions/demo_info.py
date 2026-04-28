"""GET /api/demo/info — tenant metadata + doc counts for the page header."""

from _shared import ok, safe_handler  # type: ignore[import-not-found]


@safe_handler
def handler(event, context):
    from npo_agent.config import get_settings
    from npo_agent.demo import _demo_tenant
    from npo_agent.vault import Vault

    settings = get_settings()
    tenant = _demo_tenant()
    vault = Vault(tenant)
    return ok({
        "tenant_name": tenant.name,
        "persona": tenant.persona,
        "program_doc_count": len(vault.list(namespace="programs")),
        "policy_doc_count": len(vault.list(namespace="policies")),
        "model_default": settings.model_fast,  # demo runs on Haiku
        "data_residency": settings.data_residency,
    })
