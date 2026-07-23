# Phase 1 Review — Local Monolith (create + redirect)

> Staff-eng review of the TinyURL Phase 1 build. What's solid, what's deliberately
> deferred, and the interview talking points per component. Save at `docs/PHASE-1-REVIEW.md`.

**Status:** Phase 1 functionally complete. One DoD gap — the automated test suite (see §Deferred).

**Phase objective:** ship a working shortener runnable locally — `POST` a long URL → short code,
`GET /{code}` → 302 redirect — with schema, index, and k8s-readiness right so Phases 2–5 bolt on
without reshaping the spine. The one concept: read/write asymmetry (100:1) and the 301/302 decision.

---

## Task-by-task summary

| Task | Objective | Verdict |
|---|---|---|
| 1. Skeleton | Env-driven injectable config, app factory, transport/service/repository seam | PASS |
| 2. Compose + Dockerfile | `api` + `postgres:16`, healthcheck gates startup, config via service name | PASS |
| 3. Alembic migration | `urls` table as versioned code; defend PK / TIMESTAMPTZ / nullable user_id | PASS |
| 4. Create endpoint | Race-free `ON CONFLICT ... RETURNING`, retry in service layer, repo seam, CSPRNG codes | PASS |
| 5. Redirect endpoint | 302 (not 301/307), lazy expiry (404 not 500), catch-all registered last | PASS |
| 6. /readyz + logging | Readiness reuses real session path; JSON logs + request-id correlation | PASS |
| 7. Load-test baseline | No-cache redirect p50/p99/req-s recorded as Phase 2's contract | PASS |
| — Tests | create / redirect / 404-missing / 404-expired / conflict-retry | NOT BUILT |

---

## Files created

```
services/api/
  Dockerfile                         # slim base, deps-before-code caching, non-root, PYTHONUNBUFFERED
  requirements.txt
  alembic.ini                        # no hardcoded sqlalchemy.url (env-driven)
  migrations/
    env.py                           # async template, reads os.environ["DATABASE_URL"]
    versions/f192c13b51a0_*.py       # create urls table (reversible)
  app/
    config.py                        # Settings + get_settings() lru_cache
    main.py                          # create_app() factory, lifespan engine, middleware wiring
    logging_config.py                # ContextVar request_id + JsonFormatter
    middleware.py                    # RequestContextMiddleware (id + timed completion log)
    api/
      routers.py                     # health, /readyz, POST /api/urls, GET /{short_code}
      schema.py                      # ShortenRequest / ShortenResponse
    services/
      url_service.py                 # create_url, resolve_code, _generate_code (secrets)
      exceptions.py                  # UrlNotFoundError, UrlExpiredError
    repositories/
      base.py                        # AbstractUrlRepository (ABC) + UrlRecord
      url_repository.py              # PostgresUrlRepository (insert_if_absent, get_by_code)
    db/
      session.py                     # get_session, engine read from app.state
deploy/compose/docker-compose.yml    # api + postgres:16, healthcheck, service_healthy gate
loadtest/
  locustfile.py                      # 80/20 hot-set, allow_redirects=False, 302 assertion
  RESULTS.md                         # no-cache baseline (~1447 req/s, p50 31ms, p99 110ms)
Makefile                             # up/down/down-v/restart/logs/migrate/test/shell
```

---

## What's solid (and why it matters)

**The clean-architecture seam is real, not decorative.** `AbstractUrlRepository` (ABC) declares the
contract; `PostgresUrlRepository` implements it; the service layer (`create_url`, `resolve_code`)
depends on the abstraction and imports neither FastAPI nor SQLAlchemy; transport wires the concrete
impl via `Depends`. This is the lever that lets Phase 3's KGS replace the retry loop behind the same
interface without the use-case changing. The dependency rule points inward throughout.

**Race-free collision handling.** `INSERT ... ON CONFLICT DO NOTHING RETURNING short_code`, success
determined by whether a row came back (`result.first() is not None`) — not by `rowcount`, whose
semantics for no-op conflicts are driver-dependent. Avoids the SELECT-then-INSERT TOCTOU race that
is the #2 interview trap on this problem.

**Unguessable codes.** `secrets.choice` (CSPRNG) over base62, 7 chars. 62^7 >> 30B rows, sparse ⇒
unpredictable — satisfies the "not guessable" non-functional requirement. `random.choices` would
have been a PRNG an interviewer flags.

**Schema decisions defended.** `short_code` as PK (the redirect's hot-path btree, created for free;
one index not two on writes; same value used as the Phase 4 shard key — no indirection). `TIMESTAMPTZ`
(normalizes to UTC on write, so `expires_at < now()` is always a UTC-vs-UTC comparison regardless of
where app/DB/user sit; naive TIMESTAMP would break across timezones/DST). `user_id` nullable, no FK
(reserve the column, defer the constraint — an FK needs a users table that doesn't exist yet, and
buys nothing today).

**302, deliberately.** Chosen over 301 so every click returns to the service (301 caches in the
browser and kills click analytics — Phase 5 depends on this). Chosen over 307/308 because those are
the method-preserving variants; a shortener wants a plain GET redirect. Set explicitly, since
FastAPI's RedirectResponse defaults to 307.

**Liveness vs readiness, built and demonstrated.** `/healthz` always 200 (failure → restart);
`/readyz` runs a real `SELECT 1` through the same session path real traffic uses (failure → 503 →
depool, not restart). Proven: killing Postgres flips `/readyz` to 503 while `/healthz` stays 200,
then `/readyz` self-heals when the DB returns (helped by `pool_pre_ping=True`). Conflating the two
would turn a recoverable DB blip into a full restart-loop outage.

**Observability groundwork.** Structured JSON logs to stdout; request id set in a ContextVar by
middleware (so it reaches the service layer without polluting function signatures), preserved from an
upstream `X-Request-ID` if present, echoed back to the client, and correlated across the log line and
the response header. Per-request `duration_ms` recorded — the field Phase 2 uses to show the cache win.

**Honest baseline.** 500 URLs, 80/20 hot-set, saturation load (no think-time), measuring time-to-302
(`allow_redirects=False`), with a 302 assertion so "0 failures" means genuine redirects. Throughput
plateaued ~1,500 req/s — the saturation ceiling Phase 2 must lift. Recorded with full conditions so
the Phase 2 comparison is reproducible.

---

## Deferred (deliberate — do NOT treat as bugs)

**Automated tests** — the one open DoD item. `make test` targets an empty `tests/` dir. Needed:
create, redirect, 404-on-missing, 404-on-expired, and the conflict-retry path (the last one matters
most — it's the concurrency logic that can't be eyeballed). Also requires copying `tests/` into the
image or running pytest against the right in-container path.

**SSRF hardening.** `AnyHttpUrl` enforces http/https (blocks `javascript:`, `ftp:`) but does not block
internal hosts like `http://169.254.169.254` (cloud metadata) or `http://localhost`. Phase 1 scope is
thin validation, not a URL crawler — but a sharp interviewer will ask about the metadata endpoint.

**Unit-of-work commit boundary.** The repository currently calls `session.commit()` itself. Fine for
single-statement Phase 1, but it means the repo owns transaction control, so a future multi-write
use-case (Phase 5 telemetry) can't make two writes atomic. The clean pattern moves commit/rollback to
the request boundary and out of the repo.

**Two-flavor 404.** The service raises distinct `UrlNotFoundError` vs `UrlExpiredError`, but transport
currently maps both to a bare 404. The distinction is preserved *in the design* so Phase 7 can split
them (delete-on-read for expired, or a "link expired" page) without touching the service — but it is
not yet used at the transport layer. Consider a distinct `detail` string so your 404 is
distinguishable from FastAPI's framework "no route matched" 404 (this ambiguity cost debugging time).

**Junk-traffic filter on the catch-all.** `/{short_code}` fields every unmatched path (favicon,
bots) as a DB lookup. A length/pattern constraint (codes are exactly 7 base62 chars) would reject
obvious non-codes before a DB hit. Cheap, deferred — Phase 2's cache absorbs some of it anyway.

**Migrations in production.** Currently baked into the image and run via `make migrate`. The
production pattern is a separate migration Job/init-container that runs once and gates the rollout —
so N app replicas don't race to run `upgrade head` on startup. Known, deferred to the k8s phase.

---

## Latent traps to remember

- **Catch-all registration order is load-bearing.** `/{short_code}` must stay declared last. Any
  literal route added *below* it gets silently shadowed → 404. New routes go above the catch-all.
- **`restart` doesn't rebuild.** `make restart` runs the old image. After a code change use
  `make up` (rebuilds) or you'll swear the change didn't take.
- **`make down-v` destroys the Postgres volume.** Named distinctly on purpose — data loss requires
  typing something explicit.

---

## Interview talking points (per component)

- **Requirements framing:** read-heavy 100:1 (20K/s reads vs 200/s writes); every decision optimizes
  the read path.
- **301 vs 302:** 302 so clicks return to you (analytics); 301 caches client-side and kills tracking.
- **Key generation:** random base62 + UNIQUE constraint + retry (not counter-encode → predictable;
  not KGS yet → that's Phase 3). CSPRNG for unguessability.
- **Collision handling:** `ON CONFLICT ... RETURNING`, check for a returned row — not rowcount, not
  SELECT-then-INSERT (the race).
- **Schema:** PK = short_code (hot-path index for free, future shard key); TIMESTAMPTZ (UTC compare);
  no FK yet (reserve column, defer constraint).
- **Lazy expiry:** check on read, 404 if expired; no background sweeper (that scan hammers the DB).
- **Liveness vs readiness:** restart vs depool; readiness must exercise the real dependency path or
  it lies.
- **Capacity:** 62^7 keyspace, sparse ⇒ unguessable; 500M/mo new URLs, 30B rows over 5yr, ~170GB to
  cache the hot 20%.

---

## Definition-of-done roll-up

- [x] `make up` → create via curl → 302-redirect in a browser
- [x] Load test runs; baseline redirect throughput + p99 in `loadtest/RESULTS.md`
- [x] `/healthz` and `/readyz` behave (kill Postgres → `/readyz` 503, `/healthz` 200)
- [x] Expired links 404 (not 500 — the TIMESTAMPTZ payoff)
- [x] Duplicate long URLs get different codes
- [ ] Test suite: create, redirect, 404-missing, 404-expired, conflict-retry

---

## Next

Phase 2 — Redis cache-aside on the redirect path. Must beat the recorded baseline (p99 110ms,
~1447 req/s) using the identical Locust config and 80/20 distribution, or the comparison is invalid.
Recommended first: close the test gap (especially the conflict-retry test) so the cache refactor
lands on a tested spine.
