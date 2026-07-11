from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(request.app.state.engine, expire_on_commit=False)
    async with factory() as session:
        yield session