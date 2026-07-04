# Phase 4 — Sharding the URL store (consistent hashing)

> **Handoff:** experienced ML engineer, building TinyURL to learn distributed systems. FastAPI /
> Postgres / Redis / Docker / EKS. Hints before answers; staff-eng review (correctness → failure →
> scale → cost → observability); name the road not taken; block gold-plating. See `00-ROADMAP.md`.

**Starting state:** Phases 1–3 done — cached monolith, KGS handing out globally-unique codes, single
Postgres.
**Ships:** the URL store split across **multiple Postgres shards**, with the app routing each code to
its shard via a **consistent-hash ring**. Redirects and shortens become single-shard operations.
**The one concept:** **data partitioning** — the ring, virtual nodes, rebalancing blast radius, and
why "no relationships" (your earlier question) is what makes this clean.

**Pedagogy this phase: break-then-fix.** First shard with **naïve modulo** and *measure* how many keys
move when you add a shard (~all of them). Then switch to the ring and measure again (~1/N). Feeling
that difference is the entire lesson — it's abstract until you watch 80% of your rows migrate.

---

## Design brief

**Why now.** 30B rows / 15TB doesn't fit one Postgres box, and one box caps your write throughput and
is a single failure domain. Partition by the **short_code** (your only lookup key), so every read is
`code → shard → PK lookup` — one shard, one row, no fan-out. This only works because there are **no
cross-record relationships** (your earlier question made concrete): no JOINs means no query ever needs
two shards. That property *is* the license to shard freely.

**Three partitioning schemes — know all three, pick the third:**

1. **Range-based** (codes `a*`→shard0, `b*`→shard1…): predictable, but **hotspots** — if `e*` codes
   are common, that shard melts. The doc's own example. Reject for user-facing keys.
2. **Hash-modulo** (`hash(code) % N`): even distribution, but `N` is welded into every placement.
   Add a shard → `% N` becomes `% (N+1)` → **~N/(N+1) of all keys remap** → a full-table migration
   under live traffic. This is what you'll *demonstrate breaking*.
3. **Consistent hashing:** hash nodes and keys onto a ring (0…2³²); a key belongs to the first node
   clockwise. Add a node → only the keys in **that node's arc move (~1/N)**, everyone else untouched.
   This is the pick.

**Virtual nodes — the part people skip and regret.** A few physical shards placed randomly on the ring
carve **uneven arcs** ⇒ uneven load — the exact problem you were escaping. Fix: hash each physical
shard onto the ring at **V spots** (V ≈ 100–200). Its territory becomes V small scattered arcs ⇒ load
evens out, and when a shard dies its V arcs each spill to a *different* neighbor (no single successor
eats the whole load). Vnodes also let you **weight by capacity** — a bigger box gets more vnodes.

**The ring lookup (what you're building in `libs/shard_ring`):**

```
ring: sorted list of (vnode_hash → physical_shard)
get_shard(code):
    h = hash(code)
    i = bisect_right(sorted_vnode_hashes, h)   # first vnode clockwise
    if i == len: i = 0                          # ← wraparound past 2³² → back to start
    return shard_of[sorted_vnode_hashes[i]]
```

**Hint on the one tricky bit:** the wraparound. A key hashing *past* the last vnode belongs to the
*first* vnode (the ring closes). `bisect` returning `len(list)` must map back to index 0. Get this
wrong and a thin slice of the keyspace routes to nowhere. Test it explicitly with a key hashing above
your max vnode.

**Where KGS fits:** unchanged. It hands out globally-unique codes shard-agnostically; the app decides
*which shard stores* a given code at write time via the ring. Uniqueness is global (KGS), placement is
by ring — clean separation.

**What you're deliberately NOT solving:** live, online rebalancing (a daemon that migrates data when
you add a shard with zero downtime). That's a genuinely hard distributed-systems project and pure
gold-plating for a learning build. You'll *demonstrate* the blast radius with a one-shot offline
migration script, and *discuss* how real systems (Dynamo, Cassandra, Vitess) do online resharding.

---

## Build tasks

1. **`libs/shard_ring`.** Implement `HashRing`: `add_node`, `remove_node`, `get_node(key)`, with vnodes
   (V configurable) and a stable hash (e.g. blake2/xxhash → int). ~40–60 lines. Unit-test: even-ish
   distribution across nodes for random keys; **wraparound case**; adding a node moves ≈1/N keys.
2. **Two/three shards in compose.** `pg-shard-0`, `pg-shard-1`(, `-2`). Each gets the `urls` schema
   (same migration, run per shard). Config: a list of shard DSNs via env.
3. **Shard router in the API.** A `ShardedUrlRepo` that, given a code, calls `ring.get_node(code)` →
   picks that shard's connection pool → does the PK lookup / insert. The rest of the app doesn't know
   sharding exists (repository pattern — your clean-architecture practice pays off here).
4. **BREAK IT FIRST.** Implement modulo routing, seed K codes, snapshot code→shard mapping. Add a
   shard, recompute, and **measure the % of keys whose shard changed.** Record it (~all).
5. **FIX IT.** Swap in the ring. Repeat the exact experiment: add a shard, measure % moved (~1/N).
   Put both numbers side by side in `RESULTS.md`. *That table is the deliverable.*
6. **Offline migration script.** Given old-ring and new-ring, move only the affected rows between
   shards. One-shot, run-with-writes-paused — no online magic. This makes the "~1/N moves" concrete and
   real, not just simulated.
7. **Cache interaction.** Redis (Phase 2) is keyed by `code`, independent of which shard backs it — so
   the cache layer is unaffected. Confirm a redirect still: Redis → miss → *correct shard* → populate.

---

## Definition of done

- `RESULTS.md` has the **modulo-vs-ring rebalance table** (% keys moved on adding a shard). This is the
  proof the concept landed.
- Redirects/shortens work transparently across shards; the app code above the repo is shard-unaware.
- Ring unit tests pass including wraparound and roughly-even distribution.
- Killing one shard fails *only* redirects for codes on that shard (blast radius is bounded — verify).
- KGS-issued codes remain globally unique and land on deterministic shards.

## Scope guardrails (do NOT build)

- ❌ No online/live rebalancing daemon. One-shot offline script only.
- ❌ No cross-shard queries/JOINs — there's nothing to join (that's the whole point).
- ❌ No shard-level replication/failover yet (Phase 6: RDS handles replicas). One primary per shard now.
- ❌ Don't shard Redis (Phase 6: ElastiCache cluster). Cache stays single-node locally.
- ❌ Don't over-parameterize the ring (pluggable hash strategies, etc.) — one good hash, ship it.

---

## Staff-eng review checklist

- **Wraparound:** does a key hashing above the max vnode correctly route to the first node? Show the test.
- Are vnodes actually implemented, or is it naïve consistent hashing with lumpy arcs? (Check distribution
  spread across shards — a >~20% imbalance means too few vnodes.)
- Is the router behind a clean repository boundary, or is sharding logic leaking into request handlers?
- Is the shard chosen from `code` **consistently** on read and write? (A mismatch = data written to
  shard A, looked up on shard B = phantom 404s. This is the deadliest sharding bug — trace it.)
- Connection-pool-per-shard: are you exhausting file descriptors / conns with N shards × pool size?
- Does the modulo-vs-ring experiment use the *same* keys and *same* add-a-shard operation, or is the
  comparison apples-to-oranges?

## Interview framing

"How do you shard this to billions of rows?" → shard by `code` (single-key access, no JOINs ⇒ shards
freely), reject range (hotspots) and modulo (full remap on scaling), pick **consistent hashing +
vnodes**. The killer follow-up: *"you're at 80% disk on every shard, add capacity — what happens to
availability?"* Modulo → you're migrating ~80% of 30B rows online, latency spikes everywhere. Ring →
~1/N moves, bounded blast radius. Saying that, with the vnode + weighting detail, is a strong staff
signal. This is Cassandra's/Dynamo's partitioner — name-drop it.

## Capstone connection

The ring becomes your **shard router**, reused for the ElastiCache cluster in Phase 6 (same
`key → node` primitive routes cache traffic too). In Phase 6 the shards become RDS instances (or a
single RDS with read replicas if cost matters — a real tradeoff to weigh then). Your Phase-3 KGS
already guarantees the global uniqueness that makes cross-shard placement safe.
