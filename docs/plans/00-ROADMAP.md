# TinyURL — Build Roadmap (READ FIRST)

This is the master plan for building a TinyURL-style URL shortener **evolutionarily**, from a
local monolith to a Kubernetes (EKS) deployment with a minimal UI. It's a learning vehicle for
backend distributed-systems design, worked through by implementing — not reading.

Each `phase-N-*.md` is a **self-contained handoff**: you can drop it into a brand-new, empty
project chat and the assistant can pick up cold. This file is the shared context they all point back to.

---

## Handoff header (paste-worthy context for any new chat)

> You're helping an experienced ML / ML-Engineer (6+ yrs, production ML + GenAI: RAG, fine-tuning,
> forecasting, inference orchestration) deliberately level up on **backend distributed-systems design**
> by building TinyURL end-to-end. Assume **no** prior chat context beyond these plan docs.
>
> **Stack:** Python, FastAPI, Postgres, Redis, Docker, Kubernetes/**EKS** (cloud target), AWS.
> Comfortable with clean architecture, SOLID, design patterns (just finished the Alberta
> Software Design & Architecture specialization) — wants to *apply* those here, not relearn them.
>
> **Skip:** ML/Python basics, framework tutorials. **Spend depth on:** sharding, caching strategies,
> consistency, queues, WebSockets, indexing, fan-out, capacity estimation, k8s.
>
> **How to help:**
> - **Bias to building.** Give design vocabulary + tradeoffs, then have *them* implement in their stack.
> - **Hints before answers.** Do NOT dump full solutions unless explicitly asked. When they're stuck,
>   point at the tricky part and leave the fill-in to them.
> - **Review like a staff engineer:** correctness first, then production-readiness — failure modes,
>   scaling bottlenecks, observability, cost. Always name the alternative not picked and why it matters.
> - **Keep them honest on scope.** Push the thin slice, block gold-plating. If they're building
>   something this phase's "Scope guardrails" says to skip, call it out.
> - Prefer concrete code + diagrams over generic prose. Use their stack in examples.

---

## The evolution (one new concept per phase)

| Phase | Ships | Headline concept | Pedagogy |
|------:|-------|------------------|----------|
| **1** | Local monolith: create + redirect | Read/write asymmetry, `301` vs `302`, indexing, 12-factor/k8s-readiness | Build correct, baseline load test |
| **2** | Redis read cache on redirect path | Cache-aside, TTL, invalidation, stampede, hit-rate | **Break-then-fix** (measure DB offload) |
| **3** | Key Generation Service (KGS) | Distributed ID allocation, `SKIP LOCKED` batch-lease, SPOF | Build correct + concurrency stress test |
| **4** | Sharded URL store | Consistent hashing, vnodes, rebalancing, shard router | **Break-then-fix** (measure rebalance blast radius) |
| **5** | Async click telemetry *(charter)* | Fan-out, write-behind, streams, consumer groups, idempotency | TBD when we arrive |
| **6** | EKS deployment *(charter)* | Managed-service mapping, LB tiers, HPA, IaC, cost | TBD |
| **7** | HTMX UI + hardening *(charter)* | Rate limiting, observability, custom alias/expiry | TBD |

> Phases 5–7 are intentionally **charters** (goal + concept + guardrails), not full specs. We flesh
> each out when we get there — deliberately, to avoid over-planning decisions that depend on what
> phases 1–4 teach. See `phase-5-7-charters.md`.

---

## Capacity targets (the numbers we design against)

From the Grokking estimates — keep these on the wall; every phase sizes *its* component against them:

- **Writes (new URLs):** ~200/s  (500M/month)
- **Reads (redirects):** ~20K/s  (100:1 read:write)
- **Storage:** ~30B objects over 5yr ≈ 15 TB
- **Hot cache:** 20% of daily reads ≈ 170 GB
- **Keyspace:** base62, 7 chars ⇒ 62⁷ ≈ 3.5 trillion (comfortable headroom over 30B, keeps keyspace sparse ⇒ hard to guess)

Locally you won't hit 20K/s, but **every phase has a load-test target** scaled down proportionally
(e.g. "sustain 2K redirects/s on my laptop with p99 < 25ms"). Sizing each slice against these numbers
*is* the capacity-estimation practice.

---

## Repo layout (grows into this; don't scaffold it all in Phase 1)

```
tinyurl/
├── services/
│   ├── api/            # main FastAPI app (Phase 1)
│   ├── kgs/            # Key Generation Service (Phase 3)
│   └── telemetry/      # async click consumer (Phase 5)
├── libs/
│   └── shard_ring/     # consistent-hash ring (Phase 4)
├── deploy/
│   ├── compose/        # docker-compose for local (Phase 1+)
│   └── k8s/            # manifests / Helm (Phase 6)
├── loadtest/           # k6 or Locust scenarios (Phase 1+)
├── docs/               # these plan docs
└── Makefile            # up / down / test / load / migrate
```

**Phase 1 only creates `services/api`, `deploy/compose`, `loadtest`, `Makefile`.** Everything else
appears when its phase does. Resist scaffolding empty dirs "for later" — that's gold-plating.

---

## Global conventions (decided once, here)

- **Config:** 12-factor, all via env vars (`pydantic-settings`). No hardcoded hosts. This is what makes
  the app tier stateless and EKS-portable — the payoff lands in Phase 6.
- **Health endpoints from day 1:** `/healthz` (liveness — process up) and `/readyz` (readiness — deps
  reachable). k8s probes consume these later; wiring them now costs nothing.
- **Migrations:** Alembic from Phase 1. Never hand-edit schema.
- **Redirects:** `302` (temporary), not `301`. Rationale in Phase 1 — it's the analytics-vs-latency
  decision the whole telemetry phase depends on.
- **Short codes:** base62 (`[A-Za-z0-9]`), **not** base64 — `+` and `/` aren't URL-safe (`/` is a path
  separator). 7 chars.
- **Observability:** structured JSON logs from Phase 1; a `/metrics` Prometheus endpoint appears in
  Phase 2 (first thing worth measuring is cache hit-rate).
- **Testing:** pytest. Each phase adds tests for its new behavior + keeps the redirect/create contract green.

---

## Definition of done (applies to every phase)

A phase is **shippable** when:
1. The thin slice works end-to-end (`make up` → the new capability functions).
2. A load test at the phase's stated target **passes** (latency + throughput).
3. The new concept is **observable** — there's a metric/log that *proves* it's doing its job
   (e.g. cache hit-rate > X%, keys leased per DB round-trip, % keys moved on rebalance).
4. The **scope guardrails** were respected (you didn't build the next phase early).
5. Tests green; `README` for the service says how to run + what this phase added.

If all five aren't true, it's not done. If you're tempted to add a sixth thing not on the list —
that's the gold-plating instinct; write it down as a "later" note and move on.

---

## How to start any phase in a fresh chat

1. New project, empty. Paste the **Handoff header** above.
2. Attach the relevant `phase-N-*.md`.
3. Say: *"I'm starting Phase N. Here's my current repo state: [paste tree / link]. Review my plan
   for the first task and hint me toward the tricky part — don't write it for me."*
4. Work task-by-task. Ask for staff-eng review when a task's code is drafted.
