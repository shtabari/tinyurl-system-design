from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="TinyURL API", version="0.1.0")

    # Import and register routers here (e.g., from .routers import router)
    # app.include_router(router)

    return app

app = create_app()