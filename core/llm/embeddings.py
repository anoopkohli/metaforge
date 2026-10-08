"""Embedding interface.

Invariant: feature code embeds text only through an `Embedder`. Tests and offline evals use
`HashingEmbedder`, which is deterministic and needs no model or network. A provider-backed embedder
can be added behind the same interface later.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have how if in is it its of on or that the this to "
    "was what when where which who why with do does our we you your i".split()
)


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Feature-hashed bag of words (unigrams + bigrams), L2-normalised.

    Lexical, not semantic: good enough to make retrieval behave realistically in tests and evals,
    and fully reproducible. Uses sha256, not `hash()`, so results do not depend on PYTHONHASHSEED.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim < 8:
            raise ValueError("dim must be at least 8")
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOPWORDS]
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text):
                digest = hashlib.sha256(feat.encode()).digest()
                index = int.from_bytes(digest[:4], "big") % self.dim
                vec[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec] if norm else vec)
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
