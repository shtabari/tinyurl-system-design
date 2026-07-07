# services/api/app/api/health.py
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def health_check() -> dict[str, str]:
    """Liveness probe — always 200 if the process is up. No dependency checks."""
    return {"status": "ok"}