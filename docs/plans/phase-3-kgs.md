# Phase 3 — Key Generation Service (offline keys, batch-lease)

> **Handoff:** experienced ML engineer, building TinyURL to learn distributed systems. FastAPI /
> Postgres / Redis / Docker / EKS. Hints before answers; staff-eng review (correctness → failure →
> scale → cost → observability); name the road not taken; block gold-plating. See `00-ROADMAP.md`.

**Starting state:** Phases 1–2 done — cached monolith generating keys via random-base62 +
retry-on-conflict.
**Ships:** a standalone **Key Generation Service (KGS)** that pre-generates unique codes; the API
**leases a batch** and serves from memory. The Phase-1 retry loop is deleted — collisions become
structurally impossible.
**The one concept:** **distributed ID / key allocation** — handing out globally-unique keys to
concurrent, multi-replica app servers without collisions and without a DB round-trip per write.

---

## Design brief

**Why bother — what's wrong with Phase 1's approach?** Random + retry works, but: (a) it costs a DB
INSERT attempt per shorten, on the write path; (b) as the table fills, conflict probability rises and
retries climb — a latency cliff exactly when you're busiest; (c) generation and storage are entangled.
KGS decouples them: **generate keys offline, in bulk, once; assign them at write time for free.**

**The core mechanism — batch-lease (a.k.a. "ticket server" / segment allocation):**

```
key_pool table:  code (PK) │ leased BOOL
                 pre-filled with millions of random base62 codes

App replica startup / low-water:
   ┌─ lease a BATCH of N codes from KGS ──┐
   │  UPDATE ... SET leased=true           │  (atomic, SKIP LOCKED)
   │  RETURNING code                        │
   └─ push N codes into an in-memory deque ┘
Shorten request: pop one code from the deque   ← zero DB round-trip
When deque < low-water mark: lease another batch (async, before it empties)
```

**The concurrency crux — `SELECT ... FOR UPDATE SKIP LOCKED`.** Two replicas leasing simultaneously
must never get the same code. Naïve `SELECT ... LIMIT N` then `UPDATE` races. The Postgres idiom:

```sql
UPDATE key_pool
SET leased = true
WHERE code IN (
  SELECT code FROM key_pool
  WHERE leased = false
  ORDER BY code
  FOR UPDATE SKIP LOCKED         -- ← the whole trick
  LIMIT :batch
)
RETURNING code;
```

`SKIP LOCKED` lets concurrent leasers grab *different* unlocked rows instead of blocking on each other —
turning a serialized bottleneck into parallel throughput. This exact pattern is also how you'd build a
Postgres-backed job queue; worth knowing cold.

**Failure modes — reason about each:**
- **Replica crashes with un-popped leased codes** → those codes are marked `leased` but never used.
  *Wasted, not corrupted.* Acceptable — you have 3.5T codes. Do **not** build a reclaim/GC path
  (gold-plating). Note it and move on.
- **KGS is a SPOF.** Yes. Mitigation: it's stateless over the pool table, so run **2+ replicas**
  behind the LB (they coordinate purely through `SKIP LOCKED`). No leader election needed — that's the
  elegance. (The Grokking doc's "standby" is the weaker single-primary version; multi-replica-via-DB is
  better and you get it almost free.)
- **Pool runs low.** A **refiller** (cron/worker) tops up `key_pool` with fresh random codes when
  unleased count drops below a threshold. Generation checks uniqueness via the PK on insert.

**The alternative you're NOT picking — counter + base62 encode (Snowflake / Flickr ticket server).**
Take a monotonic 64-bit counter (DB sequence, or Snowflake's timestamp+worker+seq), base62-encode it.
Zero storage for a pool, perfectly collision-free, trivially scalable. **Why not here?** Sequential
counters produce **predictable, enumerable** codes — an attacker walks `aaaa1, aaaa2, …` and scrapes
every URL. That violates the "not guessable" non-functional requirement. KGS pre-generates *random*
codes, so the assigned sequence is unguessable even though allocation is cheap. Be ready to argue this
tradeoff both directions — it's a favorite interview fork. (Mitigation if you *did* want counters:
encrypt/permute the counter, e.g. Feistel/`hashids`, to scramble order. Mention it; don't build it.)

---

## Build tasks

1. **`key_pool` table + seed.** Migration for `key_pool(code PK, leased bool default false)`. A seed
   script inserting a few million random base62 codes (batch inserts, `ON CONFLICT DO NOTHING`).
2. **`services/kgs`.** A small FastAPI service exposing `POST /lease?n=BATCH` → returns N codes using
   the `SKIP LOCKED` update above, in one transaction.
3. **Client-side pool in the API.** In `services/api`, a `KeyProvider` that holds an in-memory deque,
   pops per-shorten, and **refills asynchronously at a low-water mark** (don't block the shorten request
   waiting on a lease — pre-fetch). Make it thread/async-safe (one lock around the deque).
4. **Rip out the retry loop.** Shorten now = pop a guaranteed-unique code + INSERT. No `ON CONFLICT`
   needed anymore (assert it, don't silently keep it). This is the payoff — write path simplifies.
5. **Refiller.** A worker (or `kgs` background task) that tops up `key_pool` when unleased count < threshold.
6. **Observability.** Metrics: `keys_leased_total`, `key_pool_unleased` (gauge), `key_batches_per_shorten`
   (should be ≪ 1 — that's the proof you removed the per-write DB round-trip), local-deque depth.
7. **Concurrency stress test.** Spin **multiple API replicas** (compose `--scale api=4`) hammering
   shorten. Assert: **zero duplicate codes** issued across all replicas. This is the phase's核心 test.

---

## Definition of done

- 4 API replicas shortening concurrently issue **zero duplicate codes** (proven by a test that collects
  all issued codes and checks uniqueness).
- Shorten no longer does per-request conflict retries; `key_batches_per_shorten ≪ 1`.
- Kill one KGS replica mid-load → shortening continues (the other serves leases).
- Refiller keeps `key_pool_unleased` above threshold under sustained write load.
- `RESULTS.md` updated with write-path latency before/after (should drop or flatten).

## Scope guardrails (do NOT build)

- ❌ No leased-key reclamation/GC on crash — wasted keys are acceptable.
- ❌ No leader election / Zookeeper / etcd — `SKIP LOCKED` *is* your coordination.
- ❌ No counter/Snowflake path — you're deliberately choosing pre-generated random. (Discuss it, don't build it.)
- ❌ No custom-alias support yet (Phase 7) — even though KGS makes you think about the code namespace.
- ❌ Don't move `key_pool` to Redis — keep it in Postgres; the durability + `SKIP LOCKED` are the point.

---

## Staff-eng review checklist

- Is the lease query **actually** using `FOR UPDATE SKIP LOCKED`, in a single transaction? (A
  `SELECT` then separate `UPDATE` is a race — reject it.)
- Does refill happen **before** the deque empties (low-water), or does a shorten stall waiting on a
  lease? (Blocking refill reintroduces the DB round-trip you were removing.)
- Is the in-memory deque access thread/async-safe across concurrent requests in one replica?
- On graceful shutdown, are un-popped codes just dropped (fine) or is someone tempted to write reclaim
  code (don't)?
- SPOF story: can you run N KGS replicas with no extra coordination? Prove it in the stress test.
- Does the write path still have a leftover `ON CONFLICT` "just in case"? Remove it — dead code hides bugs.

## Interview framing

"How do you generate unique short codes at scale, across many servers?" → this is *the* meat question.
Ladder: random+retry (Ph1, has a latency cliff) → **KGS pre-generated + batch-lease** (this phase) →
counter/Snowflake (cheaper, but predictable ⇒ the guessability tradeoff). Mentioning `SKIP LOCKED`,
batch-lease-to-amortize-round-trips, orphaned-keys-are-acceptable, and multi-replica-coordination-via-DB
is a strong senior signal. The interviewer's follow-up is almost always "isn't KGS a SPOF?" — have the
multi-replica answer ready.

## Capstone connection

KGS becomes its own deployable (`services/kgs`) — your first real microservice split, and your first
taste of the operational cost of one (separate deploy, separate scaling, a new failure domain). In
Phase 4 the codes it hands out must stay unique *across shards* — which they do, because KGS is
global and shard-agnostic. In Phase 6 it's a separate Deployment on EKS with its own HPA.
