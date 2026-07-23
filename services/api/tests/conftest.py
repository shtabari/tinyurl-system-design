import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from redis.asyncio import Redis

from app.config import get_settings
from app.main import create_app


@pytest_asyncio.fixture
async def client():
    app = create_app()
    settings = get_settings()
    app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    async with app.state.engine.begin() as conn:
        await conn.execute(text("TRUNCATE urls"))
    await app.state.redis.flushdb()
    await app.state.redis.aclose()
    await app.state.engine.dispose()