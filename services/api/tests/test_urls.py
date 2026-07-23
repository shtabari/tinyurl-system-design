from datetime import datetime, timezone, timedelta

from httpx import AsyncClient


async def test_healthz(client: AsyncClient):
    """
    Purpose: Verify the liveness endpoint always returns 200.
    Scenario: GET /healthz with no dependencies involved.
    Reason: This endpoint must return 200 even when the DB is down.
            It proves the process is alive, not that dependencies are healthy.
    """
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_create_returns_short_code(client: AsyncClient):
    """
    Purpose: Verify the happy path of URL shortening.
    Scenario: POST a valid long URL, expect a 201 with a 7-char short_code
              and a short_url that ends with that code.
    Reason: Core contract of the API — any regression here breaks everything downstream.
    """
    r = await client.post("/api/urls", json={"long_url": "https://example.com"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["short_code"]) == 7
    assert body["short_url"].endswith(body["short_code"])


async def test_create_same_url_gives_different_codes(client: AsyncClient):
    """
    Purpose: Verify that shortening the same URL twice produces different codes.
    Scenario: POST the same long_url twice, compare the returned short_codes.
    Reason: The system uses random code generation, not a hash of the long URL.
            Two users shortening the same URL must get independent codes so
            click analytics are tracked separately per link.
    """
    r1 = await client.post("/api/urls", json={"long_url": "https://example.com"})
    r2 = await client.post("/api/urls", json={"long_url": "https://example.com"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["short_code"] != r2.json()["short_code"]


async def test_redirect_returns_302(client: AsyncClient):
    """
    Purpose: Verify that a valid short code redirects to the original URL with 302.
    Scenario: Create a short URL, then GET /{code} without following redirects.
    Reason: 302 (not 301) is required so every click hits our server —
            enabling click telemetry in Phase 5. A 301 would be cached by browsers,
            making analytics impossible after the first visit.
    """
    long_url = "https://example.com/test-path"
    r = await client.post("/api/urls", json={"long_url": long_url})
    code = r.json()["short_code"]
    redirect = await client.get(f"/{code}", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == long_url


async def test_redirect_404_on_missing(client: AsyncClient):
    """
    Purpose: Verify that an unknown short code returns 404.
    Scenario: GET a code that was never created.
    Reason: Must not leak information or redirect to an arbitrary destination.
            Clean 404 is the correct response for any unknown code.
    """
    r = await client.get("/doesnotexist", follow_redirects=False)
    assert r.status_code == 404


async def test_redirect_404_on_expired(client: AsyncClient):
    """
    Purpose: Verify that an expired link returns 404 (lazy expiry).
    Scenario: Create a URL with expires_at in the past, then try to redirect.
    Reason: Expiry is checked at read time, not by a background sweeper (Phase 7).
            This is the "lazy expiry" pattern — cheap to implement, correct behavior.
    """
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = await client.post("/api/urls", json={"long_url": "https://example.com", "expires_at": past})
    code = r.json()["short_code"]
    redirect = await client.get(f"/{code}", follow_redirects=False)
    assert redirect.status_code == 404


async def test_create_rejects_non_http_scheme(client: AsyncClient):
    """
    Purpose: Verify that non-http(s) URLs are rejected at the boundary.
    Scenario: POST a javascript: scheme URL.
    Reason: Open-redirect / XSS vector. Pydantic's AnyHttpUrl enforces http/https
            scheme, blocking javascript:, data:, file: etc. at input validation.
    """
    r = await client.post("/api/urls", json={"long_url": "javascript:alert(1)"})
    assert r.status_code == 422


async def test_create_rejects_missing_url(client: AsyncClient):
    """
    Purpose: Verify that a missing long_url field returns 422.
    Scenario: POST an empty JSON body.
    Reason: long_url is a required field in ShortenRequest. Pydantic should
            reject this at the schema level before any business logic runs.
    """
    r = await client.post("/api/urls", json={})
    assert r.status_code == 422


async def test_conflict_retry_generates_new_code(client: AsyncClient, monkeypatch):
    """
    Purpose: Verify the collision retry loop works correctly.
    Scenario: Monkeypatch _generate_code to return FIXED01, FIXED01, FIXED02 in sequence.
              First POST gets FIXED01. Second POST collides on FIXED01, retries, gets FIXED02.
    Reason: Collision probability is ~1 in 3.5T so it can't be tested by hand.
            This test forces the collision path deterministically.
            Critical regression guard for Phase 2 — if the cache refactor accidentally
            breaks the DB insert path, this test catches it immediately.
    """
    codes = iter(["FIXED01", "FIXED01", "FIXED02"])
    monkeypatch.setattr("app.services.url_service._generate_code", lambda: next(codes))
    r1 = await client.post("/api/urls", json={"long_url": "https://example.com/a"})
    r2 = await client.post("/api/urls", json={"long_url": "https://example.com/b"})
    assert r1.status_code == 201
    assert r1.json()["short_code"] == "FIXED01"
    assert r2.status_code == 201
    assert r2.json()["short_code"] == "FIXED02"


async def test_conflict_retry_exhausted_returns_500(client: AsyncClient, monkeypatch):
    """
    Purpose: Verify that exhausting all retries returns 500.
    Scenario: Monkeypatch _generate_code to always return SAME999.
              First POST inserts it. Second POST collides every retry (5 times) → RuntimeError → 500.
    Reason: The retry cap (MAX_RETRIES=5) must have a defined failure mode.
            500 is correct — it's a server-side inability to generate a unique code,
            not a client error. Ensures the error boundary is explicit and tested.
    """
    monkeypatch.setattr("app.services.url_service._generate_code", lambda: "SAME999")
    await client.post("/api/urls", json={"long_url": "https://example.com/x"})
    r = await client.post("/api/urls", json={"long_url": "https://example.com/y"})
    assert r.status_code == 500