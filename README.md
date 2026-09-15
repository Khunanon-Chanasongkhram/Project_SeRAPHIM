# SeRAPHIM

System for early Real-time Assessment & Predictive Hazard Incident Monitoring.

A flood watching map for Thailand. It reads 1,121 public river gauges, mixes in rain and
tide forecasts, and tries to answer one question: how long until this river comes over
its bank?

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

**Watch.** 1,121 live river gauges, rainfall, discharge forecasts, and tide prediction at
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

## Status

Phases 0, 1, 2, 3 and 6 are built and tested locally. Terrain (phase 5) and going global
(phase 7) are not started.

There used to be an SOS side, where people could ask for help and responders could triage
it. I built it, then took it out before deploying. Collecting someone's location, phone
number and medical needs during an emergency is a serious thing to be responsible for,
and a hobby project with nobody on call is the wrong place for it. The code is still in
git history on the `sos-component` branch if it ever finds a proper home.

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
