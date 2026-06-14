from functools import lru_cache
import redis.asyncio as aioredis
from app.config import get_settings

settings = get_settings()


@lru_cache
def get_redis_client() -> aioredis.Redis:
    """Return a cached async Redis client."""
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )