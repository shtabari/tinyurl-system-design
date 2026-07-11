from abc import ABC, abstractmethod
from datetime import datetime
from typing import NamedTuple


class UrlRecord(NamedTuple):
    long_url: str
    expires_at: datetime | None


class AbstractUrlRepository(ABC):
    @abstractmethod
    async def insert_if_absent(
        self,
        short_code: str,
        long_url: str,
        expires_at: datetime | None,
    ) -> bool:
        ...

    @abstractmethod
    async def get_by_code(self, short_code: str) -> UrlRecord | None:
        ...