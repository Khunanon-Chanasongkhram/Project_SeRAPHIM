# SeRAPHIM — project context

Thai-first flood intelligence + emergency coordination platform. Scales globally & to other
hazards. **This is a real product for real users**, not a demo — reliability, Thai PDPA, data
licensing and safety disclaimers are in scope.

## Read these first
- `docs/PLAN.md` — the master plan: architecture, risk engine, phases, risks
- `PROGRESS.md` — **running work log; read at the start of every session, append at the end**
- `docs/DATA_SOURCES.md` — verified endpoints, what's tested vs unverified

## Stack — static-first, $0/month
Python workers on **GitHub Actions cron** → static snapshot JSON + PMTiles → **Cloudflare Pages/R2**
→ MapLibre GL static site. **Cloudflare Worker + D1 for SOS writes only.**
No runtime database on the monitoring/prediction path.

## Rules that matter here

1. **Never invent a data endpoint.** If it isn't marked ✅ tested in `docs/DATA_SOURCES.md`,
   probe it with curl first and record the result. Wrong water data is dangerous, not just buggy.
2. **One canonical datum.** All water levels stored as metres above MSL. Convert at the source
   adapter boundary, never in the UI. Mixing m and m-MSL produces plausible, wrong, dangerous numbers.
3. **Every risk score ships with its reasoning.** No bare colour badges. Show inputs and the "why".
4. **Uncertainty is displayed, not hidden.** Time-to-bank is linear extrapolation — label it an
   estimate, degrade confidence visibly on stale/sparse data.
5. **Reads are static files on a CDN; SOS writes are the only live server path.** The spike is
   the whole point — a million map views must cost $0 and touch no origin.
6. **The site must stay statically pre-rendered.** SSR would burn the 100k/day Worker cap on
   page views. Never introduce per-request server rendering on public pages.
7. **Always display data age.** Actions cron drifts 5–20 min; the UI must never imply "live".
8. **SOS data is sensitive personal data.** Encryption, RBAC, retention, audit — from migration #1.
9. **Never claim official authority.** Disclaimer + "call 1784 / 191" on every emergency surface.
10. **Build behind `SourceAdapter`** even with one implementation, so Phase 7 (global) isn't a rewrite.

## Commands
(filled in as Phase 0 lands)
