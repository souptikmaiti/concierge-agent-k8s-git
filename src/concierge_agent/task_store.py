"""Persistent A2A task storage for this service."""

from contextlib import asynccontextmanager

from a2a.server.tasks import DatabaseTaskStore
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


def create_task_store(database_url: str):
    url = make_url(database_url)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("A2A task store requires a postgresql+asyncpg URL")

    engine = create_async_engine(url, pool_pre_ping=True)
    task_store = DatabaseTaskStore(engine=engine)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            await task_store.initialize()
            yield
        finally:
            await engine.dispose()

    return task_store, lifespan
