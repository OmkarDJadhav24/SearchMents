"""
Redis Client Singleton

- Creates a single async Redis client for the entire application.
- `@lru_cache` ensures the client is created only once and reused on every call.
- The client uses the Redis URL from application settings.
- `decode_responses=True` automatically converts Redis bytes to Python strings.
- Reusing one client is efficient because it shares the underlying connection pool.
"""

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