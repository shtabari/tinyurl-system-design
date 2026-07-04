# Phase 2 — Redis Read Cache (cache-aside on the redirect path)

> **Handoff:** experienced ML engineer, building TinyURL to learn distributed systems. FastAPI /
> Postgres / Redis / Docker / EKS. Hints before answers, no full solutions unless asked. Staff-eng
> review (correctness → failure → scale → cost → observability), name the road not taken, block
> gold-plating. Full context in `00-ROADMAP.md`.

**Starting state:** Phase 1 done — monolith with `POST /api/urls` and `GET /{code}` (302), Postgres,
baseline load numbers recorded.
**Ships:** a Redis cache in front of the redirect lookup, so hot codes never touch Postgres.
**The one concept:** **cache-aside** — its read path, its write/invalidation path, its failure modes
(stampede, staleness), and *proving* it works with a hit-rate metric and a before/after load test.

**Pedagogy this phase: break-then-fix.** You already have the "broken" (uncached) baseline. Add the
cache, re-run the *same* load test, and watch DB QPS collapse and p99 drop. Feeling that delta is the
lesson.

---

## Design brief

**Cache-aside (lazy loading)** — the app owns the cache; Redis knows nothing about Postgres.

```
READ  GET /{code}
      ├─ Redis GET code ── hit ──▶ 302 (done, DB untouched)
      └─ miss ─▶ Postgres lookup ─▶ set Redis (with TTL) ─▶ 302
```

Contrast the alternatives so you can defend the choice:
- **Read-through / write-through** (cache library sits inline, keeps itself coherent): less app code,
  but you couple to a caching layer that understands your store, and write-through taxes every write
  even for codes nobody reads. Overkill here — reads dominate and most codes are cold.
- **Write-back:** ack the write from cache, flush to DB later. Great for write-heavy; we're 100:1
  read-heavy, so pointless risk of data loss on a URL that must be durable.

Cache-aside wins: simplest, matches read-heavy + cold-tail, and Redis staying dumb means a Redis
outage degrades (slower) rather than breaks (you fall through to Postgres).

**TTL — set it deliberately.** `TTL = min(remaining_ttl_of_link, CACHE_CAP)` where `CACHE_CAP` is
maybe an hour. Why cap even non-expiring links? Bounds staleness if a link is deleted out-of-band,
and lets cold entries age out so the working set stays ~170GB-shaped (the 20/80 hot set), not the
whole 15TB.

**Invalidation — the hard part of caching.** On **delete** or **update** of a code, you must
`DEL` it from Redis. Miss this and a deleted link keeps redirecting until TTL. Wire invalidation into
the *same* code path as the DB mutation, not as an afterthought.

**Negative caching — decide, don't ignore.** A flood of 404s (bots probing random codes) all miss the
cache and hit Postgres — the cache gives you *nothing* on your most abusable path. Options: cache a
short-TTL "miss" sentinel, or defer to Phase 7 rate limiting. **Thin-slice call:** cache misses with a
30–60s sentinel TTL *only if* your load test shows 404s hurting the DB; otherwise note it and move on.
Don't build a bloom filter — that's gold-plating at this scale.

**Stampede / thundering herd — name it, size the fix to the evidence.** When a hot key expires,
N concurrent requests all miss and all hit Postgres at once. Fixes, cheapest first:
1. **TTL jitter** (`cap ± random`) so keys don't expire in lockstep — one line, do it.
2. **Single-flight lock** (first miss takes a Redis `SET NX` lock, repopulates, others wait/retry) —
   real but more code. **Only build this if the load test shows a stampede.** For a URL shortener where
   any single miss is one cheap PK lookup, jitter alone is usually enough. Resist pre-building the lock.

---

## Build tasks

1. **Redis in compose.** Add `redis:7` to `docker-compose.yml`; config `REDIS_URL` via env.
2. **Cache-aside on redirect.** Wrap the Phase-1 lookup: `GET` Redis → miss → DB → `SET` with jittered
   TTL. Store just what redirect needs (the `long_url`, and enough to know expiry) — keep the value small.
3. **Invalidation.** On delete/update endpoints, `DEL` the key in the same transaction/handler.
   **Hint:** think about ordering — invalidate *after* the DB commit, or you can re-cache a stale value
   from a concurrent read racing your delete. (This is the cache-invalidation footgun; be able to explain it.)
4. **`/metrics` endpoint.** Prometheus. Emit `cache_hits_total`, `cache_misses_total`, and derive
   hit-rate. This is your proof-of-work for the phase.
5. **Redis-down resilience.** If Redis is unreachable, the redirect must **fall through to Postgres**,
   not 500. Cache is an optimization, never a dependency of correctness. (Verify `/readyz` semantics —
   is Redis a readiness dep? Argue it either way, but be intentional.)
6. **Re-run the load test.** Same scenario as Phase 1. Record new p50/p99 + observed Postgres QPS.
   Compute hit-rate under a Zipfian/hot-key access pattern (if your Phase-1 test hit codes uniformly,
   add a hot-key distribution now — uniform access defeats caching and misrepresents reality).

---

## Definition of done

- Hot codes redirect with **zero Postgres queries** (prove via metrics/DB logs).
- `loadtest/RESULTS.md` shows the before/after: DB QPS down, p99 down, hit-rate reported.
- Deleting a link **immediately** stops redirects (invalidation works).
- `docker stop redis` → redirects still work (slower). No 500s.
- Tests: hit path, miss-then-populate, invalidation-on-delete, Redis-down fallthrough.

## Scope guardrails (do NOT build)

- ❌ No single-flight lock **unless** the load test proves a stampede. Jitter first.
- ❌ No bloom filter / fancy negative-cache structure.
- ❌ No cache replication / multi-node Redis (Phase 6 gives you ElastiCache).
- ❌ No write-back semantics — writes stay synchronous to Postgres.
- ❌ Don't cache the whole row / user data — cache the minimum the redirect needs.

---

## Staff-eng review checklist

- **Invalidation ordering:** can a concurrent read re-populate a stale value after your `DEL`? Walk the
  interleaving. (Classic: delete → concurrent read misses → reads *old* DB row in a repeatable-read txn →
  re-caches stale. Know your isolation level.)
- Is Redis a hard dependency by accident? (A missing try/except turns an optimization into an outage.)
- TTL jitter present? Are non-expiring links still capped?
- Does the value stored avoid unbounded growth (small, not the full record)?
- Is hit-rate actually measured, or assumed? No metric ⇒ not done.
- Serialization cost: are you JSON-encoding on every hit? For a hot path, consider a compact value.

## Interview framing

"How do you make redirects fast?" → cache-aside + TTL, then the follow-ups the interviewer *will* ask:
"what happens on delete?" (invalidation), "hot key expires under load?" (stampede → jitter, then
single-flight if needed), "Redis dies?" (degrade, don't fail). Being able to reach for single-flight
*and say you'd only build it if measured* signals seniority — you're scoping, not cargo-culting.

## Capstone connection

The 170GB hot-cache line from the capacity estimate becomes real here. In Phase 6 this same code points
at ElastiCache instead of a local Redis container — because config is env-driven (Phase 1), that's a
value change, not a rewrite. The hit-rate metric you add now is the first panel on your Phase-7 dashboard.
