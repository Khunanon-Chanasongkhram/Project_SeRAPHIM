# Deploy

The repo is public, so this is simpler than it used to be. **No credit card, and nothing
to install.** GitHub builds the data and hosts the site. Cloudflare is only needed if you
want the SOS side, and its free tier does not ask for a card.

Two parts, and the first one works on its own:

| Part | Where | Card? | Needed for |
|---|---|---|---|
| Map, fishing, data | GitHub Pages | no | everything except SOS |
| SOS form and console | Cloudflare Workers + D1 | no | the emergency side |

R2 is gone. It was the only piece that wanted a card, and since the site and its data now
ship together from the same place, there is no cross-origin fetch and no CORS to set up.

---

## Part 1: get the map live (about 5 minutes)

### 1. Turn on Pages

Repo → **Settings** → **Pages** (left sidebar) → under **Build and deployment**, set
**Source** to **GitHub Actions**. That is the whole step. Do not pick a branch.

### 2. Run it

Repo → **Actions** tab → **ingest** in the left list → **Run workflow** → **Run workflow**.

It takes two or three minutes. It fetches live data, runs the tests, computes risk,
bundles the pages and the snapshots together, and deploys.

If the Actions tab says workflows are disabled because it is a fork or a new public repo,
click the button to enable them.

### 3. Look at it

Your site is at:

```
https://<your-github-username>.github.io/Project_SeRAPHIM/
```

The exact URL is printed at the end of the workflow run, under the `deploy` job.

Check it properly:

```bash
python3 scripts/check_deploy.py --data https://<you>.github.io/Project_SeRAPHIM/data/out
```

Everything should pass except **time to bank**, which warns until the archive has built
up. That is expected and explained below.

**That is it for the map.** It now refreshes every 15 minutes on its own.

---

## About time to bank being empty at first

It will say zero stations for the first hour or two. Nothing is broken.

Time to bank is worked out from our own stored history, not from a single reading. The
workflow keeps that history in the Actions cache between runs, so it has to accumulate.
A trend needs at least 45 minutes of readings before it is allowed to publish a number,
which at a 15 minute cadence means roughly three or four runs.

Come back in two hours and there should be numbers.

---

## Part 2: the SOS side (optional, about 20 minutes)

Skip this if you only want the map. The map does not depend on it.

### 4. Cloudflare account

Sign up at dash.cloudflare.com. Email and password. It will push you to add a domain;
you do not need one, skip it. **Workers and D1 do not ask for a card.**

### 5. Make the database

Left sidebar → **Workers & Pages** → **D1 SQL Database** → **Create database**.
Name it `seraphim-sos`. Copy the **Database ID** it shows you.

Open `api/wrangler.toml`, replace `PUT-YOUR-D1-DATABASE-ID-HERE` with that ID, commit
and push.

### 6. API token

Cloudflare → your profile icon (top right) → **Profile** → **API Tokens** →
**Create Token** → use the **Edit Cloudflare Workers** template → then **add one more
permission**: Account, **D1**, **Edit**. Create it and copy the token.

In GitHub: repo → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**:

| Name | Value |
|---|---|
| `CF_API_TOKEN` | the token you just made |
| `CF_ACCOUNT_ID` | on the Cloudflare Workers overview page, and in the dashboard URL |

### 7. Deploy the Worker

Actions → **deploy-api** → **Run workflow**, and **tick `apply_schema`** the first time
so it creates the tables. Note the `*.workers.dev` URL it prints.

### 8. Set the secret salt

The Worker refuses to accept any submission until this is set, on purpose: the
placeholder value would make every stored IP hash reversible.

Make one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Cloudflare → **Workers & Pages** → `seraphim-sos` → **Settings** → **Variables and
Secrets** → **Add** → type **Secret**, name `IP_SALT`, paste the value. Save and deploy.

Check it:

```bash
python3 scripts/check_deploy.py --api https://seraphim-sos.<subdomain>.workers.dev
```

This confirms the Worker answers, that the 1784 / 191 notice rides on responses, that a
stranger cannot read the request queue, and that the public summary leaks no personal
data.

### 9. Point the site at the Worker

Repo → **Settings** → **Secrets and variables** → **Actions** → **Variables** tab →
**New repository variable**:

| Name | Value |
|---|---|
| `SERAPHIM_API_BASE` | `https://seraphim-sos.<subdomain>.workers.dev` |

A variable, not a secret, because it ends up in the page source anyway.

Re-run **ingest** so the site picks it up.

### 10. Give yourself an account

```bash
python3 scripts/make_token.py --role official --name "Your Name" --scope "สมุทรปราการ"
```

It prints a token once and an `INSERT` statement. Run that SQL in Cloudflare →
**D1** → `seraphim-sos` → **Console**. Keep the token somewhere safe; only its hash is
stored, so it cannot be recovered.

Scope people to a province unless they really need the whole country.

---

## Check everything

| Page | What you should see |
|---|---|
| `/` | about 1,121 stations, risk colours, a data age banner that is not red |
| `/fish.html` | 40 spots, hourly chart, tide curve on the coastal ones |
| `/sos.html` | submits and gives you a reference id |
| `/ops.html` | your token signs in, and your test submission is in the queue |

Then delete your test request, so it is not sitting in a real queue. Cloudflare → D1 →
Console:

```sql
DELETE FROM sos_requests WHERE note LIKE '%test%';
```

---

## Things worth knowing

**GitHub Pages has a soft bandwidth limit of 100 GB a month.** Fine for normal use and
for a provincial flood. A nationwide event would go past it, and GitHub throttles rather
than bills. If this ever gets real traffic, put Cloudflare in front of the Pages site or
move the site to Cloudflare Pages, which is unmetered. See `OPERATIONS.md`.

**Actions minutes are unlimited now** because the repo is public. That is why the cron
runs every 15 minutes instead of every 30.

**Scheduled workflows switch off after 60 days with no activity in the repo.** There is a
`keepalive` workflow that makes a tiny commit monthly to prevent that. If you ever see
the cron quietly stop, that is the first thing to check.

**The ThaiWater terms of use are still unconfirmed.** The repo is public now, so this
matters more than it did. Contact HII before you promote it anywhere, and leave the
attribution visible.

**The Worker has never run anywhere.** Step 7 is genuinely its first execution. Check
`/api/health` before trusting it with anything.

---

## When it breaks

| What you see | Usually means |
|---|---|
| 404 on the Pages URL | Source is not set to GitHub Actions, or `ingest` has not finished |
| Map loads but no stations | open the browser console; if the fetch 404s, check the deploy step copied `data/out` |
| Blocked requests in the console | the CSP in the page head; add the host to `connect-src` |
| Time to bank always empty | fewer than about two hours of runs, or the cache is not restoring |
| SOS returns 503 `misconfigured` | `IP_SALT` not set, which is the guard working |
| SOS returns 429 straight away | rate limit, see the buckets in `OPERATIONS.md` |
| Ops console rejects your token | wrong API URL, or the `INSERT` never ran |
| Cron silently stopped | 60 day inactivity rule, see `keepalive` above |
