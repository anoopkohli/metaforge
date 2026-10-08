"""Shared RAG types, limits, chunking and the naive-mode switch.

Invariant: a `Chunk` always records the tenant it was stored for. The service's output guard
compares that field to the caller's `TenantContext` before any chunk reaches a prompt, a cache or a
response.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("metaforge.rag")

DOC_ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"
MAX_DOC_CHARS = 50_000
MAX_CHUNKS = 200
MAX_QUESTION_CHARS = 1_000
MAX_K = 20
CHUNK_WORDS = 80

NAIVE_ENV = "METAFORGE_UNSAFE_NAIVE_RAG"
NAIVE_VALUE = "disable-tenant-filters"


def naive_rag_enabled() -> bool:
    return os.environ.get(NAIVE_ENV) == NAIVE_VALUE


def require_naive(component: str) -> None:
    """Called by every component asked to run naive. Refuses unless the exact env value is set."""
    if not naive_rag_enabled():
        raise RuntimeError(f"{component}: naive mode needs {NAIVE_ENV}={NAIVE_VALUE}")
    log.warning("NAIVE RAG MODE in %s: tenant filters are disabled. Never deploy this.", component)


class DocumentIn(BaseModel):
    """Ingest payload. There is no tenant field: the tenant comes from the caller's context."""

    model_config = ConfigDict(extra="forbid", strict=True)

    doc_id: str = Field(pattern=DOC_ID)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_DOC_CHARS)


@dataclass(frozen=True)
class Chunk:
    tenant_id: str
    doc_id: str
    ordinal: int
    title: str
    text: str

    @property
    def chunk_id(self) -> str:
        return f"{self.doc_id}#{self.ordinal}"


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def chunk_text(text: str, max_words: int = CHUNK_WORDS) -> list[str]:
    """Split on blank lines, then split any paragraph longer than `max_words` into windows."""
    chunks: list[str] = []
    for para in (p.strip() for p in text.split("\n\n")):
        words = para.split()
        for start in range(0, len(words), max_words):
            chunks.append(" ".join(words[start : start + max_words]))
    return [c for c in chunks if c]
