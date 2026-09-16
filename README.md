# SeRAPHIM

System for early Real-time Assessment & Predictive Hazard Incident Monitoring.

A flood watching map. It reads **about 16,000 public river gauges across Thailand, the
UK, the Netherlands and the United States**, mixes in rain and tide forecasts, and tries
to answer one question: how long until this river comes over its bank?

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

**Watch.** About 1,100 Thai, 3,300 British, 11,500 American and 320 Dutch live river
gauges, rainfall, discharge forecasts, official US river forecasts, and tide prediction at
23 points along both coasts, on one map. Satellite imagery by default, with street and
plain map styles in the layers panel. Thai and English. It refreshes itself every 15
minutes without a reload.

**Reservoirs.** 493 Thai dams, large and medium, coloured by how full they are. Storage
is a percentage of *usable* capacity, so above 100% is normal and common in the monsoon:
it means the reservoir is above its normal full level and is probably spilling. It does
not mean a dam is failing, and the popup says so every time.

**Layers you can tick on and off.** Province risk shading, gauges, dams, tide points, animated
rain radar, earthquakes from the past 24 hours, active fires, and geolocated world radio
with a tuner dial you can sweep. The radio has nothing to do with floods. It is there
because it is fun, and because this is a project for enjoying.

**Where, not just what.** In Thailand all 77 provinces are shaded by the worst gauge in
them, so you can see where the trouble is before you start tapping dots. Elsewhere the
map ranks the worst districts and catchments in the panel instead.

**3D terrain, and a forecast timeline.** Turn on 3D and the map gets real elevation
(Copernicus/SRTM, the same DEM family the gauge terrain profiles use), exaggerated 1.6x
because the delta that actually floods is almost flat. A scrub bar steps through
**now → +1h → +3h → +6h → +12h**, recolouring every gauge by its own projected level, and
then through **+3d → +7d → +30d** on river flow.

**It is not a flood simulation, and it does not pretend to be.** Nothing spreads across
the ground when you drag the slider. What moves is each gauge's own water surface, which
is a number this project measures its own accuracy on. Simulating where water actually
goes needs a hydraulic model, a 30 m DEM, channel cross-sections and flood defences, and
inventing that on top of 90 m elevation samples would produce a confident, beautiful,
wrong flood map. The day and week steps are flow rather than level for the same reason:
converting discharge to metres needs a rating curve nobody publishes.

**Watch a gauge, and get told.** Tap a gauge and choose Watch, and your browser alerts
you when it escalates — even when the page is closed. The watch list lives on your device
and **nothing is sent anywhere**: the page already downloads the same public snapshot
every minute, and the alert is your own browser reacting to it. Permission is asked for
when you add your first watch, never on load, and if you have notifications blocked the
alert shows as a banner instead of vanishing.

**Predict, in hours and in weeks.** Every gauge with a trustworthy trend carries a
projected level at +1, +3, +6 and +12 hours, and rivers that GloFAS can see carry a
30-day outlook. The outlook is river *flow*, never a water level, because converting one
to the other needs a rating curve nobody publishes.

**Predict.** A risk score from 1 to 5 for roughly 3,000 districts and catchments across
the four countries, with time to bank where the trend is solid enough to show one. It
also checks whether a high tide is about to block drainage, which is a big part of why
Bangkok floods the way it does.
Every score comes with the reasons behind it, in Thai and English, so you can disagree
with it.

**Flood history, and ground that floods again and again.** Every gauge can tell you how
unusual today is *for that particular river*: where its flow sits in twelve years of its
own record, how many days a year it normally runs high, when its worst year was, which
months are its flood season, and how much bigger a rare flood is than an ordinary one.
There is also a layer of **reported past floods** — 445 of them, 2005 to 2026, including
the Thailand flood of 2011 that ran for 158 days.

**And when it is predicted.** Where the 30-day outlook reaches a flow this river only
reaches about once every two or five years, the gauge gets a ring and the panel says how
many of them there are and how many days away. That is the difference between "flow is
going up" and "flow is heading for a level this place sees once every five years".

**This is flow, not flooding, and the distinction is the whole point.** It is a modelled
river flow (GloFAS, ~5 km) compared against its own past. It is not a measurement that
anywhere flooded, it carries no flood outline, and it says nothing about depth. It is
being filled in a few hundred grid cells at a time and currently covers part of Thailand;
everywhere else says so plainly rather than showing you an empty map and letting you
conclude there is nothing there.

**A second opinion, from Google.** Google's Flood Hub publishes its own flood severity
forecast for points across 150-odd countries. Where it is available it is drawn as
hollow rings beside our gauges — deliberately *beside*, not merged in, so where Google
and this map disagree you can see that they disagree instead of getting one blended
number that hides it. It carries severity, trend and a forecast change range, and no
water level, so none is shown.

This layer needs an API key and Google's access is currently waitlisted, so unless one
is configured the layer is simply absent and the map says so.

**Fish.** A **7-day** planner for 40 spots, sea and reservoir and river. Each day shows
its best hours and its peak score on the button, so you can see which day to go without
tapping through the week. Alongside the score: wind and gusts, wind direction, cloud,
rain, air temperature and the 6-hour pressure change, because the score tells you when
fish might feed and those tell you whether you should be out there at all. Partly because
it is useful, and partly because an app you open twice in your life is an app you do not
have installed when you need it.

## Live mode

The map rebuilds itself every 15 minutes. On top of that, **Live is on by default**: your
browser fetches gauge readings **straight from the agency** every two minutes, instead of
waiting for the next rebuild. There is a switch in the bottom panel to turn it off, and
the first time it runs it tells you which agency it is talking to.

Two things worth knowing, which is why you are told rather than left to find out:

* Your browser talks directly to a foreign government's servers, so they see your
  IP address.
* It costs you data. Roughly 300 KB per refresh for Thailand, 90 KB for the US,
  350 KB for the UK. It pauses while the tab is in the background, so a forgotten tab
  is not quietly downloading a national feed all week.

Live mode works for Thailand, the UK and the US. **It cannot work for the Netherlands**,
because Rijkswaterstaat's servers refuse requests from web pages. The switch says so
there rather than pretending.

One thing it deliberately will not do: a live reading can push a gauge **up** to "over
bank", because that is something being measured right now. It will never push one
**down**. The published score may be high because of a rainfall forecast or an incoming
tide, and a single new reading knows nothing about either. So you see the live level
next to the score, with the time the score was worked out, rather than the two blended
into one number you cannot take apart.

## What it will not tell you

Limits that are easy to miss, and worth knowing before you read anything off the map.

**It will not tell you how deep the water will be.** There is a layer showing low ground
near gauges that are spilling. It shows **where** water would go, never how deep it would
be there. Depth depends on how much water arrives and for how long, and this does not
know either. I tried twice to make it say a depth and both attempts produced confident
nonsense, so it says nothing.

**It cannot give a time to bank everywhere.** That number needs a published bank level,
and not every country publishes one. Thailand and the United States do. The UK publishes
only a "normal range", and the Netherlands publishes no threshold at all, so gauges there
show a level and a trend and nothing more. The map says so in the popup instead of
quietly showing you a smaller number and letting you assume it means the same thing.

**Not every gauge has a bank level, even in Thailand.** 318 of the 1,118 Thai stations
publish no overtopping threshold, and the feed marks that absence with a zero. Reading
that zero as a real bank level is exactly the bug that once had this map showing 300-odd
Thai rivers over their banks when almost none were. Those stations now show a level and a
trend and nothing more.

**The forecast deliberately under-promises.** It assumes the current rate of rise fades
rather than continuing forever, because the version that assumed otherwise was measurably
*worse than predicting no change at all* beyond an hour. The cost is that it will not
give a time-to-bank for a river far below its bank creeping up slowly. It would rather
say nothing than say a number that is out by most of a day.

**Accuracy is measured per country, and mostly it is not measured yet.** The skill
figures come from replaying our own archive, and that archive only really covers Thailand
so far. Where a country has not been measured, the panel says so instead of showing you a
number earned on Thai rivers.

**Flood history is a record of high *flow*, not of floods.** The history behind each
gauge is a modelled river discharge compared against its own twelve-year record. It can
tell you this river is carrying more water than it usually does in a given year, which is
a real thing to know. It cannot tell you that anywhere flooded, where the water went, or
how deep it got. "Past floods" is a separate layer of events somebody actually reported,
and each of those is a point saying *reported here*, not an outline of what flooded.

**Flood-prone is about decades, not about today.** A place is flood-prone in the dry
season too. The layer ranks ground by how long its river spends unusually high across
twelve years; it is deliberately drawn soft and wide and sits underneath the gauges, so
it cannot be mistaken for something happening this morning.

**Google's forecast is Google's, and it may disagree with this one.** That is the
reason it is there. It is not a check that this map is right, and where the two differ
neither is automatically the correct one.

**A quiet gauge is not a safe gauge.** If a gauge stops reporting it is marked stale
rather than dropped, because a gauge that goes silent during a flood is telling you
something. Every reading on the map carries its age.

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
| `docs/TERRAIN.md` | the low-ground layer, and why it makes no depth claim |
| `workers/seraphim/` | the Python that fetches, scores and publishes |
| `web/` | the map and the fishing page |
| `scripts/check_js.py` | scans inline JS and translations, since there is no Node here |

## Where the data comes from

* **ThaiWater / HII** for the Thai river gauges. Terms of use not confirmed yet, so do
  not put this in front of the public until that is sorted.
* **UK Environment Agency** for the British gauges. Open Government Licence, keyless.
* **NOAA / US National Weather Service** for the American gauges, their flood stages and
  their own 24-hour river forecasts. Public domain, keyless.
* **Rijkswaterstaat** for the Dutch gauges. Dutch open data, keyless.
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
