"""Sun and moon positions, rise/set and transit.

Needed for the calm-mode fishing planner: fish feed at low light and around lunar
transits, so "when is the moon overhead" is as load-bearing here as tide height.

Positions use standard low-precision series (Meeus): roughly 0.01° for the sun and
0.3° for the moon, which puts rise and set within a couple of minutes, far tighter
than the ±1 hour solunar windows built on top of them.

Rise and set are found **numerically**, by scanning altitude across the day and
bisecting sign changes, rather than from closed-form hour-angle formulae. It is the
same code path for both bodies, it degrades gracefully at high latitude where a body
may never rise or set, and it is much harder to get subtly wrong.

Validated against Open-Meteo's own sunrise/sunset (see tests/test_fishing.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

J2000 = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)

#: Standard altitude of the sun's centre at rise/set: half the disc plus refraction.
SUN_HORIZON_DEG = -0.833
#: The moon's parallax roughly cancels refraction and its semidiameter, leaving a
#: small positive standard altitude.
MOON_HORIZON_DEG = 0.125

_RAD = math.pi / 180.0


def julian_centuries(when: datetime) -> float:
    return (when - J2000).total_seconds() / 86400.0 / 36525.0


def days_since_j2000(when: datetime) -> float:
    return (when - J2000).total_seconds() / 86400.0


def obliquity(when: datetime) -> float:
    """Mean obliquity of the ecliptic, degrees."""
    return 23.439291 - 0.0130042 * julian_centuries(when)


def sun_position(when: datetime) -> tuple[float, float]:
    """Right ascension and declination of the sun, in degrees."""
    d = days_since_j2000(when)
    mean_long = (280.460 + 0.9856474 * d) % 360.0
    anomaly = (357.528 + 0.9856003 * d) % 360.0
    # Equation of centre: the orbit is an ellipse, so the sun runs ahead of and behind
    # its mean position over the year.
    ecl_long = mean_long + 1.915 * math.sin(anomaly * _RAD) + 0.020 * math.sin(2 * anomaly * _RAD)
    return _ecliptic_to_equatorial(ecl_long, 0.0, obliquity(when))


def moon_position(when: datetime) -> tuple[float, float, float]:
    """Right ascension, declination (degrees) and distance (km) of the moon."""
    d = days_since_j2000(when)
    mean_long = 218.316 + 13.176396 * d          # mean longitude
    anomaly = 134.963 + 13.064993 * d            # mean anomaly
    argument = 93.272 + 13.229350 * d            # argument of latitude

    ecl_long = mean_long + 6.289 * math.sin(anomaly * _RAD)
    ecl_lat = 5.128 * math.sin(argument * _RAD)
    distance = 385001.0 - 20905.0 * math.cos(anomaly * _RAD)

    ra, dec = _ecliptic_to_equatorial(ecl_long, ecl_lat, obliquity(when))
    return ra, dec, distance


def _ecliptic_to_equatorial(lon_deg: float, lat_deg: float, eps_deg: float) -> tuple[float, float]:
    lon, lat, eps = lon_deg * _RAD, lat_deg * _RAD, eps_deg * _RAD
    sin_dec = math.sin(lat) * math.cos(eps) + math.cos(lat) * math.sin(eps) * math.sin(lon)
    dec = math.asin(max(-1.0, min(1.0, sin_dec)))
    y = math.sin(lon) * math.cos(eps) - math.tan(lat) * math.sin(eps)
    ra = math.atan2(y, math.cos(lon))
    return (math.degrees(ra) % 360.0), math.degrees(dec)


def gmst_deg(when: datetime) -> float:
    """Greenwich mean sidereal time, degrees."""
    return (280.46061837 + 360.98564736629 * days_since_j2000(when)) % 360.0


def altitude_deg(ra_deg: float, dec_deg: float, lat: float, lon: float, when: datetime) -> float:
    """Altitude of a body above the horizon, degrees."""
    hour_angle = (gmst_deg(when) + lon - ra_deg) * _RAD
    lat_r, dec_r = lat * _RAD, dec_deg * _RAD
    sin_alt = (math.sin(lat_r) * math.sin(dec_r)
               + math.cos(lat_r) * math.cos(dec_r) * math.cos(hour_angle))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def sun_altitude(lat: float, lon: float, when: datetime) -> float:
    ra, dec = sun_position(when)
    return altitude_deg(ra, dec, lat, lon, when)


def moon_altitude(lat: float, lon: float, when: datetime) -> float:
    ra, dec, _ = moon_position(when)
    return altitude_deg(ra, dec, lat, lon, when)


@dataclass(frozen=True, slots=True)
class RiseSet:
    """Events for one body over one local day. Any field may be None.

    None is meaningful, not missing: the moon genuinely fails to rise on about one day
    in thirty, because its day is ~50 minutes longer than ours.
    """

    rise: datetime | None
    set: datetime | None
    transit: datetime | None       # highest point, the solunar "major" period
    antitransit: datetime | None   # lowest point, below the horizon; also a major period
    always_up: bool = False
    always_down: bool = False


def _bisect(alt_fn, horizon: float, lo: datetime, hi: datetime, iterations: int = 22) -> datetime:
    """Narrow a crossing to well under a second."""
    for _ in range(iterations):
        mid = lo + (hi - lo) / 2
        if (alt_fn(lo) - horizon) * (alt_fn(mid) - horizon) <= 0:
            hi = mid
        else:
            lo = mid
    return lo + (hi - lo) / 2


def find_events(alt_fn, start: datetime, hours: float, horizon_deg: float,
                step_minutes: float = 10.0) -> RiseSet:
    """Scan altitude across a window, returning the first rise, set and extremes."""
    steps = int(hours * 60 / step_minutes)
    times = [start + timedelta(minutes=step_minutes * i) for i in range(steps + 1)]
    alts = [alt_fn(t) for t in times]

    rise = set_ = None
    for i in range(len(times) - 1):
        a, b = alts[i] - horizon_deg, alts[i + 1] - horizon_deg
        if a <= 0 < b and rise is None:
            rise = _bisect(alt_fn, horizon_deg, times[i], times[i + 1])
        elif a > 0 >= b and set_ is None:
            set_ = _bisect(alt_fn, horizon_deg, times[i], times[i + 1])

    hi = max(range(len(alts)), key=lambda i: alts[i])
    lo = min(range(len(alts)), key=lambda i: alts[i])
    transit = _refine_extreme(alt_fn, times, hi, step_minutes, maximum=True)
    antitransit = _refine_extreme(alt_fn, times, lo, step_minutes, maximum=False)

    return RiseSet(
        rise=rise, set=set_, transit=transit, antitransit=antitransit,
        always_up=rise is None and set_ is None and min(alts) > horizon_deg,
        always_down=rise is None and set_ is None and max(alts) <= horizon_deg,
    )


def _refine_extreme(alt_fn, times, index, step_minutes, maximum: bool) -> datetime | None:
    """Golden-section-ish refinement of a turning point to about a minute."""
    if index <= 0 or index >= len(times) - 1:
        return times[index] if times else None
    lo, hi = times[index - 1], times[index + 1]
    for _ in range(18):
        a = lo + (hi - lo) / 3
        b = hi - (hi - lo) / 3
        if (alt_fn(a) > alt_fn(b)) == maximum:
            hi = b
        else:
            lo = a
    return lo + (hi - lo) / 2


def sun_events(lat: float, lon: float, day: date, tz_offset_hours: float = 7.0) -> RiseSet:
    """Sun events for one LOCAL day, returned in UTC."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=tz_offset_hours)
    return find_events(lambda t: sun_altitude(lat, lon, t), start, 24.0, SUN_HORIZON_DEG)


def moon_events(lat: float, lon: float, day: date, tz_offset_hours: float = 7.0) -> RiseSet:
    """Moon events for one LOCAL day, returned in UTC."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=tz_offset_hours)
    return find_events(lambda t: moon_altitude(lat, lon, t), start, 24.0, MOON_HORIZON_DEG, 5.0)


def civil_twilight(lat: float, lon: float, day: date, tz_offset_hours: float = 7.0) -> RiseSet:
    """Civil dawn and dusk (sun 6° below the horizon), the low-light feeding window."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=tz_offset_hours)
    return find_events(lambda t: sun_altitude(lat, lon, t), start, 24.0, -6.0)
