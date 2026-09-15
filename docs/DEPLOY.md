# Deploy

Everything runs on free tiers. **You do not need Node, npm or wrangler on your own
machine** — the Worker is deployed from CI. You need a GitHub account (you have one)
and a Cloudflare account (free, no card).

Work through this in order. Each step ends with something you can check.

---

## What you are building

```
GitHub Actions (cron */30)  ──►  Cloudflare R2        (snapshots: JSON + GeoJSON)
                                       │
Cloudflare Pages (web/) ───────────────┘   reads snapshots over the CDN
        │
        └─►  Cloudflare Worker + D1   (SOS submissions only)
```

Three Cloudflare pieces: **R2** (data), **Pages** (site), **Worker + D1** (SOS).

---

## 1 · Push to GitHub

You have no SSH key and no `gh` CLI, so use HTTPS with a Personal Access Token.

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained** →
   **Generate new token**. Repository access: *Only select repositories* (you will pick
   the new repo after creating it). Permissions: **Contents: Read and write**.
2. Create an **empty private repo** named `Project_SeRAPHIM` (no README, no .gitignore).
3. Then:

```bash
cd /home/khunanon/Mini_Project/Project_SeRAPHIM
git remote add origin https://github.com/<you>/Project_SeRAPHIM.git
git push -u origin main          # username = your GitHub name, password = the token
```

Optional, so you are not asked every push:
```bash
git config credential.helper 'store --file ~/.git-credentials-seraphim'
```

**Check:** the repo shows 51 files and 7 commits. The **Actions** tab shows `ingest`
starting automatically (it runs on push). It will pass and publish nothing yet — no R2
secrets exist, so the publish step skips itself by design.

---

## 2 · Cloudflare R2 — where snapshots live

Sign up at dash.cloudflare.com (free). Then **R2 → Create bucket**, name it
`seraphim-snapshots`, location Automatic.

**Make it publicly readable:**
bucket → **Settings → Public access → R2.dev subdomain → Allow Access**.
Copy the URL — it looks like `https://pub-<hash>.r2.dev`.

> R2.dev is rate-limited and Cloudflare calls it development-grade. Fine to start; move
> to a custom domain when you have one.

**Add CORS**, or the browser blocks every fetch. Same Settings page → **CORS policy**:

```json
[{ "AllowedOrigins": ["*"],
   "AllowedMethods": ["GET", "HEAD"],
   "AllowedHeaders": ["*"],
   "MaxAgeSeconds": 3600 }]
```

**Create an API token:** R2 → **Manage API Tokens → Create API token**,
permission **Object Read & Write**, scoped to this bucket. Save the
**Access Key ID** and **Secret Access Key** — shown once.

Your **Account ID** is on the R2 overview page (and in the dashboard URL).

---

## 3 · GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `CF_ACCOUNT_ID` | your Cloudflare account id |
| `CF_R2_BUCKET` | `seraphim-snapshots` |
| `CF_R2_ACCESS_KEY_ID` | from step 2 |
| `CF_R2_SECRET_ACCESS_KEY` | from step 2 |

**Check:** Actions → `ingest` → **Run workflow**. The summary should report ~1,121
stations, and `https://pub-<hash>.r2.dev/latest/meta.json` should load in a browser.

> **Time-to-bank will be empty on the first runs.** It is computed from our own archive,
> which the cache carries between runs, so it needs roughly 1–2 hours of history before
> trends clear the 45-minute minimum span. This is expected, not a fault.

---

## 4 · D1 and the SOS Worker

**Create the database:** Cloudflare → **Workers & Pages → D1 → Create database**, name
it `seraphim-sos`. Copy the **Database ID**.

Paste it into `api/wrangler.toml`, replacing `PUT-YOUR-D1-DATABASE-ID-HERE`, then
commit and push.

**Create a Cloudflare API token** (different from the R2 one): dashboard → **My Profile
→ API Tokens → Create Token → Edit Cloudflare Workers** template, and add
**Account → D1 → Edit**. Add it to GitHub secrets as `CF_API_TOKEN`.

**Set the IP salt** — the Worker refuses to accept submissions without it, deliberately,
because the placeholder would make every stored IP hash reversible:

Workers & Pages → `seraphim-sos` → *(after first deploy)* **Settings → Variables →
Add variable → Encrypt**, name `IP_SALT`, value a long random string:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Deploy:** Actions → **deploy-api → Run workflow**, tick **apply_schema** the first
time. Note the `*.workers.dev` URL it prints.

**Check:** `https://seraphim-sos.<subdomain>.workers.dev/api/health` returns
`{"ok":true,...}` with the 1784 / 191 disclaimer attached.

---

## 5 · Your first responder account

```bash
python3 scripts/make_token.py --role official --name "Your Name" --scope "สมุทรปราการ"
```

It prints a token **once** and an `INSERT` statement. Run the SQL in
**D1 → seraphim-sos → Console**. Keep the token somewhere safe; only its SHA-256 is
stored, so it cannot be recovered.

Scope responders to a province unless they genuinely need the whole country.

---

## 6 · Cloudflare Pages — the site

**Workers & Pages → Create → Pages → Connect to Git**, authorise the Cloudflare GitHub
app for your private repo, select it. Build settings:

| Field | Value |
|---|---|
| Framework preset | **None** |
| Build command | *(leave empty)* |
| Build output directory | `web` |

Deploy. You get `https://<project>.pages.dev`.

---

## 7 · Point the site at your services

Edit **`web/config.js`** — the only file that changes:

```js
window.SERAPHIM = {
  dataBase: "https://pub-<hash>.r2.dev/latest",
  apiBase:  "https://seraphim-sos.<subdomain>.workers.dev",
};
```

Commit and push; Pages redeploys automatically.

---

## 8 · Verify

| Page | Expect |
|---|---|
| `/index.html` | ~1,121 stations, risk colours, a data-age banner that is not red |
| `/fish.html` | 40 spots, hourly chart, tide curve on coastal spots |
| `/sos.html` | submits and returns a reference id |
| `/ops.html` | your token signs in; the test submission appears |

Then **delete your test SOS request** from the D1 console so it is not sitting in a real
queue:

```sql
DELETE FROM sos_requests WHERE note LIKE '%test%';
```

---

## 9 · Watch the Actions minute budget — this one bites

Private repos get **2,000 Actions minutes/month** and **every job is rounded up to a
whole minute**. At `*/30` that is 1,440 runs/month, so the budget only holds while each
run finishes **under 60 seconds**. A run that takes 61 s is billed as 2 minutes and the
month costs 2,880.

After the first day: **Settings → Billing → Plans → Actions usage**.

- Comfortably under → leave it.
- Trending over → change the cron in `.github/workflows/ingest.yml` to `*/45`.
- Making the repo **public** removes the limit entirely (unlimited minutes) and lets you
  drop to `*/15` or `*/10`.

---

## Known limits at this point

- **A national-scale flood exceeds the free Worker tier** (~204k requests vs 100k/day).
  Provincial and regional events fit. The fix is the $5/month Workers Paid plan and no
  code changes. See [`OPERATIONS.md`](OPERATIONS.md).
- **R2.dev is development-grade.** Move to a custom domain before real users depend on it.
- **ThaiWater's terms of use are unconfirmed.** Contact HII before promoting this
  publicly, and keep the attribution visible.
- **The Worker has never run anywhere yet.** Its logic is held to `api/conformance/`,
  but step 4 is genuinely its first execution — check `/api/health` before trusting it.

## If something breaks

| Symptom | Cause |
|---|---|
| Map loads, no stations | `dataBase` wrong, or R2 CORS missing — check the browser console |
| Fetches blocked, no error shown | CSP in `web/_headers`; add your domain to `connect-src` |
| SOS returns 503 `misconfigured` | `IP_SALT` not set — that is the guard working |
| SOS returns 429 immediately | rate limit; see the buckets in `OPERATIONS.md` |
| Ops console rejects the token | wrong API URL, or the `INSERT` never ran |
| Time-to-bank always empty | fewer than ~2 h of archive yet, or the cache is not restoring |
| Cron silently stopped | scheduled workflows disable after 60 days idle; `keepalive.yml` guards this |
