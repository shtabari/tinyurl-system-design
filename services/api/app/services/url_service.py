import secrets
import string
from datetime import datetime, timezone

from app.repositories.base import AbstractUrlRepository
from app.services.exceptions import UrlExpiredError, UrlNotFoundError

_ALPHABET = string.ascii_letters + string.digits
_CODE_LENGTH = 7
_MAX_RETRIES = 5


def _generate_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


async def create_url(
    long_url: str,
    expires_at: datetime | None,
    repo: AbstractUrlRepository,
) -> str:
    for _ in range(_MAX_RETRIES):
        code = _generate_code()
        if await repo.insert_if_absent(code, long_url, expires_at):
            return code
    raise RuntimeError("Failed to generate a unique short code after max retries")


async def resolve_code(short_code: str, repo: AbstractUrlRepository) -> str:
    record = await repo.get_by_code(short_code)
    if record is None:
        raise UrlNotFoundError(short_code)
    if record.expires_at is not None and record.expires_at < datetime.now(timezone.utc):
        raise UrlExpiredError(short_code)
    return record.long_url