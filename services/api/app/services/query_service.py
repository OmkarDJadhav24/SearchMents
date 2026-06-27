import hashlib
import json
import time
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import LLMUnavailableError, MissingTenantFilterError
from app.db.qdrant import get_qdrant_client
from app.db.redis import get_redis_client
from app.models.conversation import Conversation
from app.schemas.query import AskRequest, AskResponse, SourceChunk

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Semantic cache helpers ────────────────────────────────────────────

def _cache_key(user_id: uuid.UUID, question: str) -> str:
    q_hash = hashlib.md5(question.strip().lower().encode()).hexdigest()
    return f"semantic_cache:{user_id}:{q_hash}"


async def _get_cached(user_id: uuid.UUID, question: str) -> dict | None:
    redis = get_redis_client()
    key = _cache_key(user_id, question)
    raw = await redis.get(key)
    if raw:
        return json.loads(raw)
    return None


async def _set_cache(user_id: uuid.UUID, question: str, payload: dict) -> None:
    redis = get_redis_client()
    key = _cache_key(user_id, question)
    await redis.setex(key, settings.cache_ttl_seconds, json.dumps(payload))


# ── Main query handler ────────────────────────────────────────────────

async def ask_question(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    request: AskRequest,
) -> AskResponse:
    # 1. Check semantic cache
    cached = await _get_cached(user_id, request.question)
    if cached:
        logger.info("Cache hit", user_id=str(user_id))
        return AskResponse(**cached, from_cache=True)

    # 2. Embed the query (runs locally via shared library)
    retrieval_start = time.perf_counter()
    from rag_shared.embeddings.local_embedder import get_embedder
    embedder = get_embedder()
    query_vector = embedder.embed_single(request.question)

    # 3. Qdrant dense vector search (mandatory user_id filter)
    dense_chunks = await _qdrant_search(
        user_id=user_id,
        query_vector=query_vector,
        top_k=settings.retrieval_top_k,
        document_ids=request.filters.document_ids if request.filters else None,
    )

    # 4. BM25 search via PostgreSQL full-text
    bm25_chunks = await _bm25_search(
        db=db,
        user_id=user_id,
        question=request.question,
        top_k=settings.retrieval_top_k,
        document_ids=request.filters.document_ids if request.filters else None,
    )

    # 5. Reciprocal Rank Fusion
    fused = _rrf_fusion(dense_chunks, bm25_chunks)

    # 6. Rerank
    from rag_shared.reranker.cross_encoder import get_reranker
    reranker = get_reranker()
    top_chunks = reranker.rerank(request.question, fused, top_k=request.top_k)

    retrieval_ms = int((time.perf_counter() - retrieval_start) * 1000)

    # 7. LLM generation
    llm_start = time.perf_counter()
    llm_unavailable = False
    try:
        answer = await _generate_answer(request.question, top_chunks)
    except LLMUnavailableError:
        llm_unavailable = True
        answer = "LLM is currently unavailable. Here are the most relevant document sections:"
    llm_ms = int((time.perf_counter() - llm_start) * 1000)

    # 8. Persist or retrieve conversation
    conversation_id = request.conversation_id or uuid.uuid4()
    if not request.conversation_id:
        conv = Conversation(id=conversation_id, user_id=user_id)
        db.add(conv)
        await db.flush()

    # 9. Build response
    sources = [
        SourceChunk(
            chunk_id=c["chunk_id"],
            document_id=c["document_id"],
            filename=c["filename"],
            chunk_index=c["chunk_index"],
            chunk_text=c["chunk_text"],
            relevance_score=c["score"],
        )
        for c in top_chunks
    ]

    response_data = dict(
        answer=answer,
        conversation_id=conversation_id,
        sources=[s.model_dump() for s in sources],
        retrieval_ms=retrieval_ms,
        llm_ms=llm_ms,
        llm_unavailable=llm_unavailable,
    )

    # 10. Cache result (don't cache LLM failures)
    if not llm_unavailable:
        await _set_cache(user_id, request.question, response_data)

    return AskResponse(
        answer=answer,
        conversation_id=conversation_id,
        sources=sources,
        retrieval_ms=retrieval_ms,
        llm_ms=llm_ms,
        llm_unavailable=llm_unavailable,
    )


# ── Internal retrieval helpers ────────────────────────────────────────

async def _qdrant_search(
    *,
    user_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    document_ids: list[uuid.UUID] | None,
) -> list[dict]:
    """Dense vector search with mandatory tenant filter."""
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchAny

    # CRITICAL: user_id filter is NEVER optional
    if user_id is None:
        raise MissingTenantFilterError("user_id is required for Qdrant search")

    must_conditions = [
        FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
        FieldCondition(key="is_active", match=MatchValue(value=True)),
    ]
    if document_ids:
        must_conditions.append(
            FieldCondition(
                key="document_id",
                match=MatchAny(any=[str(did) for did in document_ids]),
            )
        )

    client = get_qdrant_client()
    results = await client.search(
        collection_name=settings.qdrant_collection_name,
        query_vector=query_vector,
        query_filter=Filter(must=must_conditions),
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "chunk_id": uuid.UUID(r.payload["chunk_id"]) if "chunk_id" in r.payload else uuid.uuid4(),
            "document_id": uuid.UUID(r.payload["document_id"]),
            "filename": r.payload.get("filename", ""),
            "chunk_index": r.payload.get("chunk_index", 0),
            "chunk_text": r.payload.get("chunk_text", ""),
            "score": r.score,
            "source": "dense",
            "rank": i,
        }
        for i, r in enumerate(results)
    ]


async def _bm25_search(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    question: str,
    top_k: int,
    document_ids: list[uuid.UUID] | None,
) -> list[dict]:
    """BM25 full-text search via PostgreSQL tsvector."""
    from sqlalchemy import text

    doc_filter = ""
    params: dict[str, Any] = {
        "user_id": str(user_id),
        "query": " & ".join(question.split()),
        "top_k": top_k,
    }
    if document_ids:
        doc_filter = "AND c.document_id = ANY(:doc_ids)"
        params["doc_ids"] = [str(d) for d in document_ids]

    sql = f"""
        SELECT
            c.id            AS chunk_id,
            c.document_id,
            d.filename,
            c.chunk_index,
            c.chunk_text,
            ts_rank_cd(c.chunk_text_tsv, plainto_tsquery('english', :query)) AS score
        FROM chunks_metadata c
        JOIN documents d ON d.id = c.document_id
        WHERE c.user_id = :user_id
          AND c.is_active = true
          AND c.chunk_text_tsv @@ plainto_tsquery('english', :query)
          {doc_filter}
        ORDER BY score DESC
        LIMIT :top_k
    """
    result = await db.execute(text(sql), params)
    rows = result.mappings().all()

    return [
        {
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "filename": row["filename"],
            "chunk_index": row["chunk_index"],
            "chunk_text": row["chunk_text"],
            "score": float(row["score"]),
            "source": "bm25",
            "rank": i,
        }
        for i, row in enumerate(rows)
    ]


def _rrf_fusion(
    dense: list[dict],
    bm25: list[dict],
    k: int = 60,
) -> list[dict]:
    """
    Reciprocal Rank Fusion.
    Score = 1/(k + rank_dense) + 1/(k + rank_bm25)
    Chunks that appear in both lists score highest.
    """
    scores: dict[str, float] = {}
    chunks_by_id: dict[str, dict] = {}

    for source, results in [("dense", dense), ("bm25", bm25)]:
        for rank, chunk in enumerate(results):
            cid = str(chunk["chunk_id"])
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            chunks_by_id[cid] = chunk

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    fused = []
    for cid, score in ranked:
        c = chunks_by_id[cid].copy()
        c["score"] = score
        fused.append(c)
    return fused


async def _generate_answer(question: str, chunks: list[dict]) -> str:
    """Call the configured LLM with the retrieved context."""
    context = "\n\n---\n\n".join(
        f"[Source: {c['filename']}, chunk {c['chunk_index']}]\n{c['chunk_text']}"
        for c in chunks
    )
    prompt = (
        "You are a helpful assistant. Answer the question using ONLY the provided context. "
        "If the answer is not in the context, say 'I don't have enough information to answer that.' "
        "Do not fabricate information.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )

    from app.services.llm_service import call_llm
    return await call_llm(prompt)