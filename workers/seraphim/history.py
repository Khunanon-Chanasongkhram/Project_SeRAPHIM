"""Station history, reconstructed from the published archive.

Rate of rise is the input that turns a level into a prediction, and it needs real
timestamped history. The source's own `waterlevel_msl_previous` field looked like a
shortcut, but measuring it against our archive on 2026-09-15 showed reporting intervals
scattered across 10, 20, 30 and 60 minutes with no documented rule — so a rate derived
from it would be wrong by up to 6x on an unknown subset of stations. We use our own
archive instead, where every reading carries its own timestamp.

The archive is time-partitioned (YYYY/MM/DD/HHMM.json.gz), so only the files inside the
requested window are opened rather than the whole history.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Regression window. Long enough to smooth telemetry noise, short enough that a river
#: which started rising an hour ago still shows it.
DEFAULT_WINDOW_HOURS = 6.0

#: Below this, a "trend" is indistinguishable from sensor jitter.
MIN_POINTS = 3
MIN_SPAN_HOURS = 0.75


@dataclass(frozen=True, slots=True)
class Trend:
    """A fitted rate of change, with an honest account of how much to trust it."""

    rate_m_per_hr: float | None
    r2: float | None
    points: int
    span_hours: float

    @property
    def confidence(self) -> str:
        """good | fair | poor | none — drives whether a time-to-bank is published at all."""
        if self.rate_m_per_hr is None or self.points < MIN_POINTS:
            return "none"
        if self.points >= 5 and self.span_hours >= 2.0 and (self.r2 or 0) >= 0.7:
            return "good"
        if self.points >= 4 and (self.r2 or 0) >= 0.4:
            return "fair"
        return "poor"

    @property
    def rising(self) -> bool:
        return self.rate_m_per_hr is not None and self.rate_m_per_hr > 0


def _files_in_window(archive_root: Path, since: datetime, until: datetime) -> list[Path]:
    """Select archive files by their path, so old data is never opened."""
    out: list[Path] = []
    day = since.date()
    while day <= until.date():
        d = archive_root / f"{day:%Y/%m/%d}"
        if d.is_dir():
            out.extend(sorted(d.glob("*.json.gz")))
        day += timedelta(days=1)
    return out


def load_history(
    archive_root: Path, now: datetime, window_hours: float = DEFAULT_WINDOW_HOURS
) -> dict[str, list[tuple[datetime, float]]]:
    """Per-station [(observed_at, level_msl)], de-duplicated and time-sorted.

    Snapshots are taken more often than stations report, so the same reading appears in
    several files. Keying on observed_at collapses those duplicates — without which a
    station would look like it had many readings at one level and the regression would
    report a confident rate of zero.
    """
    since = now - timedelta(hours=window_hours)
    seen: dict[str, dict[datetime, float]] = {}

    for path in _files_in_window(archive_root, since, now):
        try:
            payload = json.loads(gzip.open(path, "rb").read())
        except Exception:  # noqa: BLE001 — a corrupt archive file must not stop the build
            continue
        for feature in payload.get("features", []):
            p = feature.get("properties") or {}
            sid, level, observed = p.get("id"), p.get("level_msl"), p.get("observed_at")
            if not sid or level is None or not observed:
                continue
            try:
                ts = datetime.fromisoformat(observed)
            except ValueError:
                continue
            if ts.tzinfo is None or ts < since or ts > now + timedelta(minutes=5):
                continue
            seen.setdefault(sid, {})[ts] = float(level)

    return {sid: sorted(v.items()) for sid, v in seen.items()}


def fit_trend(points: list[tuple[datetime, float]]) -> Trend:
    """Least-squares rate of change in metres per hour.

    Regression rather than first-vs-last because station intervals are irregular
    (10-60 min) and telemetry is noisy: two-point differencing would turn a single
    spurious reading into a dramatic rate, which at these stakes means a false alarm.
    """
    if len(points) < 2:
        return Trend(None, None, len(points), 0.0)

    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 3600.0 for t, _ in points]
    ys = [v for _, v in points]
    span = xs[-1] - xs[0]
    n = len(xs)

    if span < MIN_SPAN_HOURS or n < MIN_POINTS:
        return Trend(None, None, n, round(span, 2))

    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return Trend(None, None, n, round(span, 2))
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx

    syy = sum((y - my) ** 2 for y in ys)
    # A perfectly flat river is a perfect fit, not an undefined one.
    r2 = 1.0 if syy <= 1e-12 else max(0.0, min(1.0, (sxy**2) / (sxx * syy)))

    return Trend(round(slope, 4), round(r2, 3), n, round(span, 2))


def merge_current(
    history: dict[str, list[tuple[datetime, float]]], states
) -> dict[str, list[tuple[datetime, float]]]:
    """Fold the reading we just fetched into the archived series.

    The archive is written after the snapshot is built, so without this the trend would
    always lag one cycle behind — and at the moment a river starts rising, one cycle is
    exactly the one that matters.
    """
    merged = {sid: dict(pts) for sid, pts in history.items()}
    for state in states:
        level = state.observation.level_msl
        if level is None:
            continue
        merged.setdefault(state.station.id, {})[state.observation.observed_at] = float(level)
    return {sid: sorted(v.items()) for sid, v in merged.items()}


def trends(
    archive_root: Path, now: datetime, window_hours: float = DEFAULT_WINDOW_HOURS
) -> dict[str, Trend]:
    return {
        sid: fit_trend(pts)
        for sid, pts in load_history(archive_root, now, window_hours).items()
    }
