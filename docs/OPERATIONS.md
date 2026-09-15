# Operations, capacity, health, degraded mode

Phase 6. Measured with `python3 -m tests.loadtest` from `workers/`, 2026-09-15.

> **Two things changed after this was written.** The SOS component was removed before
> deploy, so every section below about the Worker, D1, rate limiting and PDPA now
> describes code that lives only on the `sos-component` branch. It is kept because the
> reasoning still applies if it is ever revived.
>
> **Hosting also changed.** The site and its snapshots now ship
> together on **GitHub Pages** (public repo, no card, unlimited Actions minutes) rather
> than Cloudflare R2. That moves the read-path limit from "unmetered" to GitHub's
> **100 GB per month soft limit**, which is recomputed below. The SOS Worker is
> unchanged.

## The findings that matter

### Read path: GitHub Pages bandwidth

Station and area data is split per country, so a visitor downloads the one they are
looking at. Adding a country costs existing readers nothing, which is the only way this
scales past two. Per first visit: **Thailand 242 KB, UK 276 KB gzipped**.

Figures below use the Thai visit:

| Scenario | Map opens | Bandwidth | vs 100 GB/month |
|---|---|---|---|
| Provincial flood | 100,000 | ~19 GB | fits comfortably |
| Regional flood | 450,000 | ~86 GB | close to the line |
| National (2011 scale) | 1,632,000 | ~310 GB | **over, by 3x** |

GitHub throttles rather than bills, and it is a soft limit, so a one-off spike is more
likely to draw an email than an outage. Still, if this ever gets real traffic the fix is
to put Cloudflare in front of the Pages site, or move the site to Cloudflare Pages, whose
bandwidth is unmetered. Neither needs a code change.

### Write path: removed

There is no write path any more. Nothing is submitted, stored or served from an origin,
so the only capacity question left is the bandwidth above.

<details>
<summary>Historical: the SOS Worker capacity finding</summary>

**A national-scale event exceeded the free Cloudflare Worker tier.**

| Scenario | Affected | Map opens | SOS requests | Worker requests | Free tier (100k/day) |
|---|---|---|---|---|---|
| Provincial flood | 500k | 100,000 | 5,000 | 15,000 | ✅ fits |
| Regional flood | 3M | 450,000 | 24,000 | 72,000 | ✅ fits |
| **National (2011 scale)** | 13.6M | 1,632,000 | 68,000 | **204,000** | ❌ **exceeds** |

Map reads are static files and never touch the Worker. **Only SOS submissions do**, and
that is what runs out.

**The fix costs $5/month.** Cloudflare's Workers Paid plan raises the limit to 10M
requests/month, roughly 50× what a national event needs. Nothing in the architecture
changes. Budget it before flood season rather than during one, this is the single
cheapest thing standing between the system and the day it is needed most.

D1 storage is not a constraint: a national event stores ~40 MB of the 500 MB free.

</details>

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

`status` is `pass`, `warn` or `fail`. **The dangerous failure is the quiet one**, the
pipeline keeps running, the map keeps rendering, and the data behind it stopped being
true hours ago. Every check names what is wrong in words someone can act on.

## Degraded mode

Each layer fails toward still-being-useful:

| Failure | Behaviour |
|---|---|
| Origin / snapshot unreachable | service worker serves the last cached snapshot; an amber banner states it is old and how old |
| Snapshot older than 90 min | same banner, with the failing health checks listed |
| Browser offline | banner says so explicitly and keeps the last known map |
| No cached snapshot at all | clear failure message pointing at 1784 / 191 - never a blank page |
| SOS API unreachable | submissions queue in IndexedDB and retry every 45 s and on reconnect |
| Rate limited (429) | treated as a retry, not a loss - the request stays queued |
| Ops API unreachable | the queue is live data with no useful cached version, so the console says so rather than leaving stale requests looking current |

## Rate limiting, and why it was shaped this way (historical, SOS only)

Load testing found the original design would have **silently blocked flood victims**.

A single per-IP limit of 12 per 10 minutes looks reasonable until you remember that Thai
mobile networks use carrier-grade NAT: thousands of subscribers share one public address.
A limit tight enough to stop an abuser blocks an entire neighbourhood, and the people
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

- **SMS / USSD intake**, needs a Thai telco or Twilio agreement. `source` already
  accepts `'sms'`, so intake slots in without a schema change.
- **Real-network load testing** against a deployed Worker, rather than against the
  Python reference implementation.
