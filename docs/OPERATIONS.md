# Operations — capacity, health, degraded mode

Phase 6. Measured with `python3 -m tests.loadtest` from `workers/`, 2026-09-15.

## The finding that matters

**A national-scale event exceeds the free Cloudflare Worker tier.**

| Scenario | Affected | Map opens | SOS requests | Worker requests | Free tier (100k/day) |
|---|---|---|---|---|---|
| Provincial flood | 500k | 100,000 | 5,000 | 15,000 | ✅ fits |
| Regional flood | 3M | 450,000 | 24,000 | 72,000 | ✅ fits |
| **National (2011 scale)** | 13.6M | 1,632,000 | 68,000 | **204,000** | ❌ **exceeds** |

The read path is fine at every scale — 311 GB of CDN egress on Pages, which is unmetered,
because map reads are static files that never reach an origin. **Only SOS submissions
touch the Worker**, and that is what runs out.

**The fix costs $5/month.** Cloudflare's Workers Paid plan raises the limit to 10M
requests/month, roughly 50× what a national event needs. Nothing in the architecture
changes. Budget it before flood season rather than during one — this is the single
cheapest thing standing between the system and the day it is needed most.

D1 storage is not a constraint: a national event stores ~40 MB of the 500 MB free.

## Health

`meta.json` carries a `health` block with an explicit verdict, so a monitor reads one
field instead of re-deriving judgement:

| Check | Threshold | Why |
|---|---|---|
| `station_count` | ≥ 800 (normally ~1,121) | a large drop means a silent upstream break |
| `sources` | all adapters ok | a dead feed must be visible, not inferred |
| `data_freshness` | median reading age ≤ 180 min | warn |
| `reporting_rate` | ≤ 60% of stations stale | warn |
| `snapshot_stale_after_minutes` | 90 min | cron runs every 30 min; 3 misses is a fault |

`status` is `pass`, `warn` or `fail`. **The dangerous failure is the quiet one** — the
pipeline keeps running, the map keeps rendering, and the data behind it stopped being
true hours ago. Every check names what is wrong in words someone can act on.

## Degraded mode

Each layer fails toward still-being-useful:

| Failure | Behaviour |
|---|---|
| Origin / snapshot unreachable | service worker serves the last cached snapshot; an amber banner states it is old and how old |
| Snapshot older than 90 min | same banner, with the failing health checks listed |
| Browser offline | banner says so explicitly and keeps the last known map |
| No cached snapshot at all | clear failure message pointing at 1784 / 191 — never a blank page |
| SOS API unreachable | submissions queue in IndexedDB and retry every 45 s and on reconnect |
| Rate limited (429) | treated as a retry, not a loss — the request stays queued |
| Ops API unreachable | the queue is live data with no useful cached version, so the console says so rather than leaving stale requests looking current |

## Rate limiting, and why it is shaped this way

Load testing found the original design would have **silently blocked flood victims**.

A single per-IP limit of 12 per 10 minutes looks reasonable until you remember that Thai
mobile networks use carrier-grade NAT: thousands of subscribers share one public address.
A limit tight enough to stop an abuser blocks an entire neighbourhood — and the people
behind a shared mobile NAT are disproportionately those with no landline to call 1784 from.

So there are two buckets, protecting against different things:

| Bucket | Limit / 10 min | Reasoning |
|---|---|---|
| Device (`dev:`) | 8 | the real actor; one browser submitting 40 times is broken or malicious |
| IP (`ip:`) | 300 | a shared resource; stops one machine hammering, does not police a carrier |
| IP, life-threatening | 900 | a real mass-casualty event in one soi looks exactly like abuse |

Verified: 400 distinct devices behind one NAT reporting `medical` **all get through**;
one device spamming is stopped at 8.

A false accept costs one triage review. A false reject can cost a life. The asymmetry
decides the design.

## Running the load test

```bash
cd workers
python3 -m tests.loadtest                                   # defaults
python3 -m tests.loadtest --submitters 400 --seconds 10
```

It asserts data integrity (every accepted request is stored) and prints latency
percentiles, throughput, and the capacity table above.

## Still open

- **SMS / USSD intake** — needs a Thai telco or Twilio agreement. `source` already
  accepts `'sms'`, so intake slots in without a schema change.
- **The Worker has never been executed** here (no node). Its logic is held to
  `api/conformance/`; the first real run is `wrangler dev`.
- **Real-network load testing** against a deployed Worker, rather than against the
  Python reference implementation.
