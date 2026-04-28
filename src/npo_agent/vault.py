"""Per-tenant Knowledge Vault.

Each NPO gets a logically-isolated namespace. Documents are stored in SQLite
with the tenant_id as a hard filter; the retrieval layer never sees another
tenant's corpus.

Two retrievers ship in this module:

  - TF-IDF (sklearn) — the default when sklearn is installed. Better quality
    on corpora with overlapping vocabulary; ~200MB of dependencies.
  - Keyword overlap — a no-dep fallback when sklearn isn't available. Good
    enough for a few hundred documents and small corpora; this is what runs
    on serverless platforms (Netlify Functions) where the sklearn bundle
    busts the 250MB function-size limit.

For production, swap either retriever for an embedding-backed store (voyage-3 /
Cohere / OpenAI) — keep the `Vault.search` signature and nothing else changes.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass

try:  # pragma: no cover — exercised by integration; tests use the sklearn path
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

from .db import connect
from .tenancy import Tenant

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "to", "was", "were",
    "will", "with", "this", "these", "those", "we", "you", "your", "our", "they",
    "them", "their", "but", "not", "do", "does", "did", "so", "if", "than", "then",
}


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS]


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
        if _HAS_SKLEARN:
            return self._tfidf_search(docs, query, k=k, min_score=min_score)
        return self._keyword_search(docs, query, k=k, min_score=min_score)

    def _tfidf_search(
        self, docs: list[Document], query: str, *, k: int, min_score: float
    ) -> list[SearchHit]:
        corpus = [f"{d.title}\n\n{d.body}" for d in docs]
        try:
            vectorizer = TfidfVectorizer(
                lowercase=True, stop_words="english", ngram_range=(1, 2),
                max_features=20_000,
            )
            doc_matrix = vectorizer.fit_transform(corpus)
            query_vec = vectorizer.transform([query])
        except ValueError:
            return []
        sims = cosine_similarity(query_vec, doc_matrix).ravel()
        top_indices = np.argsort(-sims)[:k]
        return [
            SearchHit(document=docs[i], score=float(sims[i]))
            for i in top_indices
            if sims[i] >= min_score
        ]

    def _keyword_search(
        self, docs: list[Document], query: str, *, k: int, min_score: float
    ) -> list[SearchHit]:
        """No-dep retriever: token-overlap with light IDF weighting.

        For demo-scale corpora (a few hundred small documents) this is
        comparable to TF-IDF and adds zero deps to the deployment bundle.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # Build per-doc token counts and a doc-frequency table.
        doc_tokens: list[Counter] = []
        df: Counter = Counter()
        for d in docs:
            tokens = _tokenize(f"{d.title} {d.title} {d.body}")  # title weighted 2x
            counts = Counter(tokens)
            doc_tokens.append(counts)
            for term in counts:
                df[term] += 1

        n_docs = len(docs)
        scored: list[tuple[float, Document]] = []
        for doc, counts in zip(docs, doc_tokens):
            total = sum(counts.values()) or 1
            score = 0.0
            for q in query_tokens:
                if q in counts:
                    # IDF-like weight: rare terms count more.
                    idf = 1.0 + (n_docs / (1 + df[q]))
                    score += (counts[q] / total) * idf
            scored.append((score, doc))

        scored.sort(key=lambda t: -t[0])
        # Normalize the top score so min_score has a consistent meaning.
        top_score = scored[0][0] if scored else 0.0
        if top_score == 0:
            return []
        results = []
        for score, doc in scored[:k]:
            normalized = score / top_score
            if normalized >= min_score:
                results.append(SearchHit(document=doc, score=normalized))
        return results
