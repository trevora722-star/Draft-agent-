"""Shared agent harness.

Every agent: takes a Tenant, holds a Vault for retrieval, runs every model
input through the privacy scrubber, then rehydrates names in the model
output before returning.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..personas import persona_text
from ..privacy import scrub
from ..tenancy import Tenant
from ..vault import SearchHit, Vault


@dataclass(frozen=True)
class AgentContext:
    tenant: Tenant
    vault: Vault

    @property
    def persona(self) -> str:
        return persona_text(self.tenant.persona)


class Agent:
    name: str = "agent"

    def __init__(self, tenant: Tenant):
        self.context = AgentContext(tenant=tenant, vault=Vault(tenant))

    # ---- helpers shared across agents -------------------------------------

    def _retrieve(
        self, query: str, *, namespace: str | None = None, k: int = 5
    ) -> list[SearchHit]:
        return self.context.vault.search(query, namespace=namespace, k=k)

    @staticmethod
    def _format_context(hits: list[SearchHit]) -> str:
        if not hits:
            return "(No relevant internal documents were found in the knowledge vault.)"
        lines = []
        for i, hit in enumerate(hits, start=1):
            lines.append(
                f"[Doc {i} | namespace={hit.document.namespace} | score={hit.score:.2f}] "
                f"{hit.document.title}\n{hit.document.body.strip()}"
            )
        return "\n\n---\n\n".join(lines)

    @staticmethod
    def _scrub(*texts: str) -> tuple[list[str], dict[str, str]]:
        """Scrub all inputs in one pass so placeholders are consistent across them."""
        joined = "\f".join(texts)  # form-feed is a safe separator (won't appear in real text)
        result = scrub(joined)
        scrubbed = result.text.split("\f")
        return scrubbed, result.mapping

    @staticmethod
    def _rehydrate(text: str, mapping: dict[str, str]) -> str:
        for placeholder, original in mapping.items():
            text = text.replace(placeholder, original)
        return text
