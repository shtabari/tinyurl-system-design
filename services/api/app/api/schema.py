from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel


class ShortenRequest(BaseModel):
    long_url: AnyHttpUrl
    expires_at: datetime | None = None


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str