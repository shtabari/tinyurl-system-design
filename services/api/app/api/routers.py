from fastapi import APIRouter, Response, status

router = APIRouter()

@router.get("/healthz", tags=["healthz"])
async def health_check():
    return Response(status_code=status.HTTP_200_OK)
