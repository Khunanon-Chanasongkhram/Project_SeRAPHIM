# Does the prediction actually predict?

The map tells people a river reaches its bank in about N hours. Until this was written,
nothing measured whether that was true. This is that measurement.

It runs on every build, over the archive the risk engine already keeps, and publishes
`validation.json` alongside the snapshot. The headline goes into `meta.json` so the UI can
show it next to the prediction it describes.

```bash
cd workers && python3 -m seraphim.cli build --out ../data   # runs it
```

## Method

Every archived reading with at least four prior readings becomes an origin. A trend is
fitted using **only data that existed at that moment**, a level is predicted at each lead
time, and it is compared with the observed series interpolated to that exact time.

**No cut points are chosen by hand.** Every eligible origin is used, so the result cannot
be improved by picking convenient moments.

### Persistence is the baseline that matters

Persistence is the naive forecast "the level stays exactly where it is". It is free, needs
no trend, and is surprisingly hard to beat over short horizons.

**Skill** is the fraction of persistence's error that the forecast removes. Positive means
the forecast helps. **Negative means it is worse than doing nothing**, in which case
time-to-bank should be withdrawn rather than dressed in a confidence label.

### The "moving rivers" split

Predicting a flat river correctly is not skill. Most Thai gauges barely move on a given
day, and they dominate the raw median, making the engine look better than it is at the job
it exists for. So every figure is also reported for cases where the river actually changed
by at least 5 cm over the lead time.

## Results (first run, 2026-09-15)

```
   lead      n  stns   median  persist  skill   |  moving n   median  skill
     1h  1,195   413   0.009m   0.010m    +7%   |       210   0.069m   +35%
     2h    783   400   0.020m   0.013m   -49%   |       210   0.086m   +35%
     3h    783   400   0.029m   0.020m   -46%   |       262   0.091m   +40%
```

**Read the two halves separately, because they say opposite things.**

Across all rivers the forecast is worse than persistence beyond an hour. On rivers that
are actually moving it is consistently 35 to 40 percent better.

Both are true, and the reconciliation is simple: on a flat river persistence is exactly
right and extrapolating a small noisy trend only adds error. Time-to-bank already refuses
to fire below 1 cm/hr, so **it operates entirely in the regime where the forecast has
skill.** The negative column is a warning about extrapolating flat rivers, not about the
feature as shipped.

### Bank warnings

```
100 made, 11 resolvable, 7 came true   hit rate 64%
median timing error 3.06 h
```

Eleven is far too few to conclude anything, and a three hour timing error on predictions
in the zero to twelve hour range is large. Treat the hit rate as a placeholder that will
firm up as the archive grows.

## What this changed in the code

**A `steady` confidence tier**, added because the measurement showed the tiers were not
ordered: `poor` was predicting better than `fair`.

The cause was a selection effect. A flat river scores a low R2 because there is no signal
to fit, not because the fit is bad, and a flat river is trivially easy to predict. Those
stations were filed under `poor` and quietly flattering it. Splitting them out let the
remaining tiers mean what they claim:

```
before                      after
good   0.008m  p90 0.101    good   0.018m  p90 0.157
fair   0.020m  p90 0.174    fair   0.036m  p90 0.239
poor   0.011m  p90 0.065    poor   0.031m  p90 0.260
                            steady 0.007m  p90 0.032   (1,284 flat rivers)
```

By p90 the moving tiers now order correctly. By median `fair` and `poor` are still close,
on a small sample for `poor`.

## A bug this process caught in itself

The first version matched each prediction to the **nearest observation** within half an
hour. The numbers looked plausible. They were not.

738 of the real observation gaps are exactly 180 minutes, so origin+3h always landed on a
reading while origin+2h always landed between two and scored nothing at all. Each lead was
being measured on a different population of stations, which made the leads incomparable.
Interpolating the observed series to the exact target time removed the artifact, and the
2h row appeared.

Worth remembering: the first version of a measurement can be wrong in a way that looks
like a result.

## What this is not

Accuracy against our own archive, sampled at whatever cadence we happen to poll, over a
window measured in hours. It is **not a hydrological validation**, no hydrologist has
reviewed it, and it says nothing about places with no gauge.

It answers one narrow question honestly: given the readings we have, does the arithmetic
beat doing nothing? Right now, on rivers that are moving, yes.
