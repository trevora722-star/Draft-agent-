"""Per-tenant Knowledge Vault.

Each NPO gets a logically-isolated namespace. Documents are stored in SQLite
with the tenant_id as a hard filter; the retrieval layer fits a TF-IDF index
per tenant on demand and never sees another tenant's corpus.

For production, swap the TF-IDF retriever for an embedding-backed store
(voyage-3 / Cohere / OpenAI) — keep the same `Vault.search` signature and
nothing else has to change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .db import connect
from .tenancy import Tenant


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    body: str
    namespace: str

    def snippet(self, length: int = 320) -> str:
        body = self.body.strip().replace("\n", " ")
        return body if len(body) <= length else body[: length - 1] + "…"


@dataclass(frozen=True)
class SearchHit:
    document: Document
    score: float


class Vault:
    """Tenant-scoped retrieval. Hold one of these per request."""

    def __init__(self, tenant: Tenant):
        self._tenant = tenant

    @property
    def namespace(self) -> str:
        return self._tenant.namespace

    # ---- ingest -----------------------------------------------------------

    def add(self, title: str, body: str, namespace: str = "default") -> Document:
        doc_id = uuid.uuid4().hex
        with connect() as conn:
            conn.execute(
                "INSERT INTO documents (id, tenant_id, namespace, title, body) "
                "VALUES (?, ?, ?, ?, ?)",
                (doc_id, self._tenant.id, namespace, title, body),
            )
        return Document(id=doc_id, title=title, body=body, namespace=namespace)

    def list(self, namespace: str | None = None) -> list[Document]:
        with connect() as conn:
            if namespace is None:
                rows = conn.execute(
                    "SELECT id, namespace, title, body FROM documents "
                    "WHERE tenant_id = ? ORDER BY created_at DESC",
                    (self._tenant.id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, namespace, title, body FROM documents "
                    "WHERE tenant_id = ? AND namespace = ? ORDER BY created_at DESC",
                    (self._tenant.id, namespace),
                ).fetchall()
        return [
            Document(id=r["id"], title=r["title"], body=r["body"], namespace=r["namespace"])
            for r in rows
        ]

    # ---- retrieval --------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        namespace: str | None = None,
        k: int = 5,
        min_score: float = 0.05,
    ) -> list[SearchHit]:
        """Return top-k tenant-scoped documents matching the query.

        Always filters by tenant_id at the SQL boundary so even a buggy
        retriever can't reach across tenants.
        """
        docs = self.list(namespace=namespace)
        if not docs:
            return []

        # Fit per-request — for the MVP corpus sizes (hundreds of docs) this is
        # fast enough and means we never hold a stale index. Production: cache
        # per-tenant vectorizers in Redis with invalidation on add/delete.
        corpus = [f"{d.title}\n\n{d.body}" for d in docs]
        try:
            vectorizer = TfidfVectorizer(
                lowercase=True,
                stop_words="english",
                ngram_range=(1, 2),
                max_features=20_000,
            )
            doc_matrix = vectorizer.fit_transform(corpus)
            query_vec = vectorizer.transform([query])
        except ValueError:
            # Corpus has no usable terms (all stop words, etc.).
            return []

        sims = cosine_similarity(query_vec, doc_matrix).ravel()
        top_indices = np.argsort(-sims)[:k]
        return [
            SearchHit(document=docs[i], score=float(sims[i]))
            for i in top_indices
            if sims[i] >= min_score
        ]
