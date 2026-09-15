# Terrain: where water would go

Phase 5. The goal was to turn "this gauge reads 2.1 m" into "which ground floods". What
shipped is narrower than that, and the narrowing is the interesting part.

## Three approaches, two of them wrong

### 1. Subtract the DEM from the water level

Obvious, and wrong. A gauge reports metres above mean sea level, a DEM reports ground
elevation above mean sea level, so subtracting should give depth. Measured against four
over-bank Thai stations:

```
water 1.71 m,  ground 0 m   ->  +1.7 m   plausible
water 1.29 m,  ground 5 m   ->  -3.7 m   the river is flowing underground
water 1.68 m,  ground 4 m   ->  -2.3 m   likewise
water 144.31 m, ground 145 m -> -0.7 m   plausible
```

Two of four are impossible. Not a broken datum, a resolution problem: a 90 m DEM cell at
a canal gate contains the embankment and the buildings beside it, not the water surface,
so "ground elevation at the gauge" is often the top of the bank.

### 2. Use the gauge as a relative reference, then subtract

Better, still wrong. Sampling around one Samut Prakan gate returned every point within a
kilometre sitting 1 to 7 m **below** the gauge, because the canal is embanked above a
delta. Subtracting there turns a 0.4 m overtopping into a claim of 3.4 m of water.

That is not how overtopping works. Water spilling over a crest fills land at a rate set
by volume and time. This has neither, so any depth it produced would be invented.

### 3. Relative height, and no depth claim at all

What shipped. Ground is sampled at 8 bearings and 3 ranges (250 m, 500 m, 1 km) around
each gauge and expressed as height relative to the gauge. Points more than 1 m below it
are published, **but only for gauges whose channel is currently over its bank**, because
low ground beside a river that is well within its banks is just geography.

The output says **where water would go**. It never says how deep.

## What it cannot do

- **No flow routing.** It cannot know whether water actually reaches a point.
- **Nothing about defences**, culverts, pumps or gates.
- **~90 m DEM**, quantised to whole metres, so anything smaller is invisible.
- **Thailand only.** It needs a bank level to know the channel is spilling, and the UK
  network does not publish one, so British gauges are skipped entirely.

Read it as "this ground is low and the channel beside it is spilling", not as a flood
forecast.

## How it is built

Terrain does not change, so it is sampled once and committed like the province outlines.
Elevation comes from Open-Meteo's elevation API, which meant **no DEM download and no
raster library**: this machine has no numpy, rasterio, GDAL or even pip, and a 30 m DEM
for Thailand would have been about 2.5 GB.

Four gauges ride in each request (25 points each, 100 per call). The service rate-limits,
so it is topped up deliberately rather than during a build:

```bash
cd workers && python3 -m seraphim.cli terrain --budget 60
```

Worst-risk gauges are sampled first, so a partial dataset is still useful. Currently
**220 of 1,121** Thai gauges have profiles, covering the highest-risk ones.

A build that sometimes makes a hundred extra upstream calls is a build that sometimes
fails for no good reason, which is why this is not automatic.
