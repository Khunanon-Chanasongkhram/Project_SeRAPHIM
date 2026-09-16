# SeRAPHIM

System for early Real-time Assessment & Predictive Hazard Incident Monitoring.

A flood watching map. It reads **~16,000 public river gauges across Thailand, the UK,
the Netherlands and the United States**,
mixes in rain and tide forecasts, and tries to answer one question: how long until this
river comes over its bank?

It watches. It does not collect anything about you, and it cannot call anyone for help.

When nothing is flooding it turns into a fishing planner, because the same tide and moon
data answers a much nicer question.

## Vibe code alert

Please read this part before anything else.

I built this fast, mostly by talking to an AI, over a handful of sessions. It is a
personal side project. It is not a product, it is not an official service, and nobody
qualified has checked it.

So:

* It has never run in production. No real user, no real flood, not once.
* No hydrologist or emergency responder has looked at it. The risk scoring is a set of
  rules I wrote. I can explain every one of them, but that is not the same as being
  right.
* The data comes from other people's APIs. Gauges break, feeds go down, forecasts are
  forecasts. I try to show you when the data is old or missing, but I cannot show you
  what I do not know about.
* "Time to bank" assumes the river keeps rising at exactly the rate it has been rising.
  Rivers do not agree to that.
* I have not confirmed the terms of use for the Thai water data yet. See
  `docs/DATA_SOURCES.md`.

Do not make a safety decision because of this map. If the water is rising where you are,
trust your eyes first, then the authorities, then maybe this.

**In an emergency in Thailand call 1784 (DDPM) or 191. Not this.**

It is AGPL so you can read exactly what it does instead of taking my word for it. If you
find something wrong, please open an issue. I would rather know.

## Why it exists

I am doing a PhD, and some weeks the research goes nowhere. You read, you write, you
rerun everything, and at the end of the week you cannot point at anything and say "I
made that." It gets to you after a while.

So I needed a thing to build. Something with an end of the day, where I could see
progress and nobody was going to review it. This is that thing. It is my stress relief
project, which is a strange thing to say about a flood warning system, but here we are.

I picked floods because Thailand floods every year and I grew up with it in the news.
And because when I went looking, the data was already there. Over a thousand gauges,
updating all day, sitting in a public API that almost nothing is built on top of. Most of
the dashboards I found show you a number and a colour. Almost none of them will say the
thing you actually want to hear:

> This bank goes over in about 3.3 hours at the current rate, and high tide at 18:40 will
> stop it draining.

That sentence is the whole idea. Whether I got anywhere near it is still an open
question, since none of this has been tested where it counts.

## What it does

**Watch.** 1,117 Thai, 3,327 British, 11,467 American and 318 Dutch live river gauges,
rainfall, discharge forecasts, official US river forecasts, and tide prediction at
23 points along both coasts, on one map. Satellite imagery by default with a one tap flip
to a street map, Thai and English, and it refreshes itself every 15 minutes without a
reload.

**Layers you can tick on and off.** Province risk shading, gauges, tide points, animated
rain radar, earthquakes from the past 24 hours, active fires, and geolocated world radio
with a tuner dial you can sweep. The radio has nothing to do with floods. It is there
because it is fun, and because this is a project for enjoying.

**Where, not just what.** All 77 provinces are shaded by the worst gauge in them, so you
can see where the trouble is before you start tapping dots.

**Predict.** A risk score from 1 to 5 for every district in all 77 provinces, with time
to bank where the trend is solid enough to show one. It also checks whether a high tide
is about to block drainage, which is a big part of why Bangkok floods the way it does.
Every score comes with the reasons behind it, in Thai and English, so you can disagree
with it.

**Fish.** Tide, moon and weather for 40 spots, sea and reservoir and river. Partly
because it is useful, and partly because an app you open twice in your life is an app
you do not have installed when you need it.

## Does it work?

I finally measured it instead of guessing. Every build now backtests its own predictions
against what actually happened and publishes the result.

On rivers that are actually moving, the forecast is about **35 to 40 percent better than
assuming the level does not change**, with a typical error around 9 cm at a 3 hour lead.
On flat rivers it is worse than doing nothing, which is why time-to-bank refuses to fire
below 1 cm/hr and never sees that case.

Bank warnings: 7 of 11 came true so far, with a median timing error of 3 hours. Eleven is
far too small a sample to mean much, and I would not lean on that number yet.

The measurement also found a real defect and fixed it: the confidence labels were not
ordered, because flat rivers were being filed as "poor" and making that tier look good.
Details, method and the bug I found in my own first attempt at measuring are in
[`docs/VALIDATION.md`](docs/VALIDATION.md).

## Status

Built, tested and deployed: ingest, the risk engine, calm mode, terrain, validation, and
four countries with an opt-in live refresh. Terrain currently profiles Thai gauges only.

`PROGRESS.md` has the full build log, including the bugs. I kept the mistakes in there on
purpose, including a few where I broke something and my own test caught it.

## Running it

Python 3.11 or newer. No dependencies, no npm, no build step.

```bash
# pull live data and build a snapshot
cd workers
python3 -m seraphim.cli build --out ../data

# tests
python3 -m unittest discover -s tests -v

# look at it
cd .. && python3 -m http.server 8000
# then open http://localhost:8000/web/
```

To put it online, `docs/DEPLOY.md` walks through it. It takes about five minutes and
needs nothing but turning on GitHub Pages. No credit card, no account anywhere else,
nothing to install.

## What is where

| Path | What |
|---|---|
| `docs/DEPLOY.md` | how to get it online, start here |
| `docs/PLAN.md` | the plan, the phases, what could go wrong |
| `docs/SECURITY.md` | security review, six findings, all fixed |
| `docs/OPERATIONS.md` | capacity, health checks, what happens when things break |
| `docs/DATA_SOURCES.md` | every API, tested or not, with the date I checked |
| `docs/VALIDATION.md` | whether the predictions are any good, measured |
| `docs/TERRAIN.md` | where water would go, and the two wrong versions |
| `workers/seraphim/` | the Python that fetches, scores and publishes |
| `web/` | the map and the fishing page |
| `scripts/check_js.py` | scans inline JS and translations, since there is no Node here |

## Where the data comes from

* **ThaiWater / HII** for the river gauges. Terms of use not confirmed yet, so do not
  put this in front of the public until that is sorted.
* **Open-Meteo** for rain, river discharge and tide. CC-BY 4.0, non commercial.
* **USGS** for earthquakes, keyless.
* **RainViewer** for global rain radar, keyless.
* **Radio Browser** for geolocated stations, keyless.
* **NASA FIRMS** for active fires. Optional: needs a free key, added as a repo secret so
  it never reaches a browser. Without it the layer is just absent.
* **Esri World Imagery** for satellite, **OpenStreetMap** and **CARTO** for maps.
* Basemap from OpenStreetMap and CARTO.

Everything in `docs/DATA_SOURCES.md` says whether I actually tested it and when. I did
not take any of it from documentation alone, because a wrong water level is worse than no
water level.

## Terrain, and the two versions of it that were wrong

There is a layer showing low ground near gauges whose channel is spilling. It says
**where water would go, never how deep**, and that restraint took three attempts.

Subtracting a DEM from the water level looked right and produced impossible answers: two
of four test stations had the river flowing several metres underground, because a 90 m
DEM cell at a canal gate contains the embankment, not the water. Using the gauge as a
relative reference instead turned a 0.4 m overtopping into a claim of 3.4 m of water,
because gauges sit on the high ground and the delta beside them is low.

So it makes no depth claim at all. It needed no DEM download and no raster library, which
matters because this machine has no numpy, no GDAL and no pip. Details, including the
numbers that killed the first two versions, are in [`docs/TERRAIN.md`](docs/TERRAIN.md).

## Four countries, and what each one taught me

**Thailand** publishes a bank level for every gauge. **The UK does not.** It publishes a
typical operating range, which is a different claim, and its levels come in four
different datums in the same feed.

So UK stations get a weaker signal that says so: above or below typical range, capped
below the levels that mean "the river is out", and **no time to bank at all**, because
you cannot forecast reaching a threshold that nobody published. The map says this in the
popup rather than quietly showing a lower number.

**The United States does publish one.** NOAA's flood stage is the level at which water
leaves the channel, the same kind of number as Thailand's bank level, so **US gauges get
the full treatment**: freeboard, time to bank, and level 5. The risk engine needed no
change for that, because its caps key on whether a threshold exists, not on which country
a gauge is in. Before trusting it I checked our computed freeboard against NWS's own
flood category across 6,797 gauges: **100.00% agreement**.

America also broke the forecast budget. 11,467 gauges took the shared Open-Meteo grid
from 1,029 cells to 7,284, roughly 36,000 calls a day against a 10,000 allowance. The
fix was realising Open-Meteo is the *downgrade* there: NOAA publishes a per-gauge
24-hour river forecast, which costs two calls no matter how many gauges you ask about.

**The Netherlands tried to hand me readings from 1740.** Its "latest observations"
endpoint returns the latest value of *every series it has ever held*, live telemetry and
centuries-old archives together, distinguished only by a timestamp. Unfiltered, that puts
286-year-old marks on a live flood map, each looking perfectly ordinary. A hard recency
gate drops them; what remains is 318 gauges with a **median age of 22 minutes**, the
freshest data in the project.

It also nearly cost me real data the other way. Dutch gauges reading 119 m above datum
look impossible in a country famous for being flat, until you find that the one doing it
gauges a valley in South Limburg that genuinely sits that high. The plausibility check is
built against Dutch terrain, not against the stereotype.

That was the real test of the adapter design from day one, and the interesting part is
that it held: the risk engine already treated a missing bank level as missing rather than
zero, so nothing had to be unpicked.

## Live data, without a server

Reads are static files on a CDN, and that is what makes a million map views cost nothing.
So "live" here does not mean putting a server in front of the map, it means **the browser
asking the agency directly**, exactly as it already does for rain radar. The origin still
serves nothing but files.

It is **off by default and opt-in**, because turning it on points your browser and your
IP at a foreign government API and costs you a few hundred KB per refresh. Neither should
happen to you without your say-so.

A live reading is allowed to do very little: it replaces the level and recomputes
freeboard, and it may **escalate** a gauge to "over bank", because that is an observation.
It may **never de-escalate**, because the published score can rest on a forecast, a tide
window or a trend that one reading knows nothing about. The panel shows a live level
beside a score stamped with the build it came from, rather than blending the two.

Thailand, the UK and the US allow it. **Rijkswaterstaat sends no CORS header, so the
Netherlands cannot be refreshed from a browser at all** — and the map says so, instead of
offering a toggle that silently does nothing.

## Credit where it is due

The idea of putting a lot of live public feeds on one globe and letting you tick them on
and off comes from [gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view) by
Bilawal Sidhu and Sameh Khamis, which is MIT licensed. The radio tuner, the earthquake
layer, the active fire layer and the Esri plus OSM basemap stack are all their ideas, and
several of the APIs here are ones they found first. SeRAPHIM shares no code with it, but
it would look quite different without it.

What is different here: this one is about water in Thailand. The gauges, the risk scoring,
time to bank, the tide work and the fishing planner are its own.

## Licence

AGPL-3.0. If someone runs a changed version of this as a public service, the people
relying on it should be able to see what it really does. That seems like the right rule
for something that claims to warn you about floods.

Same goes here. Read it before you trust it.
