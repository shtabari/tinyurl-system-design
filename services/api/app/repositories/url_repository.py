from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.base import AbstractUrlRepository


class PostgresUrlRepository(AbstractUrlRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_absent(
        self,
        short_code: str,
        long_url: str,
        expires_at: datetime | None,
    ) -> bool:
        result = await self._session.execute(
            text(
                """
                INSERT INTO urls (short_code, long_url, expires_at)
                VALUES (:short_code, :long_url, :expires_at)
                ON CONFLICT (short_code) DO NOTHING
                RETURNING short_code
                """
            ),
            {"short_code": short_code, "long_url": long_url, "expires_at": expires_at},
        )
        await self._session.commit()
        return result.first() is not None