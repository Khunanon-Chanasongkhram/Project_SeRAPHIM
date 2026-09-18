"""Entry point. `python -m seraphim.cli build` is what the cron job runs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from seraphim import cache
from seraphim import floodhist

#: Forecast sampling grid, in degrees. About 22 km, close to GloFAS's own resolution.
FORECAST_GRID_DEG = 0.2
from seraphim.adapters import forecast_registry, registry
from seraphim.adapters.marine import TideAdapter, summarise
from seraphim.models import Forecast, SourceHealth, StationState
from seraphim.history import fit_trend, load_history, merge_current
from seraphim.adapters.hazards import (
    fetch_earthquakes, fetch_events, fetch_fires, fetch_past_floods)
from seraphim.adapters.thaidam import fetch_dams
from seraphim.adapters.cameras import fetch_cameras
from seraphim.adapters.googlefloods import fetch_google_floods
from seraphim.adapters.spots import all_spots, fetch_weather
from seraphim.fishing import build_fishing
from seraphim.publish import (
    build_areas,
    prune_archive,
    build_fishing_doc,
    build_provinces,
    split_by_country,
    build_geojson,
    build_meta,
    build_tide,
    write_archive,
    write_snapshot,
)
from seraphim.risk import assess, rollup
from seraphim import terrain as terrain_mod
from seraphim.validate import backtest, format_report


def _grid_cells(stations) -> tuple[dict[tuple[int, int], list[str]], list[tuple[str, float, float]]]:
    """Collapse stations onto the shared forecast grid.

    Returns the cell -> station ids map and the cell centroids as fetchable points.
    Shared by the forecast fetch and the flood climatology so the two can never end up
    comparing a forecast from one cell against a history from another, which would
    produce a percentile that looks authoritative and means nothing.
    """
    cells: dict[tuple[int, int], list[str]] = {}
    for st in stations:
        key = (round(st.lat / FORECAST_GRID_DEG), round(st.lon / FORECAST_GRID_DEG))
        cells.setdefault(key, []).append(st.id)
    points = [(f"cell:{a}:{b}", a * FORECAST_GRID_DEG, b * FORECAST_GRID_DEG)
              for (a, b) in cells]
    return cells, points


def _flood_history(stations, data_root: Path, forecast_fields: dict[str, dict]) -> dict:
    """Join the cached flood climatology onto stations, and read today against it.

    Purely local: this never fetches. The climatology is filled in by the separate
    `floodhist` command, because it costs a rate-limited multi-megabyte request per 50
    cells and has no business inside a 15-minute build. Cells not yet fetched simply
    carry no flood history, and the UI says so rather than guessing.
    """
    hit = cache.load(floodhist.cache_root(data_root),
                     floodhist.CACHE_KEY, floodhist.CACHE_HOURS)
    clim = (hit or {}).get("data") or {}
    if not clim:
        return {"cells": 0, "stations": 0}

    cells, _ = _grid_cells(stations)
    joined = 0
    prone_counts: dict[int, int] = {}
    predicted = 0
    skipped_old = 0
    for (a, b), ids in cells.items():
        stats = clim.get(f"cell:{a}:{b}")
        if not stats or stats.get("no_river"):
            continue
        # A summary at an older version is skipped whole, not read in part. Most of its
        # fields are still valid, but `prone_level` refuses it, and publishing a popup
        # full of history beside a panel reporting no history measured here is a worse
        # state than reporting none consistently until the top-up re-fetches the cell.
        if stats.get("v") != floodhist.STATS_SCHEMA:
            skipped_old += 1
            continue
        prone = floodhist.prone_level(stats)
        for sid in ids:
            fields = forecast_fields.setdefault(sid, {})
            fields["flood_years"] = stats.get("years")
            fields["flood_worst_cms"] = stats.get("max_cms")
            fields["flood_worst_on"] = stats.get("max_on")
            fields["flood_high_days_per_year"] = stats.get("high_days_per_year")
            fields["flood_growth_ratio"] = stats.get("growth_ratio")
            fields["flood_last_episode_on"] = stats.get("last_episode_on")
            fields["flood_return_2y_cms"] = stats.get("return_2y_cms")
            fields["flood_return_5y_cms"] = stats.get("return_5y_cms")
            fields["flood_season_months"] = _flood_season(stats.get("by_month_cms"))
            if prone is not None:
                fields["flood_prone"] = prone
                prone_counts[prone] = prone_counts.get(prone, 0) + 1

            # Where today's modelled flow sits in this cell's own record.
            now = fields.get("discharge_now_cms")
            pct = floodhist.percentile_of(stats, now)
            if pct is not None:
                fields["flood_percentile"] = pct

            # The predicted half: does the outlook reach a level this river only
            # reaches every couple of years?
            ex = floodhist.forecast_exceedance(stats, fields.get("discharge_outlook_cms"))
            if ex:
                # The SOONEST meaningful crossing, not the most severe one. Publishing
                # only the 5-year day threw away the fact that the 2-year level might
                # be reached a fortnight earlier, which is the part anyone can act on,
                # and it could push a gauge out of the map's 14-day highlight while
                # still being days away from unusual water.
                fields["flood_outlook_day"] = ex["day_2y"]
                fields["flood_outlook_5y_day"] = ex.get("day_5y")
                # The strongest level the outlook reaches at all, for the label.
                fields["flood_outlook_period_y"] = 5 if ex.get("day_5y") is not None else 2
                fields["flood_outlook_peak_vs_2y"] = ex.get("peak_vs_2y")
                predicted += 1
            joined += 1
    return {"cells": sum(1 for v in clim.values()
                        if not v.get("no_river") and v.get("v") == floodhist.STATS_SCHEMA),
            "stations": joined, "prone": prone_counts, "predicted": predicted,
            "skipped_old": skipped_old}


def _flood_season(by_month: list | None) -> str | None:
    """The months this river usually runs highest, as "Jul-Oct", or None.

    Months carrying at least 70% of the peak month's mean flow. Answers "when does
    this place normally flood", which is the question behind most of the history."""
    if not by_month or len(by_month) != 12:
        return None
    vals = [v for v in by_month if v is not None]
    if not vals or max(vals) <= 0:
        return None
    cut = max(vals) * 0.7
    hot = [i for i, v in enumerate(by_month) if v is not None and v >= cut]
    if not hot or len(hot) == 12:
        return None
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    # Wrap a season that straddles the new year (Nov-Feb) instead of printing "Jan-Dec".
    runs, run = [], [hot[0]]
    for m in hot[1:]:
        if m == run[-1] + 1:
            run.append(m)
        else:
            runs.append(run)
            run = [m]
    runs.append(run)
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == 11:
        runs = [runs[-1] + runs[0]] + runs[1:-1]
    best = max(runs, key=len)
    return names[best[0]] if len(best) == 1 else f"{names[best[0]]}-{names[best[-1]]}"


def _forecasts(
    stations,
    cache_root: Path,
    generated_at: datetime,
    health: list[SourceHealth],
    scale: float,
) -> tuple[dict[str, dict], datetime]:
    """Per-station forward-looking fields, from cache where still fresh."""
    # Fetch on a grid, not per gauge. With two countries the naive approach needs
    # ~22,000 Open-Meteo location-calls a day against a 10,000 limit. Rainfall and a
    # 5 km discharge model do not vary meaningfully inside a 22 km cell, so gauges
    # sharing a cell share a forecast. That is ~1,000 calls instead of ~4,400, and the
    # accuracy lost is smaller than the model's own resolution.
    # A source whose own network publishes a per-gauge forecast opts out of this grid.
    # Without that, 11,467 US gauges took it from 1,029 cells to 7,284, about 36,000
    # Open-Meteo location-calls a day against a 10,000 allowance, to buy a rainfall
    # proxy for gauges NOAA already runs a hydrological model on.
    gridded = [st for st in stations
               if getattr(registry.get(st.source), "shared_forecast_grid", True)]
    cells, points = _grid_cells(gridded)
    opted_out = len(stations) - len(gridded)
    print(f"[forecast] {len(gridded)} gauges collapse to {len(points)} grid cells "
          f"at {FORECAST_GRID_DEG} deg"
          + (f"; {opted_out} use their own national forecast" if opted_out else ""))

    merged: dict[str, dict] = {}
    oldest = generated_at

    for name, adapter in forecast_registry.items():
        # Grid adapters are fed cell centroids and their answer is shared by every gauge
        # in the cell. Per-gauge adapters are fed the real stations of their own country.
        if getattr(adapter, "grid", True):
            asked = points
        else:
            asked = [(st.id, st.lat, st.lon) for st in stations
                     if st.admin and st.admin.country == adapter.country]
            if not asked:
                continue

        max_age = adapter.refresh_hours * scale
        hit = cache.load(cache_root, name, max_age)
        if hit:
            data = hit["data"]
            fetched = datetime.fromisoformat(hit["fetched_at"])
            age = (generated_at - fetched).total_seconds() / 3600
            print(f"[{name}] cache hit ({age:.1f} h old, limit {max_age:g} h)")
        else:
            print(f"[{name}] refreshing {len(asked)} points (limit {max_age:g} h)…", flush=True)
            data, h = adapter.fetch_for(asked)
            health.append(h)
            for w in h.warnings:
                print(f"[{name}] warning: {w}", file=sys.stderr)
            if not h.ok:
                print(f"[{name}] FAILED: {h.error}", file=sys.stderr)
                continue
            fetched = cache.save(cache_root, name, data)
            print(f"[{name}] {len(data)} points enriched", flush=True)

        oldest = min(oldest, fetched)
        if not getattr(adapter, "grid", True):
            # Already keyed by station id: nothing to spread.
            for sid, fields in data.items():
                merged.setdefault(sid, {}).update(fields)
            continue
        # Spread each cell's forecast back over the gauges that share it.
        for cell_id, fields in data.items():
            try:
                _, a, b = cell_id.split(":")
                members = cells.get((int(a), int(b)), [])
            except ValueError:
                members = []
            for sid in members:
                merged.setdefault(sid, {}).update(fields)

    return merged, oldest


def _tide(
    cache_root: Path, generated_at: datetime, health: list[SourceHealth], scale: float
) -> list[dict]:
    adapter = TideAdapter()
    max_age = adapter.refresh_hours * scale
    hit = cache.load(cache_root, adapter.id, max_age)
    if hit:
        age = (generated_at - datetime.fromisoformat(hit["fetched_at"])).total_seconds() / 3600
        print(f"[{adapter.id}] cache hit ({age:.1f} h old, limit {max_age:g} h)")
        return hit["data"]

    print(f"[{adapter.id}] refreshing coastal predictions…", flush=True)
    series, th = adapter.fetch()
    health.append(th)
    for w in th.warnings:
        print(f"[{adapter.id}] warning: {w}", file=sys.stderr)
    if not series:
        print(f"[{adapter.id}] FAILED: {th.error}", file=sys.stderr)
        return []
    tide = [summarise(s, generated_at) for s in series]
    cache.save(cache_root, adapter.id, tide)
    print(f"[{adapter.id}] {len(tide)} points", flush=True)
    return tide


def build(
    out_root: Path,
    sources: list[str] | None = None,
    archive: bool = True,
    archive_keep_hours: float = 0.0,
    forecasts: bool = True,
    refresh_scale: float = 1.0,
) -> int:
    generated_at = datetime.now(timezone.utc)
    selected = {k: v for k, v in registry.items() if not sources or k in sources}
    if not selected:
        print(f"no adapters matched {sources!r}; available: {sorted(registry)}", file=sys.stderr)
        return 2

    all_stations = []
    observations_by_id = {}
    health: list[SourceHealth] = []

    for name, adapter in selected.items():
        print(f"[{name}] fetching…", flush=True)
        # Station metadata only. Adapters that use this cache their gauge *list*, never
        # a reading: the Dutch catalogue is 7.3 MB and changes far more slowly than the
        # 15-minute build cadence.
        adapter.cache_root = out_root / "cache"
        stations, observations, h = adapter.fetch()
        health.append(h)
        if not h.ok:
            # Degrade, never abort: a partial map during a flood beats no map.
            print(f"[{name}] FAILED: {h.error}", file=sys.stderr)
            continue
        all_stations.extend(stations)
        observations_by_id.update({o.station_id: o for o in observations})
        print(f"[{name}] {h.stations} stations, {h.observations} observations", flush=True)
        for w in h.warnings:
            print(f"[{name}] warning: {w}", file=sys.stderr)

    # --- forecasts: cached, because they do not change on the level cadence ---
    forecast_fields: dict[str, dict] = {}
    tide_points: list[dict] = []
    forecast_fetched_at = generated_at
    if forecasts and all_stations:
        cache_root = out_root / "cache"
        forecast_fields, forecast_fetched_at = _forecasts(
            all_stations, cache_root, generated_at, health, refresh_scale
        )
        # Flood history is a local join over an already-cached climatology, so it costs
        # no upstream call and adds no build time. `floodhist` fills the cache.
        fh = _flood_history(all_stations, out_root, forecast_fields)
        if fh["stations"]:
            prone = fh.get("prone") or {}
            print(f"[floodhist] {fh['cells']} cells of history joined to "
                  f"{fh['stations']} gauges | flood-prone 3={prone.get(3,0)} "
                  f"2={prone.get(2,0)} 1={prone.get(1,0)} 0={prone.get(0,0)} | "
                  f"{fh['predicted']} with a high-flow outlook")
        elif fh.get("skipped_old"):
            print(f"[floodhist] {fh['skipped_old']} cells are cached at an older summary "
                  f"version and were skipped; run `python -m seraphim.cli floodhist "
                  f"--budget 300` to re-fetch them")
        else:
            print("[floodhist] no climatology cached yet; "
                  "run `python -m seraphim.cli floodhist --budget 300`")
        tide_points = _tide(cache_root, generated_at, health, refresh_scale)

    states: list[StationState] = []
    for st in all_stations:
        ob = observations_by_id.get(st.id)
        if ob is None:
            continue
        fields = forecast_fields.get(st.id)
        fc = Forecast(fetched_at=forecast_fetched_at, **fields) if fields else None
        states.append(
            StationState(station=st, observation=ob, generated_at=generated_at, forecast=fc)
        )

    # --- risk: needs history, which lives in the archive we have already written ---
    history = load_history(out_root / "archive", generated_at)
    history = merge_current(history, states)
    station_trends = {sid: fit_trend(pts) for sid, pts in history.items()}

    risks = {
        s.station.id: assess(s, station_trends.get(s.station.id), tide_points, generated_at)
        for s in states
    }
    areas = rollup(states, risks)
    conf = {}
    for r in risks.values():
        conf[r.trend_confidence] = conf.get(r.trend_confidence, 0) + 1
    print(
        f"[risk] {len(risks)} assessed | trend confidence: "
        + ", ".join(f"{k}={v}" for k, v in sorted(conf.items()))
        + f" | {sum(1 for r in risks.values() if r.time_to_bank_hr is not None)} with time-to-bank"
    )

    # --- calm mode: the same data, answering the question people have on quiet days ---
    fishing_doc = None
    if forecasts and tide_points:
        spots = all_spots()
        hit = cache.load(out_root / "cache", "spot_weather", 6.0 * refresh_scale)
        if hit:
            spot_weather = hit["data"]
            print(f"[fishing] spot weather cache hit ({len(spot_weather)} spots)")
        else:
            spot_weather, wh = fetch_weather(spots)
            health.append(wh)
            if wh.ok:
                cache.save(out_root / "cache", "spot_weather", spot_weather)
            else:
                print(f"[fishing] weather FAILED: {wh.error}", file=sys.stderr)
        tide_by_id = {t["id"]: t for t in tide_points}
        plans = build_fishing(spots, tide_by_id, spot_weather, generated_at.date())
        fishing_doc = build_fishing_doc(plans, generated_at)
        peak = max((d["peak_score"] for p in plans for d in p["days"]), default=0)
        print(f"[fishing] {len(plans)} spots x {len(plans[0]['days'])} days | best score {peak}")

    # --- global hazard layers, cached like the forecasts ---
    extra: dict = {}
    if forecasts:
        cache_root = out_root / "cache"
        hit = cache.load(cache_root, "quakes", 0.5 * refresh_scale)
        if hit:
            extra["quakes.geojson"] = hit["data"]
            print("[quakes] cache hit")
        else:
            quakes, qh = fetch_earthquakes()
            health.append(qh)
            if quakes:
                extra["quakes.geojson"] = quakes
                cache.save(cache_root, "quakes", quakes)
                print(f"[quakes] {qh.stations} in the past 24 h")
            else:
                print(f"[quakes] FAILED: {qh.error}", file=sys.stderr)

        # Reservoirs. A daily product, so a 3 h cache costs nothing and spares the
        # source a 1 MB fetch every quarter hour.
        hit = cache.load(cache_root, "dams", 3.0 * refresh_scale)
        if hit:
            extra["dams.geojson"] = hit["data"]
            print("[dams] cache hit")
        else:
            dams, dh = fetch_dams()
            health.append(dh)
            for w in dh.warnings:
                print(f"[dams] warning: {w}", file=sys.stderr)
            if dams:
                extra["dams.geojson"] = dams
                cache.save(cache_root, "dams", dams)
                print(f"[dams] {dh.stations} reservoirs")
            else:
                print(f"[dams] FAILED: {dh.error}", file=sys.stderr)

        # Past floods. A 21-year archive changes only when a new flood ends, so it is
        # cached for a week; the per-year fetch is 22 requests and has no business
        # running on the 15-minute cadence.
        hit = cache.load(cache_root, "floods_past", 168.0 * refresh_scale)
        if hit:
            extra["floods-past.geojson"] = hit["data"]
            print("[floods-past] cache hit")
        else:
            past, ph = fetch_past_floods()
            health.append(ph)
            for w in ph.warnings:
                print(f"[floods-past] warning: {w}", file=sys.stderr)
            if past:
                extra["floods-past.geojson"] = past
                cache.save(cache_root, "floods_past", past)
                print(f"[floods-past] {ph.stations} orange/red floods, "
                      f"{past['years'][0]}-{past['years'][1]}")
            else:
                print(f"[floods-past] FAILED: {ph.error}", file=sys.stderr)

        hit = cache.load(cache_root, "events", 3.0 * refresh_scale)
        if hit:
            extra["events.geojson"] = hit["data"]
            print("[events] cache hit")
        else:
            events, eh = fetch_events()
            health.append(eh)
            if events:
                extra["events.geojson"] = events
                cache.save(cache_root, "events", events)
                import collections as _c
                by = _c.Counter(f["properties"]["kind"] for f in events["features"])
                print(f"[events] {eh.stations} worldwide: {dict(by)}")
            else:
                print(f"[events] FAILED: {eh.error}", file=sys.stderr)

        # Google Flood Hub. Status is refreshed "several times a day" upstream, so a
        # 3 h cache costs nothing. Absent entirely without a key, and that is not a
        # failure: access is gated behind a pilot waitlist.
        hit = cache.load(cache_root, "google_floods", 3.0 * refresh_scale)
        if hit:
            extra["floods-google.geojson"] = hit["data"]
            print("[google-floods] cache hit")
        else:
            gdoc, gh = fetch_google_floods()
            health.append(gh)
            for w in gh.warnings:
                print(f"[google-floods] warning: {w}", file=sys.stderr)
            if gdoc:
                extra["floods-google.geojson"] = gdoc
                cache.save(cache_root, "google_floods", gdoc)
                print(f"[google-floods] {gh.stations} flood-status points")
            else:
                print(f"[google-floods] skipped: {gh.error}")

        # Street cameras, Yala and Hat Yai. Cached for an hour, which is a politeness
        # budget rather than a freshness one: probing Yala mints a real Kinesis session
        # on someone else's AWS account, and that feed carries no licence and names no
        # owner to have asked. An hour is ~120 mints a day instead of ~480 on a
        # 15-minute build. It costs nothing in freshness that matters: the browser
        # fetches a live image or mints its own session on click, so a camera that
        # comes back between builds still works. What the hour *does* stale is the
        # published age of a Hat Yai still, which is why `observed_at` is published as
        # a timestamp rather than as a precomputed "n minutes ago".
        hit = cache.load(cache_root, "cctv", 1.0 * refresh_scale)
        if hit:
            extra["cameras.geojson"] = hit["data"]
            print("[cameras] cache hit")
        else:
            cams, chs = fetch_cameras(states)
            health.extend(chs)
            for ch in chs:
                for w in ch.warnings:
                    print(f"[{ch.source}] warning: {w}", file=sys.stderr)
                if not ch.ok:
                    print(f"[{ch.source}] skipped: {ch.error}")
            if cams:
                extra["cameras.geojson"] = cams
                cache.save(cache_root, "cctv", cams)
                by_op = {}
                for f in cams["features"]:
                    by_op[f["properties"]["operator"]] = \
                        by_op.get(f["properties"]["operator"], 0) + 1
                paired = sum(1 for f in cams["features"]
                             if f["properties"]["gauge_id"])
                print(f"[cameras] {len(cams['features'])} cameras "
                      f"({', '.join(f'{k}={v}' for k, v in sorted(by_op.items()))}), "
                      f"{paired} paired to a gauge")

        hit = cache.load(cache_root, "fires", 3.0 * refresh_scale)
        if hit:
            extra["fires.geojson"] = hit["data"]
            print("[fires] cache hit")
        else:
            fires, fh = fetch_fires()
            health.append(fh)
            if fires:
                extra["fires.geojson"] = fires
                cache.save(cache_root, "fires", fires)
                print(f"[fires] {fh.stations} detections")
            else:
                # Not an error: the layer is optional and needs a free NASA key.
                print(f"[fires] skipped: {fh.error}")

    # --- does the prediction actually predict? measured, then published ---
    if archive:
        import time as _time
        t0 = _time.perf_counter()
        validation = backtest(out_root / "archive")
        validation["generated_at"] = generated_at.isoformat()
        extra["validation.json"] = validation
        print(f"[validation] {format_report(validation)}")
        print(f"[validation] took {_time.perf_counter() - t0:.1f}s")

    # --- terrain: where water would go where a channel is spilling ---
    # Profiles are static and shipped with the code, so this costs no upstream calls
    # at build time. Topping them up is a separate, deliberate command.
    terrain_layer = None
    profiles = terrain_mod.load_profiles(terrain_mod.PROFILES_FILE)
    if profiles:
        terrain_layer = terrain_mod.build_layer(states, profiles, generated_at)
        if terrain_layer:
            extra["terrain.geojson"] = terrain_layer
            print(f"[terrain] {len(profiles)} profiles, "
                  f"{terrain_layer['gauges_with_terrain']} gauges covered, "
                  f"{len(terrain_layer['features'])} low-ground points where a channel is spilling")

    # Per-country files so a visitor downloads only the country they are looking at.
    country_files, country_index = split_by_country(states, risks, areas, station_trends)

    # A transient upstream failure must not delete a country from the site. The UK API
    # returned a 500 mid-development and the build cheerfully published a Thailand-only
    # site; for the 15 minutes until the next run, British readers would have had
    # nothing at all. Last known good is kept and republished instead, and since every
    # reading already carries its own age, going stale is visible rather than silent.
    cache_root = out_root / "cache"
    live = {c["code"] for c in country_index["countries"]}
    for h in health:
        adapter = registry.get(h.source)
        if adapter is None or h.ok or adapter.country in live or adapter.country == "*":
            continue
        stale = cache.load(cache_root, f"country_{adapter.country.lower()}", 24.0)
        if not stale:
            continue
        kept = stale["data"]
        country_files.update(kept["files"])
        entry = dict(kept["entry"])
        entry["stale"] = True
        entry["stale_since"] = stale["fetched_at"]
        country_index["countries"].append(entry)
        print(f"[{h.source}] source is down; republishing the last good "
              f"{adapter.country} snapshot from {stale['fetched_at'][:16]}")

    for entry in country_index["countries"]:
        if entry.get("stale"):
            continue
        cc = entry["code"].lower()
        keep = {k: v for k, v in country_files.items() if k.endswith(f"-{cc}.geojson")
                or k.endswith(f"-{cc}.json")}
        cache.save(cache_root, f"country_{cc}", {"files": keep, "entry": entry})
    country_index["generated_at"] = generated_at.isoformat()
    extra.update(country_files)
    extra["index.json"] = country_index
    print("[countries] " + ", ".join(
        f"{c['code']} {c['stations']}" for c in country_index["countries"]))

    geojson = build_geojson(states, risks, station_trends)
    meta = build_meta(states, health, generated_at, tide_points=len(tide_points),
                      risks=risks, validation=extra.get("validation.json"))
    tide = build_tide(tide_points, generated_at) if tide_points else None
    areas_doc = build_areas(areas, generated_at) if len(
        {a.get("country") for a in areas}) <= 1 else None
    th_states = [s for s in states
                 if s.station.admin and s.station.admin.country == "TH"]
    provinces = build_provinces(th_states, areas, generated_at)
    if provinces:
        j = provinces["join"]
        print(f"[provinces] {j['mapped']}/{j['polygons']} shapes joined"
              + (f", {len(j['weak_joins'])} weak" if j["weak_joins"] else "")
              + f", {j['gauges_outside_any_province']} gauges outside any shape")

    # geojson=None: the combined file feeds the archive below, not the website.
    written = write_snapshot(out_root / "out", None, meta, tide, areas_doc,
                             fishing_doc, provinces, extra)
    if archive and states:
        written.append(write_archive(out_root / "archive", geojson, generated_at))
    if archive_keep_hours > 0:
        dropped = prune_archive(out_root / "archive", generated_at, archive_keep_hours)
        if dropped:
            print(f"[archive] pruned {dropped} files older than {archive_keep_hours:g} h")
    for p in written:
        print(f"wrote {p} ({p.stat().st_size:,} bytes)")

    c = meta["counts"]
    rl = meta["risk"]["by_level"]
    print(
        f"\nrisk levels: 5={rl['5']} 4={rl['4']} 3={rl['3']} 2={rl['2']} 1={rl['1']}"
        f" | soonest to bank: {meta['risk']['soonest_to_bank_hr']} h"
        f" | districts: {len(areas)}"
    )
    print(
        f"{c['stations']} stations | {c['with_bank_level']} with bank level | "
        f"{c['at_or_over_bank']} at/over bank | {c['stale']} stale | "
        f"{c['with_forecast']} with forecast | {c['tide_points']} tide points | "
        f"median age {meta['data_age_minutes']['median']} min"
    )

    if not any(h.ok for h in health):
        print("all sources failed", file=sys.stderr)
        return 1
    return 0


def _floodhist(budget: int, out_root: Path, country: str | None) -> int:
    """Top up the flood climatology, a grid cell at a time.

    Separate from the build for the same reason `terrain` is: it costs a rate-limited
    multi-megabyte request per 50 cells and takes minutes, and a build that sometimes
    does that is a build that sometimes times out during a flood.

    Incremental by design. Each run fetches up to `budget` cells that have no history
    yet or whose history is older than REFRESH_DAYS, merges them into the cache, and
    stops. An interrupted run costs progress, never data.
    """
    cache_root = out_root / "cache"
    clim_root = floodhist.cache_root(out_root)
    hit = cache.load(clim_root, floodhist.CACHE_KEY, floodhist.CACHE_HOURS)
    clim: dict[str, dict] = dict((hit or {}).get("data") or {})
    before = len(clim)

    collected = []
    for name, adapter in registry.items():
        if country and adapter.country.lower() != country.lower():
            continue
        adapter.cache_root = cache_root
        stations, _obs, health = adapter.fetch()
        if not health.ok:
            print(f"[{name}] FAILED: {health.error}", file=sys.stderr)
            continue
        collected += stations
    if not collected:
        print("no stations available", file=sys.stderr)
        return 1

    cells, points = _grid_cells(collected)
    # Busiest cells first: a cell holding forty gauges answers the question for forty
    # places, and the budget runs out long before the world does.
    weight = {f"cell:{a}:{b}": len(ids) for (a, b), ids in cells.items()}

    def needs(cid: str) -> bool:
        have = clim.get(cid)
        if have is None:
            return True
        # Summaries computed by an older version are re-fetched: a statistic that was
        # never computed cannot be recovered from the summary it is missing from, and
        # the alternative is a map showing two incompatible definitions of flood-prone
        # side by side with nothing to tell them apart.
        return have.get("v") != floodhist.STATS_SCHEMA and not have.get("no_river")

    todo = [p for p in points if needs(p[0])]
    todo.sort(key=lambda p: -weight.get(p[0], 0))
    if not todo:
        print(f"[floodhist] all {len(points)} cells covered at v{floodhist.STATS_SCHEMA}"
              f"{f' in {country.upper()}' if country else ''}; nothing due")
        return 0

    stale = sum(1 for p in todo if p[0] in clim)
    print(f"[floodhist] {len(clim)} cells cached, {len(todo)} to fetch "
          f"({stale} at an older summary version)"
          f"{f' in {country.upper()}' if country else ''}; "
          f"fetching up to {budget} at {floodhist.BATCH}/request, "
          f"~{floodhist.PACE_SECONDS}s apart", flush=True)

    health = SourceHealth(source=floodhist.CACHE_KEY, ok=False)
    fetched = floodhist.fetch_climatology(todo, health, budget=budget)
    for w in health.warnings:
        print(f"[floodhist] warning: {w}", file=sys.stderr)
    if not fetched:
        print(f"[floodhist] FAILED: {health.error}", file=sys.stderr)
        return 1

    clim.update(fetched)
    cache.save(clim_root, floodhist.CACHE_KEY, clim)
    rivers = sum(1 for v in clim.values() if not v.get("no_river"))
    print(f"[floodhist] {before} -> {len(clim)} cells ({rivers} with a river), "
          f"{len(points) - len(clim)} still missing")
    return 0


def _terrain(budget: int) -> int:
    """Top up terrain profiles, worst-risk gauges first.

    Deliberately a separate command rather than part of the build. Terrain does not
    change, the elevation service rate-limits, and a build that sometimes makes a
    hundred extra upstream calls is a build that sometimes fails for no good reason.
    """
    profiles = terrain_mod.load_profiles(terrain_mod.PROFILES_FILE)
    before = len(profiles)
    collected = []
    for name, adapter in registry.items():
        stations, observations, health = adapter.fetch()
        if not health.ok:
            print(f"[{name}] FAILED: {health.error}", file=sys.stderr)
            continue
        levels = {o.station_id: o.level_msl for o in observations}

        def urgency(st):
            level, bank = levels.get(st.id), st.bank_msl
            if level is None or bank is None:
                return 1e9
            return bank - level          # smallest freeboard first, negative is worst

        stations.sort(key=urgency)
        collected += stations
    if not collected:
        print("no stations available", file=sys.stderr)
        return 1

    profiles = terrain_mod.build_profiles(collected, profiles, budget=budget)
    terrain_mod.save_profiles(terrain_mod.PROFILES_FILE, profiles)
    print(f"[terrain] {before} -> {len(profiles)} profiles, "
          f"{terrain_mod.PROFILES_FILE.stat().st_size:,} B")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seraphim", description="SeRAPHIM snapshot builder")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="fetch sources and write a snapshot")
    b.add_argument("--out", type=Path, default=Path("../data"), help="output root (default: ../data)")
    b.add_argument("--source", action="append", dest="sources", help="limit to adapter id (repeatable)")
    b.add_argument("--no-archive", action="store_true", help="skip the time-partitioned archive")
    b.add_argument(
        "--archive-keep-hours", type=float, default=0.0, metavar="HOURS",
        help="delete archive snapshots older than this (0 = keep everything)")
    b.add_argument("--no-forecast", action="store_true", help="water levels only, no upstream forecast calls")
    b.add_argument(
        "--refresh-scale",
        type=float,
        default=1.0,
        metavar="X",
        help="multiply every adapter's refresh interval (e.g. 0 forces a refetch)",
    )

    sub.add_parser("sources", help="list registered adapters")

    tp = sub.add_parser(
        "terrain",
        help="sample ground around gauges (run occasionally; terrain does not change)")
    tp.add_argument("--budget", type=int, default=60,
                    help="gauges to sample this run (default 60)")
    tp.add_argument("--out", type=Path, default=Path("../data"))

    gp = sub.add_parser(
        "googlefloods",
        help="check the Google Flood Hub schema against a live response (needs a key)")
    gp.add_argument("--probe", action="store_true",
                    help="talk to Google: dump one live row and check every field the "
                         "adapter reads. Without it, just prints local status.")
    gp.add_argument("--region", default="TH", help="region code to probe (default TH)")

    fh = sub.add_parser(
        "floodhist",
        help="top up the flood climatology (run daily; 12 years of history per cell)")
    fh.add_argument("--budget", type=int, default=300,
                    help="grid cells to fetch this run (default 300, ~6 min)")
    fh.add_argument("--country", help="limit to one adapter country, e.g. TH")
    fh.add_argument("--out", type=Path, default=Path("../data"))

    args = parser.parse_args(argv)
    if args.command == "terrain":
        return _terrain(args.budget)
    if args.command == "floodhist":
        return _floodhist(args.budget, args.out.resolve(), args.country)
    if args.command == "googlefloods":
        from seraphim.adapters import googlefloods as _gf
        if args.probe:
            return _gf.probe(region=args.region)
        # Bare command: say where things stand without spending a request. `--probe` is
        # the one that talks to Google, and it should be a deliberate act rather than
        # something a status check does behind your back.
        key = _gf.api_key()
        print(f"key: {'configured' if key else 'NOT configured'}"
              + ("" if key else " (set GOOGLE_FLOOD_API_KEY; access is waitlisted at "
                               "https://developers.google.com/flood-forecasting)"))
        print(f"regions fetched by the build: {', '.join(_gf.DEFAULT_REGIONS)}")
        print("severity mapping: " + ", ".join(
            f"{k}->{v}" for k, v in _gf.SEVERITY_LEVEL.items())
            + " (UNKNOWN is dropped, never mapped to 'no flooding')")
        print("\nField names come from Google's published reference, NOT from a live "
              "response.\nRun with --probe once a key exists, before trusting the layer.")
        return 0 if key else 2
    if args.command == "sources":
        for name, a in sorted(registry.items()):
            print(f"  level    {name:<18} {a.country:<3} {a.attribution}")
        for name, a in sorted(forecast_registry.items()):
            print(f"  forecast {name:<18} {getattr(a, 'country', '*'):<3} {a.attribution}")
        print(f"  tide     {TideAdapter.id:<18} TH  {TideAdapter.attribution}")
        return 0
    return build(
        args.out.resolve(),
        args.sources,
        archive=not args.no_archive,
        archive_keep_hours=args.archive_keep_hours,
        forecasts=not args.no_forecast,
        refresh_scale=args.refresh_scale,
    )


if __name__ == "__main__":
    raise SystemExit(main())
