# # services/api/app/api/health.py
# from fastapi import APIRouter

# router = APIRouter(tags=["health"])


# @router.get("/healthz")
# async def health_check() -> dict[str, str]:
#     """Liveness probe — always 200 if the process is up. No dependency checks."""
#     return {"status": "ok"}


from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schema import ShortenRequest, ShortenResponse
from app.config import get_settings, Settings
from app.db.session import get_session
from app.repositories.url_repository import PostgresUrlRepository
from app.services.url_service import create_url

router = APIRouter()


def get_repo(session: AsyncSession = Depends(get_session)) -> PostgresUrlRepository:
    return PostgresUrlRepository(session)



@router.get("/healthz", tags=["ops"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}



@router.post("/api/urls", response_model=ShortenResponse, status_code=status.HTTP_201_CREATED, tags=["urls"])
async def shorten_url(
    payload: ShortenRequest,
    repo: PostgresUrlRepository = Depends(get_repo),
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