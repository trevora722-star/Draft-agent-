"""Tenant model + API-key authentication.

Multi-tenancy is enforced two ways:
  1. Every persisted record (documents, drafts) carries a tenant_id, and every
     query filters by it. There is no cross-tenant accessor.
  2. The vector vault stores embeddings under a per-tenant namespace key, so
     even an LLM hallucinating a doc ID can't reach another tenant's data.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from .db import connect
from .personas import DEFAULT_PERSONA


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    persona: str

    @property
    def namespace(self) -> str:
        # Per-tenant namespace key for the vault — derived from the immutable id,
        # not the human-friendly name. Renaming the org won't fragment the index.
        return f"tenant:{self.id}"


def create_tenant(
    name: str, persona: str = DEFAULT_PERSONA, *, api_key: str | None = None
) -> tuple[Tenant, str]:
    """Create a tenant. Returns (tenant, plaintext_api_key).

    The plaintext key is shown to the admin once; only its hash is stored.
    A specific `api_key` may be supplied (used by demo bootstrap so a one-link
    deploy can hand the browser a known key); otherwise one is generated.
    """
    tenant_id = uuid.uuid4().hex
    if api_key is None:
        api_key = f"npo_{secrets.token_urlsafe(32)}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO tenants (id, name, api_key_hash, persona) VALUES (?, ?, ?, ?)",
            (tenant_id, name, _hash_key(api_key), persona),
        )
    return Tenant(id=tenant_id, name=name, persona=persona), api_key


def authenticate(api_key: str) -> Tenant | None:
    if not api_key:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, persona FROM tenants WHERE api_key_hash = ?",
            (_hash_key(api_key),),
        ).fetchone()
    if row is None:
        return None
    return Tenant(id=row["id"], name=row["name"], persona=row["persona"])


def get_tenant(tenant_id: str) -> Tenant | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, persona FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
    if row is None:
        return None
    return Tenant(id=row["id"], name=row["name"], persona=row["persona"])
