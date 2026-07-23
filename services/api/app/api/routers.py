import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from app.cache.redis import get_redis

from app.api.schema import ShortenRequest, ShortenResponse
from app.config import Settings, get_settings
from app.db.session import get_session
from app.repositories.url_repository import PostgresUrlRepository
from app.repositories.caching_url_repository import CachingUrlRepository
from app.repositories.base import AbstractUrlRepository
    
from app.services.exceptions import UrlExpiredError, UrlNotFoundError
from app.services.url_service import create_url, resolve_code

logger = logging.getLogger(__name__)
router = APIRouter()


def get_repo(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> AbstractUrlRepository:
    postgres_repo = PostgresUrlRepository(session)
    return CachingUrlRepository(
        inner=postgres_repo,
        redis=redis,
        default_ttl_seconds=settings.default_ttl_seconds,
    )
 

@router.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
async def readyz(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok"})
    except Exception:
        logger.exception("readyz DB ping failed")
        return JSONResponse({"status": "db_unavailable"}, status_code=503)


@router.post("/api/urls", response_model=ShortenResponse, status_code=status.HTTP_201_CREATED, tags=["urls"])
async def shorten_url(
    payload: ShortenRequest,
    repo: AbstractUrlRepository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> ShortenResponse:
    try:
        short_code = await create_url(str(payload.long_url), payload.expires_at, repo)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Could not generate unique short code")
    return ShortenResponse(
        short_code=short_code,
        short_url=f"{settings.base_url}/{short_code}",
    )


@router.get("/{short_code}", tags=["urls"])
async def redirect(
    short_code: str,
    repo: AbstractUrlRepository = Depends(get_repo),
) -> RedirectResponse:
    try:
        long_url = await resolve_code(short_code, repo)
    except (UrlNotFoundError, UrlExpiredError):
        raise HTTPException(status_code=404)
    return RedirectResponse(url=long_url, status_code=302)