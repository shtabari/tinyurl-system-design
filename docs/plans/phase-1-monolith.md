# Phase 1 — Local Monolith: create + redirect

> **Handoff:** experienced ML engineer leveling up on distributed systems by building TinyURL.
> Stack: FastAPI, Postgres, Redis, Docker, EKS target. Skip Python/ML basics. Bias to building;
> **hints before answers, never dump full solutions unless asked.** Review like a staff engineer
> (correctness → failure modes → scaling → cost → observability), always name the road not taken.
> Push the thin slice; block gold-plating. Full context in `00-ROADMAP.md`.

**Starting state:** empty repo.
**Ships:** a working shortener you can run locally — `POST` a long URL, get a short code back,
`GET /{code}` redirects. Nothing else.
**The one concept:** the read/write asymmetry and the `301`/`302` decision — plus getting the
schema, the index, and k8s-readiness right so later phases bolt on cleanly.

---

## Design brief (the vocabulary + the tradeoffs)

**Two paths, wildly different load.** Write = "shorten this URL" (~200/s at target). Read = "redirect me"
(~20K/s). 100:1. Every architectural decision from here on optimizes the read path. Internalize this now:
it's *why* we cache (Ph2), *why* we shard on the code (Ph4), *why* telemetry must be async (Ph5).

**`301` vs `302` — the decision that quietly defines the whole system.**

| | `301 Moved Permanently` | `302 Found (temporary)` |
|---|---|---|
| Browser behavior | Caches the redirect; **future clicks skip your server** | Re-asks your server **every click** |
| Redirect latency | Faster for the user after first hit | One hop to you every time |
| Analytics | **You lose click tracking** (you never see repeat clicks) | You see every click |
| Reversibility | Hard (browsers cached it) | Easy |

We choose **`302`**. The whole Phase 5 telemetry system exists *because* every click comes back to us.
Choosing `301` here would silently make click analytics impossible later. This is the #1 interview
gotcha on this problem — be able to say why in one breath.

**Key generation — start simplest-correct, not clever.** Phase 1 uses **random base62, 7 chars,
insert with a UNIQUE constraint, retry on the rare conflict.** That's it. Not a counter-encode
(predictable ⇒ violates the "not guessable" non-functional req). Not KGS (that's Phase 3's whole
point — don't pre-empt it). This deliberately leaves a visible seam: "what happens to write latency
when the table fills and conflicts rise?" — which *motivates* Phase 3.

**Lazy expiry.** Store `expires_at`. Check it on read; if expired, return 404 (and you *may* delete
then). Do **not** build a background sweeper — that's Phase 7. Continuously scanning for expired rows
hammers the DB (the doc's own warning).

---

## Schema (Alembic migration)

```
urls
  short_code   VARCHAR(10)  PRIMARY KEY      -- PK gives you the lookup index for free
  long_url     TEXT         NOT NULL
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
  expires_at   TIMESTAMPTZ  NULL             -- NULL = no expiry
  user_id      BIGINT       NULL             -- nullable now; real users are Phase 7
```

Staff-eng notes to sit with before you write it:
- **Why is `short_code` the PK and not a surrogate `id`?** Your only hot query is
  `WHERE short_code = ?`. Making it the PK means the redirect is a primary-key lookup — the single
  fastest thing Postgres does. A separate auto-`id` PK would add a second index you'd have to maintain
  and buy you nothing on the hot path. (It *would* matter if you sharded by `id` — but you'll shard by
  `short_code`. See Ph4.) Decide deliberately; be ready to defend it.
- `long_url` as `TEXT` not `VARCHAR(n)` — Postgres treats them the same storage-wise; the arbitrary
  cap just invites truncation bugs.
- No `user` table yet. "No relationships" is the whole point of the data model — don't build joins
  you won't use.

---

## Build tasks (thin slice, in order)

You implement these. I'll hint, not hand over.

1. **Project skeleton.** `services/api` with FastAPI, `pydantic-settings` config (DB URL, base URL,
   default TTL — all from env). `Makefile` with `up`/`down`/`test`.
2. **Compose.** `deploy/compose/docker-compose.yml`: `api` + `postgres:16`. Healthcheck on postgres so
   `api` waits for it.
3. **Migration.** Alembic init + the `urls` table above.
4. **Create endpoint.** `POST /api/urls` `{long_url, expires_at?}` → `{short_code, short_url}`.
   - Generate 7-char base62. **Hint on the collision handling:** don't pre-check existence with a
     `SELECT` (that's a race — two requests can both see "free" then both insert). Let the DB be the
     arbiter: `INSERT ... ON CONFLICT DO NOTHING`, check `rowcount`, regenerate + retry on 0. Cap
     retries (e.g. 5) and 500 if you somehow exhaust them (you won't at this keyspace).
   - Validate `long_url` (scheme required; reject `javascript:` etc. — thin validation, not a URL crawler).
5. **Redirect endpoint.** `GET /{short_code}` →
   - lookup; miss ⇒ 404.
   - expired (`expires_at < now()`) ⇒ 404 (optionally delete-on-read).
   - else ⇒ **`RedirectResponse(long_url, status_code=302)`**.
   - Guard the route so it doesn't shadow `/api/...`, `/healthz`, `/docs`.
6. **Health + logs.** `/healthz` (return 200 always), `/readyz` (ping Postgres; 503 if down).
   Structured JSON logging with a request id.
7. **Load-test scaffold.** `loadtest/` with k6 or Locust: one scenario that seeds N URLs then hammers
   redirects. Record baseline p50/p99 and sustained req/s. **Write the numbers down** — Phase 2 beats them.

---

## Definition of done

- `make up` → create a short URL via curl → hitting it 302-redirects in a browser.
- Load test runs; you have a **baseline redirect throughput + p99** recorded in `loadtest/RESULTS.md`.
- `/healthz` and `/readyz` behave (kill Postgres → `/readyz` flips to 503).
- Expired links 404. Duplicate long URLs get *different* codes (that's fine and expected here).
- Tests: create, redirect, 404-on-missing, 404-on-expired, conflict-retry path.

## Scope guardrails (do NOT build this phase)

- ❌ No Redis / caching (Phase 2).
- ❌ No KGS / pre-generated keys (Phase 3). Keep the retry loop — it's the thing Phase 3 replaces.
- ❌ No sharding, no multiple DBs (Phase 4).
- ❌ No analytics/click counting (Phase 5).
- ❌ No background expiry sweeper, no auth, no rate limiting, no UI (Phase 7).
- ❌ No custom aliases yet (Phase 7) — random codes only.

If you catch yourself "just adding" any of these, stop and note it as a later task.

---

## Staff-eng review checklist (what the reviewing AI should probe)

- Is collision handling **race-free**? (SELECT-then-INSERT is a bug — must be `ON CONFLICT`.)
- Is `302` used, and can you articulate why not `301`?
- Is the redirect a pure PK lookup (no accidental extra queries, no ORM lazy-load surprise)?
- Is *all* config env-driven (no `localhost` baked in)? This is the EKS-readiness gate.
- Does `/readyz` actually check the dependency, or does it lie (return 200 while DB is down)?
- Open-redirect / SSRF surface on `long_url`? At minimum enforce `http(s)` scheme.
- Is `expires_at` timezone-aware (`TIMESTAMPTZ`, compared in UTC)?

## Interview framing

"Walk me through a URL shortener" → this phase *is* the answer to the first 10 minutes: clarify
read-heavy 100:1, pick `302` for analytics, PK-lookup redirect, keyspace sizing (62⁷ ≫ 30B, sparse
⇒ unguessable), lazy expiry. The interviewer's first trap is `301` vs `302`; the second is
SELECT-then-INSERT races. Nail both and you've set the tone.

## Capstone connection

This is the spine — the `services/api` process every later phase extends. Keep the create/redirect
contract stable; Phases 2–5 change *what happens behind it*, not the API surface.
