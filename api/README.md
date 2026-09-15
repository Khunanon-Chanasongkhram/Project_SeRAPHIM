# SeRAPHIM SOS API

Cloudflare Worker + D1. The **only always-on server** in the system — everything else is
static files on a CDN. It does one job: accept and triage requests for help.

> ⚠️ **Not an official emergency channel.** Every response carries the 1784 / 191 notice.
> This holds **sensitive personal data** under Thailand's PDPA.

## Two implementations, one specification

| | Runtime | Purpose |
|---|---|---|
| `src/index.js` | Cloudflare Worker | **production** |
| `../workers/seraphim/devserver.py` | Python stdlib + SQLite | local dev, no node required |

They must agree. `conformance/severity.json` is the referee — hand-reasoned cases that
both are tested against, plus tests that diff the need codes and thresholds out of the
JS source. **If the two ever disagree, the conformance file is right.**

D1 *is* SQLite, so the dev server executes the real `schema.sql`: the constraints,
indexes and queries exercised locally are the production ones.

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/sos` | public | submit; rate limited; requires consent |
| GET | `/api/public/summary` | public | **anonymised** district counts, no PII |
| GET | `/api/needs` | public | need codes for the form |
| GET | `/api/sos` | responder | triage queue, re-sorted by age-escalated severity |
| GET | `/api/sos/:id` | responder | detail — **logs the access** |
| PATCH | `/api/sos/:id` | responder | status / severity / assignment — audited |
| GET | `/api/clusters` | responder | "one soi, one boat" dispatch hints |
| — | cron `17 3 * * *` | — | PDPA purge of expired rows |

## PDPA

- **Consent** recorded with a timestamp on every row; submission is refused without it.
- **Retention** — every row carries `purge_after` (90 days) and deletes itself on a cron.
- **Access logging** — every read of personal data writes to `access_log`, which is
  retained *longer* than the data it describes: it is the evidence that access was
  controlled, so it must outlive the records.
- **Minimisation** — the public summary strips all PII and coarsens coordinates.
- **Tokens** are stored only as SHA-256; raw IPs are hashed, never stored.

## Deploy

```bash
npm i -g wrangler          # or use npx
wrangler d1 create seraphim-sos              # put the id into wrangler.toml
wrangler d1 execute seraphim-sos --file=./schema.sql --remote
wrangler secret put IP_SALT
wrangler deploy
```

Creating the first responder (manual allowlist — the trust ladder starts here):

```sql
-- token_hash is SHA-256 of the token you hand out. Never store the raw token.
INSERT INTO actors (id, token_hash, role, name, org, created_at)
VALUES ('a1', '<sha256-of-token>', 'official', 'Name', 'Org', unixepoch()*1000);
```

## Local development (no node needed)

```bash
cd workers
python3 -m seraphim.devserver --port 8788 --seed --token-file /tmp/tok
# citizen form:   web/sos.html?api=http://127.0.0.1:8788
# ops console:    web/ops.html   (paste the token)
```

## Not implemented yet

- **SMS / USSD intake** — needs a Thai telco or Twilio agreement. The `source` column
  already accepts `'sms'`, so intake can be added without a schema change.
- **`.go.th` magic-link sign-in** — needs an email provider. Manual allowlist works today.
- **Load testing under spike conditions** — Phase 6.
- The Worker has **not been executed** in this environment (no node available). Its logic
  is held to the conformance suite and its structure validated, but the first real run
  will be your `wrangler dev`.
