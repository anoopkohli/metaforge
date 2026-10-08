"""Stage 3: multi-tenant RAG with two isolation strategies (pooled Postgres + RLS, siloed SQLite).

Invariant: a tenant's question is answered only from that tenant's chunks. Isolation is layered:
application filter, database enforcement (RLS, or a separate database per tenant), and an output
guard that drops any foreign chunk before it reaches a prompt, a cache or a response.
"""

from rag.cache import AnswerCache
from rag.models import (
    MAX_DOC_CHARS,
    MAX_K,
    MAX_QUESTION_CHARS,
    NAIVE_ENV,
    NAIVE_VALUE,
    Chunk,
    DocumentIn,
    Hit,
    chunk_text,
    naive_rag_enabled,
)
from rag.service import Answer, RagError, RagService
from rag.silo import SiloIndex

__all__ = [
    "MAX_DOC_CHARS",
    "MAX_K",
    "MAX_QUESTION_CHARS",
    "NAIVE_ENV",
    "NAIVE_VALUE",
    "Answer",
    "AnswerCache",
    "Chunk",
    "DocumentIn",
    "Hit",
    "RagError",
    "RagService",
    "SiloIndex",
    "chunk_text",
    "naive_rag_enabled",
]
