# Phases 5–7 — Charters (flesh out on arrival)

> **Handoff:** experienced ML engineer, building TinyURL to learn distributed systems. FastAPI /
> Postgres / Redis / Docker / EKS. Hints before answers; staff-eng review; name the road not taken;
> block gold-plating. See `00-ROADMAP.md`.

These are **intentionally lighter than Phases 1–4** — goal, concept, the key tasks, guardrails, and the
interview hook. We deliberately don't over-specify decisions that depend on what Phases 1–4 taught you.
When you reach one, start a fresh chat with this file + your repo state and say *"expand Phase N into a
full spec like the earlier phases, then hint me through task 1."*

---

## Phase 5 — Async click telemetry

**Starting state:** Phases 1–4 — cached, KGS-fed, sharded shortener.
**Ships:** click tracking that does **not** slow the redirect. Every 302 emits an event; a separate
consumer aggregates counts + metadata off the hot path.
**The one concept:** **write-behind / fan-out via a stream** — decoupling the hot path from analytics,
with at-least-once delivery and idempotent aggregation.

**Why this is where `302` pays off:** because Phase 1 chose `302`, every click returns to your server,
so every click is an emittable event. Had you chosen `301`, repeat clicks would be browser-cached and
invisible — no analytics possible. Call this out; it's the through-line.

**Design brief:**
- The redirect handler does its normal fast path, then **fire-and-forget** an event to **Redis Streams**
  (`XADD`) — bounded, non-blocking, must never add latency or fail the redirect. A `telemetry` worker
  reads via a **consumer group** (`XREADGROUP`), aggregates into a counts store, `XACK`s.
- **The hot-URL contention problem** (the doc's own worry): a synchronous `UPDATE clicks=clicks+1` on a
  viral URL serializes thousands of concurrent writes on one row — lock contention meltdown. Async
  aggregation dodges it: events queue, the worker batches increments. This contrast is the interview meat.
- **Delivery semantics:** streams give at-least-once ⇒ the consumer must be **idempotent** or tolerate
  double-counts (event id dedup, or accept approximate counts — decide based on whether analytics needs
  exactness; usually "approximate is fine" is the right, cheaper call).
- **Backpressure:** cap the stream (`MAXLEN ~`); if the consumer falls behind, you drop the oldest
  events rather than OOM. Analytics is lossy-tolerant; the redirect is not. Prioritize accordingly.

**Alternatives to name:** synchronous counter (contention — rejected); Kafka (durable, partitioned,
right at real scale, but heavy to run locally — note as the AWS-era upgrade → Kinesis/MSK/SQS in Phase 6);
a separate analytics DB / OLAP (overkill now).

**Key tasks:** `XADD` in the redirect path (guarded, non-blocking) → `services/telemetry` consumer-group
worker → aggregate store (a `clicks` table per shard, or a dedicated counts store) → expose a
`GET /api/urls/{code}/stats`. Track: events emitted vs consumed (lag), consumer throughput.

**Guardrails:** ❌ no Kafka locally, ❌ no exact-once gymnastics unless analytics truly needs it,
❌ no per-click row forever (aggregate — don't store 50B click rows), ❌ telemetry failure must never
break redirects (test: kill the consumer, redirects unaffected, events buffer).

**Interview hook:** "How do you count clicks on a URL that's going viral without melting your DB?" →
async fan-out via stream + batched idempotent aggregation, lossy-tolerant with bounded backpressure;
contrast the synchronous-counter contention trap.

---

## Phase 6 — EKS deployment

**Starting state:** Phases 1–5 — full local system in Docker Compose (`api`, `kgs`, `telemetry`,
Redis, N Postgres shards).
**Ships:** the whole thing on **EKS**, state on managed services.
**The one concept:** mapping a local compose topology to **Kubernetes + managed AWS services**, with
health probes, autoscaling, LB tiers, IaC, and a cost model.

**Why the earlier choices pay off:** env-driven config (Phase 1) makes services portable with zero code
change; `/healthz` + `/readyz` (Phase 1) become k8s liveness/readiness probes directly; stateless app
tier (all state externalized) is what lets HPA scale replicas freely.

**Design brief / mapping:**
| Local (compose) | EKS / AWS |
|---|---|
| `api`, `kgs`, `telemetry` containers | Deployments (each with an HPA) |
| Postgres shards | RDS instances *(or one RDS + read replicas — weigh cost vs isolation)* |
| Redis | ElastiCache (cluster mode if you sharded cache) |
| ports | Service + ALB Ingress |
| `.env` | ConfigMaps + Secrets (or SSM/Secrets Manager) |
| Redis Streams | keep, **or** graduate to SQS/Kinesis (decide by cost/ops) |

- **LB tiers** (the doc's three places): ALB (clients→api), and internal routing api→shards (your ring,
  app-side) + api→cache. Discuss round-robin vs least-conn vs P2C at the ALB; consistent hashing stays
  app-side for cache/shards.
- **Autoscaling:** HPA on the **stateless** api/telemetry tiers (CPU or RPS). KGS scales too (coordination
  is via `SKIP LOCKED`, so replicas are safe). Shards do **not** autoscale — stateful, capacity-planned.
- **IaC:** Terraform or CDK for the cluster + managed services. Helm or Kustomize for the workloads.

**Key tasks:** Dockerfiles per service (multi-stage, slim) → k8s manifests/Helm (Deployments, Services,
Ingress, HPA, ConfigMaps/Secrets, probes) → provision RDS/ElastiCache/ALB via IaC → migrations as a k8s
Job → smoke + load test against the cluster → **write a monthly cost estimate.**

**Guardrails:** ❌ single region only, ❌ no service mesh (Istio/Linkerd), ❌ no multi-cluster, ❌ no
blue-green/canary yet, ❌ don't self-host Postgres/Redis on k8s (use managed) — the point is the mapping
and the ops model, not running databases on k8s the hard way.

**Interview hook:** "Deploy this to production" → stateless tiers behind an ALB with HPA, state on
managed services, ring stays app-side for shard/cache routing, probes wired to health endpoints, plus a
cost sentence. Knowing *what not to autoscale* (shards) and *why KGS replicas are safe* is the signal.

---

## Phase 7 — HTMX UI + hardening

**Starting state:** Phases 1–6 — deployed system, API-complete.
**Ships:** a minimal **FastAPI + HTMX/Jinja** UI (create form, custom alias + expiry, per-link stats
dashboard) plus the production-hardening the API's been deferring.
**The concepts:** **rate limiting** (token bucket in Redis — the `api_dev_key` abuse control),
end-to-end **observability**, and finally-implementing **custom aliases** + expiry UX.

**Design brief:**
- **UI:** server-rendered Jinja + HTMX partials — no SPA, no build step. A create form (returns the short
  URL inline via HTMX swap), a link list with expiry, and a stats page reading Phase-5 aggregates.
- **Custom aliases** (deferred since Phase 1): user-supplied code → must check availability against the
  KGS/pool namespace and reserve atomically (same `ON CONFLICT`/pool-reservation discipline). Enforce the
  16-char cap from the doc. This is where the code namespace finally gets a second writer besides KGS —
  reason about the collision surface between user aliases and pool codes.
- **Rate limiting:** token-bucket per `api_dev_key` in Redis (atomic via Lua or `INCR`+`EXPIRE`), separate
  quotas for create vs redirect. This is the doc's abuse-prevention. Where does it sit — edge (ALB/WAF) or
  app? Discuss; implement app-side thin slice.
- **Observability:** Prometheus metrics (you've been adding these since Phase 2 — now a Grafana dashboard),
  OpenTelemetry traces across api→kgs→shard→cache→stream, structured logs with request/trace ids.

**Key tasks:** Jinja/HTMX templates + endpoints → custom-alias reserve flow → Redis token-bucket limiter
+ 429s → OTel tracing wired through all services → a Grafana dashboard (hit-rate, p99, shard balance,
KGS pool depth, stream lag) → basic API-key issuance (thin — not full OAuth).

**Guardrails:** ❌ no React/SPA (HTMX only), ❌ no full auth/OAuth/user management (thin API keys only),
❌ no WAF/DDoS deep-dive, ❌ no A/B or feature-flag infra, ❌ don't gold-plate the dashboard — the five
metrics that prove the system works, not fifty.

**Interview hook:** "How do you prevent abuse?" → per-key token bucket in Redis, distinct create/redirect
quotas, edge-vs-app placement tradeoff. "How do you know it's healthy?" → the five-metric dashboard +
distributed tracing across the service graph. Custom aliases → the namespace-collision reasoning against
the pre-generated pool.

---

## After Phase 7 — where to go if you want more

Deliberately out of scope for the core build, but natural next system-design reps on the same app:
- **Multi-region / geo-routing** (latency + DR) — the "highly available" NFR taken seriously.
- **Analytics at real scale** — swap Redis Streams → Kafka/Kinesis, aggregate into an OLAP store.
- **CDC / cache coherence** — invalidate caches from the DB changelog instead of app-side.
- **Online resharding** — the hard version of Phase 4 you deliberately skipped.

Pick one as a *new* Grokking-style problem rather than bolting onto this — each is its own capstone.
