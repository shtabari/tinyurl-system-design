import logging
import json
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.repositories.base import AbstractUrlRepository, UrlRecord

logger = logging.getLogger(__name__)


class CachingUrlRepository(AbstractUrlRepository):
    def __init__(
        self,
        inner: AbstractUrlRepository,
        redis: Redis,
        default_ttl_seconds: int,
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._default_ttl_seconds = default_ttl_seconds

    def _cache_key(self, short_code: str) -> str:
        return f"url:{short_code}"

    def _serialize(self, record: UrlRecord) -> str:
        return json.dumps(
            {
                "long_url": record.long_url,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            }
        )

    def _deserialize(self, raw: str) -> UrlRecord:
        payload = json.loads(raw)
        expires_at = payload["expires_at"]
        return UrlRecord(
            long_url=payload["long_url"],
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        )

    def _ttl_for(self, record: UrlRecord) -> int | None:
        if record.expires_at is None:
            return self._default_ttl_seconds
        seconds_until_expiry = int(
            (record.expires_at - datetime.now(timezone.utc)).total_seconds()
        )
        if seconds_until_expiry <= 0:
            return None
        return min(self._default_ttl_seconds, seconds_until_expiry)

    async def insert_if_absent(
        self,
        short_code: str,
        long_url: str,
        expires_at: datetime | None,
    ) -> bool:
        return await self._inner.insert_if_absent(short_code, long_url, expires_at)

    async def get_by_code(self, short_code: str) -> UrlRecord | None:
        key = self._cache_key(short_code)

        try:
            cached = await self._redis.get(key)
            if cached is not None:
                return self._deserialize(cached)
        except RedisError:
            logger.warning("Redis GET failed for key=%s, falling back to DB", key)

        record = await self._inner.get_by_code(short_code)
        if record is None:
            return None

        ttl = self._ttl_for(record)
        if ttl is not None:
            try:
                await self._redis.set(key, self._serialize(record), ex=ttl)
            except RedisError:
                logger.warning("Redis SET failed for key=%s, skipping cache write", key)

        return record