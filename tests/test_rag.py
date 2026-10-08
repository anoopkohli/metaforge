"""Stage 3 RAG service on the siloed index (offline): threat-model entries 3.x that are not attacks."""

import json
import logging

import pytest
from conftest import first_doc_llm

from core.llm import FakeLLM
from core.llm.embeddings import HashingEmbedder, cosine
from core.synthetic import synthetic_documents
from core.tenancy import MissingTenantContext
from rag import (
    MAX_DOC_CHARS,
    MAX_K,
    MAX_QUESTION_CHARS,
    NAIVE_ENV,
    NAIVE_VALUE,
    AnswerCache,
    Chunk,
    Hit,
    RagError,
    RagService,
    SiloIndex,
    chunk_text,
    naive_rag_enabled,
)


def answer_json(answer: str, citations: list[str]) -> str:
    return json.dumps({"answer": answer, "citations": citations})


@pytest.fixture
def rag(seeded):
    _, tenants = seeded
    service = RagService(SiloIndex(), HashingEmbedder(), first_doc_llm(), cache=AnswerCache())
    for tenant in tenants[:2]:
        for doc in synthetic_documents(tenant):
            service.ingest(
                tenant.context(), {"doc_id": doc.doc_id, "title": doc.title, "text": doc.text}
            )
    yield service, tenants
    service.close()


# ---- embedder and chunking --------------------------------------------------------------------


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder(dim=64)
    a, b = e.embed(["expense approval limit", "expense approval limit"])
    assert a == b and abs(sum(x * x for x in a) - 1.0) < 1e-9
    near, far = e.embed(["approval limit for expenses", "laptop buddy program"])
    assert cosine(a, near) > cosine(a, far)


def test_chunking_splits_long_text_and_keeps_paragraphs():
    text = "\n\n".join(" ".join(f"w{p}x{i}" for i in range(50)) for p in range(4))
    chunks = chunk_text(text, max_words=80)
    assert len(chunks) == 4 and all(len(c.split()) <= 80 for c in chunks)
    long_para = " ".join(f"w{i}" for i in range(200))
    assert len(chunk_text(long_para, max_words=80)) == 3


# ---- retrieval and answers --------------------------------------------------------------------


def test_answer_uses_own_documents_and_cites_them(rag):
    service, tenants = rag
    me = tenants[0]
    facts = {d.doc_id: d.facts for d in synthetic_documents(me)}
    answer = service.answer(me.context(), "What is the expense approval limit for expense reports?")
    assert answer.citations and answer.citations[0].startswith("expense_policy#")
    assert facts["expense_policy"]["expense_limit"] in answer.text
    assert all(h.chunk.tenant_id == me.tenant_id for h in answer.hits)


def test_citations_must_come_from_retrieved_chunks(rag):
    service, tenants = rag
    me = tenants[0]
    bad = answer_json("made up", ["cinder_private#0"])
    llm = FakeLLM([bad, bad, bad])
    service.llm = llm
    with pytest.raises(RagError, match="valid answer"):
        service.answer(me.context(), "expense approval limit", k=3)
    assert len(llm.calls) == 3  # bounded retries, each with feedback
    assert "cinder_private#0" in llm.calls[1]["messages"][-1].content


def test_invalid_answer_is_repaired(rag):
    service, tenants = rag
    me = tenants[0]
    hits = service.retrieve(me.context(), "expense approval limit", k=3)
    good = answer_json("ok", [hits[0].chunk.chunk_id])
    service.llm = FakeLLM(['{"answer": "x", "citations": [], "extra": 1}', good])
    answer = service.answer(me.context(), "expense approval limit", k=3)
    assert answer.text == "ok" and answer.attempts == 2


def test_no_hits_means_no_model_call(seeded):
    _, tenants = seeded
    llm = FakeLLM([answer_json("x", [])])
    service = RagService(SiloIndex(), HashingEmbedder(), llm)
    answer = service.answer(tenants[0].context(), "anything at all")
    assert answer.citations == [] and llm.calls == []


def test_reingest_replaces_chunks(rag):
    service, tenants = rag
    me = tenants[0].context()
    service.ingest(
        me, {"doc_id": "onboarding", "title": "New", "text": "Brand new onboarding text."}
    )
    hits = service.retrieve(me, "laptop buddy program onboarding", k=10)
    onboarding = [h for h in hits if h.chunk.doc_id == "onboarding"]
    assert len(onboarding) == 1 and "Brand new" in onboarding[0].chunk.text


def test_delete_not_found_message_is_uniform(rag):
    service, tenants = rag
    me, other = tenants[0], tenants[1]
    service.ingest(other.context(), {"doc_id": "only_theirs", "title": "t", "text": "their text"})
    msgs = []
    for doc_id in ("only_theirs", "zz_missing"):
        with pytest.raises(RagError) as exc:
            service.delete(me.context(), doc_id)
        msgs.append(str(exc.value).replace(doc_id, "<id>"))
    assert msgs[0] == msgs[1]


def test_ingest_rejects_tenant_and_unknown_keys(rag):
    service, tenants = rag
    for extra in ({"tenant_id": "cinder"}, {"tenant": "cinder"}, {"owner": "x"}):
        with pytest.raises(RagError):
            service.ingest(
                tenants[0].context(), {"doc_id": "d", "title": "t", "text": "x", **extra}
            )


def test_limits(rag):
    service, tenants = rag
    ctx = tenants[0].context()
    with pytest.raises(RagError):
        service.ingest(
            ctx, {"doc_id": "big", "title": "t", "text": "x " * (MAX_DOC_CHARS // 2 + 1)}
        )
    with pytest.raises(RagError):
        service.retrieve(ctx, "q" * (MAX_QUESTION_CHARS + 1))
    with pytest.raises(RagError):
        service.retrieve(ctx, "expense", k=MAX_K + 1)
    with pytest.raises(RagError):
        service.retrieve(ctx, "   ")


def test_requires_tenant_context(rag):
    service, _ = rag
    with pytest.raises(MissingTenantContext):
        service.retrieve(None, "expense")
    with pytest.raises(MissingTenantContext):
        service.ingest(None, {"doc_id": "d", "title": "t", "text": "x"})


# ---- output guard, cache, logs ----------------------------------------------------------------


class LeakyIndex(SiloIndex):
    """An index with a bug: every search also returns a chunk from another tenant."""

    def search(self, ctx, vector, k):
        foreign = Chunk("cinder", "leak", 0, "Leak", "CANARY-leaked")
        return [Hit(foreign, 1.0), *super().search(ctx, vector, k)]


def test_output_guard_drops_foreign_chunks(seeded, caplog):
    _, tenants = seeded
    service = RagService(LeakyIndex(), HashingEmbedder(), first_doc_llm())
    me = tenants[0].context()
    service.ingest(me, {"doc_id": "d", "title": "t", "text": "expense approval limit"})
    with caplog.at_level(logging.WARNING, logger="metaforge.rag"):
        hits = service.retrieve(me, "expense approval limit")
    assert [h.chunk.tenant_id for h in hits] == [me.tenant_id]
    assert "output guard dropped 1" in caplog.text and "CANARY-leaked" not in caplog.text


def test_cache_hits_are_tenant_scoped_and_invalidated(rag):
    service, tenants = rag
    me, other = tenants[0].context(), tenants[1].context()
    q = "What is the expense approval limit?"
    first = service.answer(me, q)
    assert not first.cached and service.answer(me, q).cached
    assert not service.answer(other, q).cached
    service.ingest(me, {"doc_id": "new", "title": "t", "text": "unrelated"})
    assert not service.answer(me, q).cached  # this tenant's index changed


def test_logs_carry_no_text(rag, caplog):
    service, tenants = rag
    me = tenants[0]
    with caplog.at_level(logging.DEBUG, logger="metaforge"):
        service.answer(me.context(), "What is the merger code name?")
        service.ingest(me.context(), {"doc_id": "x", "title": "Secret title", "text": me.canary})
    assert caplog.records  # something was logged
    assert (
        me.canary not in caplog.text and "merger" not in caplog.text and "Secret" not in caplog.text
    )


# ---- siloed storage ---------------------------------------------------------------------------


def test_silo_paths_stay_under_root(seeded, tmp_path):
    _, tenants = seeded
    service = RagService(SiloIndex(root=tmp_path), HashingEmbedder(), first_doc_llm())
    for t in tenants[:2]:
        service.ingest(t.context(), {"doc_id": "d", "title": "t", "text": "hello"})
    service.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == sorted(f"{t.tenant_id}.sqlite" for t in tenants[:2])


# ---- naive mode -------------------------------------------------------------------------------


def test_rag_naive_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    assert naive_rag_enabled() is False
    with pytest.raises(RuntimeError):
        SiloIndex(naive=True)
    with pytest.raises(RuntimeError):
        RagService(SiloIndex(), HashingEmbedder(), first_doc_llm(), naive=True)


@pytest.mark.parametrize("value", ["1", "true", NAIVE_VALUE.upper(), f" {NAIVE_VALUE}"])
def test_rag_naive_mode_needs_the_exact_env_value(monkeypatch, value):
    monkeypatch.setenv(NAIVE_ENV, value)
    assert naive_rag_enabled() is False


def test_rag_naive_mode_warns(monkeypatch, caplog, seeded):
    _, tenants = seeded
    monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    with caplog.at_level(logging.WARNING, logger="metaforge.rag"):
        service = RagService(SiloIndex(naive=True), HashingEmbedder(), first_doc_llm(), naive=True)
        service.retrieve(tenants[0].context(), "expense")
    assert "NAIVE" in caplog.text
