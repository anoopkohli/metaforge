"""Stage-3 RAG suite: tenant questions answered from tenant documents, scored against known facts.

- `replay` (offline, CI): the model is an extractive FakeLLM that answers with the top retrieved
  chunk and cites it, so the score measures the offline hashing embedder's retrieval end to end.
- `live`: a real model answers through `LLMClient`; the same checks apply.

Every case is also a leak check: a retrieved chunk from another tenant, or another tenant's canary
in the answer, scores 0 regardless of anything else.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from core.llm.base import FakeLLM, LLMClient, Message
from core.llm.embeddings import HashingEmbedder
from core.store.sqlite_store import MetadataStore
from core.synthetic import seed_demo, synthetic_documents
from evals.runner import EvalCase
from rag import RagService, SiloIndex

HERE = Path(__file__).parent
DATASET = HERE / "datasets" / "rag.jsonl"
BASELINE = HERE / "baselines" / "rag.json"
K = 5

_DOC = re.compile(r'<document id="([^"]+)"[^>]*>\n(.*?)\n</document>', re.DOTALL)


def extractive_llm() -> FakeLLM:
    def script(messages: Sequence[Message]) -> str:
        m = _DOC.search(messages[0].content)
        if not m:
            return json.dumps({"answer": "I could not find this.", "citations": []})
        return json.dumps({"answer": m.group(2), "citations": [m.group(1)]})

    return FakeLLM(script)


def build(
    mode: str = "replay", llm_factory: Callable[[], LLMClient] | None = None
) -> Callable[[EvalCase], dict]:
    if mode not in ("replay", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    tenants = seed_demo(MetadataStore(":memory:"), n_tenants=5, seed=7)
    llm = extractive_llm() if mode == "replay" else llm_factory()  # type: ignore[misc]
    service = RagService(SiloIndex(), HashingEmbedder(), llm)
    facts: dict[str, dict[str, dict[str, str]]] = {}
    for tenant in tenants:
        facts[tenant.tenant_id] = {}
        for doc in synthetic_documents(tenant):
            service.ingest(
                tenant.context(), {"doc_id": doc.doc_id, "title": doc.title, "text": doc.text}
            )
            facts[tenant.tenant_id][doc.doc_id] = doc.facts

    def fn(case: EvalCase) -> dict:
        tenant = tenants[case.input["tenant_index"]]
        answer = service.answer(tenant.context(), case.input["question"], k=K)
        foreign = [t.canary for t in tenants if t.tenant_id != tenant.tenant_id]
        return {
            "mode": mode,
            "question": case.input["question"],
            "tenant_id": tenant.tenant_id,
            "answer": answer.text,
            "citations": answer.citations,
            "retrieved_docs": [h.chunk.doc_id for h in answer.hits],
            "hit_tenants": sorted({h.chunk.tenant_id for h in answer.hits}),
            "foreign_canary_in_answer": any(c in answer.text for c in foreign),
            "fact_value": facts[tenant.tenant_id][case.expected["doc"]][case.expected["fact"]],
            "usage": {
                "input_tokens": answer.usage.input_tokens,
                "output_tokens": answer.usage.output_tokens,
            },
        }

    return fn


def rag_score(output: dict, case: EvalCase) -> float:
    """Leak = 0. Otherwise the mean of: retrieved (recall@k), cited, fact in answer."""
    if (
        output["hit_tenants"] not in ([], [output["tenant_id"]])
        or output["foreign_canary_in_answer"]
    ):
        return 0.0
    doc = case.expected["doc"]
    checks = [
        doc in output["retrieved_docs"],
        any(c.split("#")[0] == doc for c in output["citations"]),
        output["fact_value"] in output["answer"],
    ]
    return sum(checks) / len(checks)


def live_scorer(judge_llm: LLMClient) -> Callable[[dict, EvalCase], float]:
    """Live-mode scorer: deterministic leak gate first, then retrieval and citation checks, then the
    judge decides whether the answer states the fact (wording varies in live answers)."""
    from evals.judge import judge

    def score(output: dict, case: EvalCase) -> float:
        if output["hit_tenants"] not in ([], [output["tenant_id"]]):
            return 0.0
        if output["foreign_canary_in_answer"]:
            return 0.0
        doc = case.expected["doc"]
        reference = f"{case.expected['fact']}: {output['fact_value']}"
        verdict = judge(judge_llm, output["question"], reference, output["answer"])
        checks = [
            float(doc in output["retrieved_docs"]),
            float(any(c.split("#")[0] == doc for c in output["citations"])),
            verdict.score,
        ]
        return sum(checks) / len(checks)

    return score
