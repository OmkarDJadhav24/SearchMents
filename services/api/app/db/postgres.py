"""
Database Configuration (SQLAlchemy Async + FastAPI)
===================================================

Purpose:
--------
This file initializes the SQLAlchemy async database layer for the application.
It creates:
1. The database engine (connection pool)
2. A session factory (AsyncSessionLocal)
3. A shared declarative Base for ORM models
4. A FastAPI dependency (get_db) that manages database sessions

Flow:
-----
Request
   │
   ▼
get_db()
   │
   ▼
Create AsyncSession
   │
   ▼
Route performs DB operations
   │
   ├── Success  → Commit transaction
   └── Error    → Rollback transaction
   │
   ▼
Close session (returns connection to pool)

Components:
-----------
engine
    • Manages database connections.
    • Uses connection pooling for better performance.

AsyncSessionLocal
    • Factory that creates one AsyncSession per request.
    • Configured with:
        - expire_on_commit=False → Keep ORM objects usable after commit.
        - autoflush=False → Flush only when explicitly requested or on commit.
        - autocommit=False → Transactions are committed manually.

Base
    • Parent class for all SQLAlchemy ORM models.
    • Example:
        class User(Base):
            __tablename__ = "users"

get_db()
    • FastAPI dependency.
    • Creates one session per request.
    • Automatically:
        - Commits on success
        - Rolls back on exceptions
        - Closes the session in all cases

Engine Configuration:
---------------------
echo
    Prints SQL queries in development for debugging.

pool_pre_ping=True
    Checks if a database connection is alive before using it,
    preventing stale connection errors.

pool_size=10
    Maintains up to 10 persistent database connections.

max_overflow=20
    Allows 20 additional temporary connections during high traffic.
    Maximum concurrent connections = 30.

Typical Usage:
--------------
@app.get("/users")
async def get_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    return result.scalars().all()

Best Practices:
---------------
✓ One session per request.
✓ Never create AsyncSession manually inside routes.
✓ Let get_db handle commit, rollback, and cleanup.
✓ Always use `await` with async database operations.
✓ Import Base when defining ORM models.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,        # detect stale connections
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,    # prevents lazy-load errors after commit
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


async def get_db() -> AsyncSession:
    """
    FastAPI dependency that yields a database session per request
    and guarantees rollback on error and close on exit.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()