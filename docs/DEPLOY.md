# Deploy

The repo is public, so this is about as simple as deploying gets. **No credit card, no
account anywhere except GitHub, nothing to install.** GitHub Actions builds the data and
GitHub Pages serves it alongside the site.

Since the pages and the snapshots ship together from the same place, there is no
cross-origin fetch and no CORS to configure, which removes the most common way this sort
of setup breaks.

Three steps, about five minutes.

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

## Check it over

| Page | What you should see |
|---|---|
| `/` | about 1,121 stations, risk colours, a data age banner that is not red |
| `/fish.html` | 40 spots, hourly chart, tide curve on the coastal ones |

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

**There is no SOS side any more.** It was built and then removed before deploying, so
nothing here collects personal data or promises anyone a response. The code is on the
`sos-component` branch. See `docs/PLAN.md` for why.

---

## When it breaks

| What you see | Usually means |
|---|---|
| 404 on the Pages URL | Source is not set to GitHub Actions, or `ingest` has not finished |
| Map loads but no stations | open the browser console; if the fetch 404s, check the deploy step copied `data/out` |
| Blocked requests in the console | the CSP in the page head; add the host to `connect-src` |
| Time to bank always empty | fewer than about two hours of runs, or the cache is not restoring |
| Cron silently stopped | 60 day inactivity rule, see `keepalive` above |
