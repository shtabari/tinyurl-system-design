from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.routers import router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    yield
    await app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="TinyURL API", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()