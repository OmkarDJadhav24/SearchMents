"""

Application Starts
        │
        ▼
Load settings
        │
        ▼
Create (or reuse) Async Qdrant client
        │
        ▼
Check existing collections
        │
        ├───────────────► Exists?
        │                     │
        │                     ▼
        │                Skip creation
        │
        ▼
Create collection (if missing)
        │
        ▼
Create/verify payload indexes
        │
        ▼
Application ready to store and search embeddings

"""

from functools import lru_cache

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PayloadSchemaType,
)

import structlog
from app.config import get_settings

settings = get_settings()
logger = structlog.get_logger(__name__)


@lru_cache
def get_qdrant_client() -> AsyncQdrantClient:
    """Return a cached async Qdrant client."""
    return AsyncQdrantClient(url=settings.qdrant_url)


async def init_qdrant_collection() -> None:
    """
    Create the rag_chunks collection if it does not already exist,
    and ensure all payload indexes are in place.

    This is called once at API startup and is fully idempotent.
    """
    client = get_qdrant_client()
    collection_name = settings.qdrant_collection_name

    existing = await client.get_collections()
    existing_names = {c.name for c in existing.collections}

    if collection_name not in existing_names:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=settings.qdrant_vector_size,
                distance=Distance.COSINE,
                on_disk=False,          # keep in RAM for dev
            ),
        )
        logger.info("Created Qdrant collection", collection=collection_name)
    else:
        logger.info("Qdrant collection already exists", collection=collection_name)

    # ── Payload indexes for fast filtering ───────────────────────────
    # These are idempotent — safe to call even if indexes already exist.
    index_specs = [
        ("user_id",          PayloadSchemaType.KEYWORD),
        ("document_id",      PayloadSchemaType.KEYWORD),
        ("version_id",       PayloadSchemaType.KEYWORD),
        ("is_active",        PayloadSchemaType.BOOL),
        ("version_number",   PayloadSchemaType.INTEGER),
    ]

    for field_name, schema_type in index_specs:
        await client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=schema_type,
        )

    logger.info("Qdrant payload indexes verified", collection=collection_name)