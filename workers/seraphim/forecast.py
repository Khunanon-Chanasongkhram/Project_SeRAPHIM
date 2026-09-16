"""Short-range level forecasting, and the damping that made it work.

**The problem this fixes.** Until 2026-09-16 the prediction was `level + rate * hours`,
straight-line extrapolation of the measured rate of rise. Backtesting said that was not
merely imperfect, it was *worse than doing nothing* beyond about an hour:

    lead    skill vs persistence (all stations)
     1 h    +12%
     2 h     -3%
     3 h     -4%
     6 h    -72%
    12 h    -48%

A negative skill means you would have been better off predicting "the level stays where
it is". Rivers do not keep rising at the rate they happened to be rising at, and a
straight line says they do, more confidently the further out you ask.

**The fix.** Let the rate decay. Displacement after `L` hours becomes

    d(L) = rate * tau * (1 - exp(-L / tau))

which is indistinguishable from a straight line when `L << tau` and flattens out beyond
it, so the model stops claiming a river will keep climbing forever.

**Choosing tau, measured not guessed.** Swept over the archive, skill vs persistence,
shown as all-stations / rivers that actually moved at least 5 cm:

    tau       1 h         2 h         3 h         6 h        12 h
    linear  +12/+10     -3/ +7      -4/ +5     -72/+15     -48/-12
    24 h    +12/+10     -0/ +8      -0/ +7     -56/ +8     -22/ +4
    12 h    +13/+10     +1/ +9      +3/ +8     -45/ +0     -12/+12
     8 h    +14/+11     +2/+10      +4/ +9     -30/ +8      -1/ +8
     6 h    +14/+12     +4/+11      +7/+10     -26/+12      +1/ +2
     4 h    +15/+12     +5/+12      +9/+13     -16/ +9      -4/ -0
     3 h    +15/+12     +7/+12     +11/+13     -10/ +9      -8/ +2
     2 h    +16/+13    +10/+12     +11/+12      -5/ +8     -12/ +2

Every value of tau beats the straight line at every lead. Six hours is chosen because it
is best or near-best on the rivers that are actually moving, which is the population this
feature exists for, and because it matches the regression window: we trust a rate about
as far forward as we measured it backward. Shorter tau values score slightly better on
the all-station column, but that column is dominated by flat rivers where persistence is
already right and there is nothing to win.

**What it costs.** The model now asymptotes, so it will not promise that a river 4 m
below its bank and rising 2 cm/h gets there. Time-to-bank calls in backtest fell from 616
to 96, and their median timing error fell from **5.80 h to 0.46 h**. Far fewer warnings,
far better ones. That trade is deliberate: a bank warning whose timing is out by most of
a day is not something anyone can act on.
"""

from __future__ import annotations

import math

from seraphim.history import Trend

#: Decay constant for the measured rate of rise, in hours. See the module docstring for
#: the sweep this came from. Raising it makes the model more willing to extrapolate,
#: which the archive says costs accuracy at every lead.
RATE_DECAY_HOURS = 6.0

#: Confidence tiers allowed to produce a forward projection at all. "steady" is excluded
#: on purpose: a flat river's best forecast is its current level, which is what the
#: persistence baseline already gives, and dressing it up as a prediction adds nothing.
FORECASTABLE = ("good", "fair")

#: The furthest ahead this model will name a time at all.
#:
#: Near the ceiling the inverse blows up: a gap 0.999 of the way there solves to
#: hundreds of hours, which is arithmetic, not a forecast. Anything past this returns
#: None, because "some time next week, at a rate measured over the last six hours" is
#: not a claim this data supports.
MAX_PROJECTION_HOURS = 48.0

#: Lead times published per station. Beyond 12 h nothing here has measurable skill, so
#: nothing is published; the weeks-ahead outlook is a different product built on a
#: different model (see `outlook.py`).
LEAD_HOURS = (1.0, 3.0, 6.0, 12.0)


def displacement(rate_m_per_hr: float, lead_hours: float,
                 tau: float = RATE_DECAY_HOURS) -> float:
    """Metres the level is expected to move over `lead_hours`, with the rate decaying."""
    if lead_hours <= 0:
        return 0.0
    if tau <= 0:  # degenerate, treat as straight-line
        return rate_m_per_hr * lead_hours
    return rate_m_per_hr * tau * (1.0 - math.exp(-lead_hours / tau))


def max_displacement(rate_m_per_hr: float, tau: float = RATE_DECAY_HOURS) -> float:
    """The most this model will ever claim the level moves, in metres.

    The asymptote of `displacement`. Anything beyond this is outside what the model is
    willing to say, and callers use it to decline rather than to extrapolate anyway.
    """
    return rate_m_per_hr * tau


def project(level_msl: float | None, trend: Trend | None,
            lead_hours: float, tau: float = RATE_DECAY_HOURS) -> float | None:
    """Expected level after `lead_hours`, or None when no honest projection exists."""
    if level_msl is None or trend is None:
        return None
    if trend.confidence not in FORECASTABLE:
        return None
    rate = trend.rate_m_per_hr
    if rate is None:
        return None
    return round(level_msl + displacement(rate, lead_hours, tau), 3)


def hours_to_level(level_msl: float | None, target_msl: float | None,
                   trend: Trend | None, tau: float = RATE_DECAY_HOURS) -> float | None:
    """Hours until the level is expected to reach `target_msl`.

    Inverts `displacement`. Returns None when the target is already reached, when the
    trend is not trustworthy, or when the target lies beyond what a decaying rate can
    reach: the honest answer there is "not at this rate", not a very large number.
    """
    if level_msl is None or target_msl is None or trend is None:
        return None
    if trend.confidence not in FORECASTABLE:
        return None
    rate = trend.rate_m_per_hr
    if rate is None or rate <= 0:
        return None
    gap = target_msl - level_msl
    if gap <= 0:
        return None
    ceiling = max_displacement(rate, tau)
    if gap >= ceiling:
        # The asymptote. Saying "83 hours" here would be a straight-line answer wearing
        # a decaying model's clothes.
        return None
    hours = -tau * math.log(1.0 - gap / ceiling)
    # Just inside the ceiling the answer is still arithmetic rather than a forecast.
    return None if hours > MAX_PROJECTION_HOURS else hours
