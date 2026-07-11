from abc import ABC, abstractmethod
from datetime import datetime


class AbstractUrlRepository(ABC):
    @abstractmethod
    async def insert_if_absent(
        self,
        short_code: str,
        long_url: str,
        expires_at: datetime | None,
    ) -> bool:
        pass