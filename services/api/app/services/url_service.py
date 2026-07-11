import random
import string
from datetime import datetime

from app.repositories.base import AbstractUrlRepository

_ALPHABET = string.ascii_letters + string.digits
_CODE_LENGTH = 7
_MAX_RETRIES = 5


def _generate_code() -> str:
    return "".join(random.choices(_ALPHABET, k=_CODE_LENGTH))


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