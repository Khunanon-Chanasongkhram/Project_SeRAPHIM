"""SOS domain logic: needs, severity, geohashing and clustering.

This module is the **specification**. The production runtime is a Cloudflare Worker
written in JavaScript (`api/src/index.js`), and both implementations must satisfy the
same conformance cases in `api/conformance/`. Keeping the rules in one readable place
and testing both against it is how two implementations stay honest about agreeing.

Severity decides who gets a boat first. It is a transparent rule table, not a model:
when this orders a queue during a flood, a responder is entitled to see why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# What people actually ask for.
#
# `base` is the severity this need alone justifies. Data minimisation applies: this
# list is deliberately short, and every entry exists because it changes what a
# responder brings or how fast they come.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Need:
    code: str
    th: str
    en: str
    base: int
    #: Marks a person who cannot self-evacuate. Drives prioritisation independently
    #: of what they asked for.
    vulnerable: bool = False


NEEDS: tuple[Need, ...] = (
    # Life-threatening — someone dies without a response today.
    Need("medical",    "เจ็บป่วยฉุกเฉิน",   "Medical emergency",      5),
    Need("missing",    "คนสูญหาย",          "Missing person",         5),
    Need("trapped",    "ติดอยู่ ออกไม่ได้",  "Trapped, cannot leave",  5),
    Need("oxygen",     "ออกซิเจน",          "Oxygen supply",          5, True),
    Need("dialysis",   "ฟอกไต",             "Dialysis",               5, True),
    # Urgent — movement or life-sustaining supply.
    Need("rescue_boat", "ต้องการเรือ",      "Boat / rescue",          4),
    Need("evacuation", "ต้องการอพยพ",       "Evacuation",             4),
    Need("insulin",    "อินซูลิน",          "Insulin",                4, True),
    Need("medicine",   "ยาประจำตัว",        "Regular medication",     4, True),
    # Vulnerability markers.
    Need("infant",     "เด็กทารก",          "Infant",                 3, True),
    Need("elderly",    "ผู้สูงอายุ",        "Elderly",                3, True),
    Need("disabled",   "ผู้พิการ",          "Person with disability", 3, True),
    Need("pregnant",   "ตั้งครรภ์",         "Pregnant",               3, True),
    # Sustenance and shelter.
    Need("water",      "น้ำดื่ม",           "Drinking water",         3),
    Need("food",       "อาหาร",             "Food",                   2),
    Need("shelter",    "ที่พักพิง",         "Shelter",                2),
    Need("power",      "ไฟฟ้า",             "Power",                  2),
    Need("toilet",     "ห้องน้ำ/สุขอนามัย", "Sanitation",             2),
    # Animals — excluded from severity, but responders need to know what to bring.
    Need("pets",       "สัตว์เลี้ยง",       "Pets",                   1),
    Need("livestock",  "ปศุสัตว์",          "Livestock",              1),
)

NEED_BY_CODE = {n.code: n for n in NEEDS}
VALID_NEEDS = frozenset(NEED_BY_CODE)

#: Chest-deep on an adult: standing, wading or self-rescue stop being possible.
DEPTH_LIFE_THREATENING_CM = 150
#: Waist-deep: dangerous for children, the elderly, and anyone with limited mobility.
DEPTH_DANGEROUS_CM = 100
#: A group this size needs a different response than a household.
LARGE_GROUP = 10
#: Waiting this long without a response is itself an escalation.
AGE_ESCALATE_MINUTES = 360


def needs_are_valid(needs: list[str]) -> bool:
    return bool(needs) and all(n in VALID_NEEDS for n in needs)


def has_vulnerable(needs: list[str]) -> bool:
    return any(NEED_BY_CODE[n].vulnerable for n in needs if n in NEED_BY_CODE)


def derive_severity(
    needs: list[str],
    people_count: int | None = None,
    water_depth_cm: int | None = None,
) -> int:
    """Severity 1-5 from what was reported. 5 is most urgent.

    Starts from the most serious single need, then escalates for circumstances that
    make any need harder to survive. Deliberately errs upward: under-triaging someone
    who drowns is not symmetric with over-triaging someone who was merely frightened.
    """
    known = [NEED_BY_CODE[n] for n in needs if n in NEED_BY_CODE]
    if not known:
        return 1

    severity = max(n.base for n in known)
    vulnerable = any(n.vulnerable for n in known)

    if water_depth_cm is not None:
        if water_depth_cm >= DEPTH_LIFE_THREATENING_CM:
            severity += 1
        elif water_depth_cm >= DEPTH_DANGEROUS_CM and vulnerable:
            severity += 1

    if people_count is not None and people_count >= LARGE_GROUP:
        severity += 1

    return max(1, min(5, severity))


def escalate_for_age(severity: int, waiting_minutes: float) -> int:
    """Raise severity for a request nobody has answered.

    Applied at read time rather than stored, so the queue reorders itself as people
    wait instead of freezing the order they arrived in.
    """
    if waiting_minutes >= AGE_ESCALATE_MINUTES * 2:
        return min(5, severity + 2)
    if waiting_minutes >= AGE_ESCALATE_MINUTES:
        return min(5, severity + 1)
    return severity


# ---------------------------------------------------------------------------
# Geohash — used to collapse many requests on one street into one dispatch.
# ---------------------------------------------------------------------------

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lon: float, precision: int = 7) -> str:
    """Standard geohash. 7 characters is roughly 150 m — about one soi.

    Boundary convention: a coordinate sitting exactly on a cell midpoint goes to the
    HIGH cell (`>=`, not `>`). This matches Redis, Elasticsearch and geohash.org —
    (0, 0) encodes to "s0000...", not "7zzzz...". It only matters on exact boundaries,
    but 0/0 is precisely the value a broken GPS reports, so it is worth matching the
    rest of the world rather than inventing our own answer.
    """
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out: list[str] = []
    bit = 0
    ch = 0
    even = True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_lo = mid
            else:
                ch <<= 1
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_lo = mid
            else:
                ch <<= 1
                lat_hi = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_B32[ch])
            bit = 0
            ch = 0
    return "".join(out)


def cluster(requests: list[dict], prefix: int = 7) -> list[dict]:
    """Group open requests that share a geohash prefix.

    Fourteen requests from one soi should dispatch one boat, not fourteen. Clustering
    is presented to responders as a suggestion with the underlying requests intact —
    it never merges or hides anyone.
    """
    buckets: dict[str, list[dict]] = {}
    for r in requests:
        gh = (r.get("geohash") or "")[:prefix]
        if gh:
            buckets.setdefault(gh, []).append(r)

    clusters = []
    for gh, group in buckets.items():
        if len(group) < 2:
            continue
        clusters.append(
            {
                "geohash": gh,
                "count": len(group),
                "severity": max(g.get("severity", 1) for g in group),
                "people": sum(g.get("people_count") or 0 for g in group),
                "lat": round(sum(g["lat"] for g in group) / len(group), 6),
                "lon": round(sum(g["lon"] for g in group) / len(group), 6),
                "needs": sorted({n for g in group for n in (g.get("needs") or [])}),
                "ids": [g["id"] for g in group],
                "district": group[0].get("district"),
                "province": group[0].get("province"),
            }
        )
    clusters.sort(key=lambda c: (-c["severity"], -c["count"]))
    return clusters


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
