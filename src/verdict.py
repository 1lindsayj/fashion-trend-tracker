"""
thread-sight — src/verdict.py   [new] 10/30/26
=====================================================
turns the weekly Google-Trends series into a plain-language verdict.

only python: no flask, no network. making it easier to unit-test and tune.
every threshold lives in the CONSTANTS block so they can be adjusted by one number and re-run
`pytest` to see which known cases change.

verdicts (label):
  rising · peaking · steady · fading · seasonal · one_off_spike · spiking · insufficient_data

notes:
  * a single one-week spike no longer reads as growth
  * a yearly cycle (e.g. "linen") is called seasonal instead of rising/fading
  * the verdict always carries a confidence level and the numbers behind it
"""
import math
from statistics import median

# ── CONSTANTS (step 1) ───────────────────────────────────────
RECENT_WEEKS    = 4      # "right now" = median of the last 4 weeks
BASELINE_WEEKS  = 26     # "before" = median of the 26 weeks prior to that
MIN_WEEKS       = 30     # need at least recent + baseline to say anything
MIN_VOLUME      = 5      # index below this (out of 100) = too little search volume
RISE_PCT        = 25.0   # recent vs baseline: >= +25% rising, <= -25% fading
FALL_PCT        = -25.0
PEAK_NEAR_MAX   = 0.85   # "peaking" = recent is within 15% of the 52-week high ...
PEAK_FLAT_PCT   = 5.0    # ... and the last 4 weeks are no longer climbing (<= +5%)
SPIKE_RATIO     = 2.5    # a "spike" week is >= 2.5x the 26-week median
SPIKE_MAX_WEEKS = 4      # ... and lasts at most 4 weeks
SEASONAL_CORR   = 0.6    # this year's curve vs last year's (Pearson r)
SEASONAL_RANGE  = 15     # ... and the curve must actually move (max-min >= 15 pts)

DISPLAY = {
    "rising": "rising", "peaking": "peaking", "steady": "steady", "fading": "fading",
    "seasonal": "seasonal", "one_off_spike": "one-off spike", "spiking": "spiking",
    "insufficient_data": "not enough data",
}


# ── helpers ──────────────────────────────────────────────────────────────────
def _pct(new, old):
    """% change, or None when the old value is ~0 (avoids divide-by-zero)."""
    if old is None or old < 1:
        return None
    return round((new - old) / old * 100, 1)


def _corr(a, b):
    """Pearson correlation of two equal-length lists (None if undefined)."""
    n = len(a)
    if n < 8 or n != len(b):
        return None
    ma, mb = sum(a) / n, sum(b) / n
    da, db = [x - ma for x in a], [y - mb for y in b]
    den = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return None if den == 0 else sum(x * y for x, y in zip(da, db)) / den


def rescale(values):
    """Rescale a series so its own peak is 100 (used when comparing several terms)."""
    m = max(values) if values else 0
    return [v * 100.0 / m for v in values] if m > 0 else list(values)


def _confidence(vals, baseline):
    pts = 0
    if len(vals) >= 104:            # two+ years of history
        pts += 1
    if baseline >= 15:              # enough search volume to be stable
        pts += 1
    tail = vals[-52:]
    diffs = [abs(tail[i] - tail[i - 1]) for i in range(1, len(tail))]
    if diffs and median(diffs) / max(baseline, 1) <= 0.15:   # low week-to-week noise
        pts += 1
    return "high" if pts == 3 else "medium" if pts == 2 else "low"


def _out(label, confidence, headline, reason, detail):
    return {"label": label, "label_display": DISPLAY[label], "confidence": confidence,
            "headline": headline, "reason": reason, "detail": detail}


# ── main entry point ─────────────────────────────────────────────────────────
def analyze_series(values, name="this trend"):
    """values: weekly interest (0-100), oldest -> newest, partial week already removed."""
    vals = [float(v) for v in values]
    n = len(vals)
    detail = {"weeks": n}

    if n < MIN_WEEKS:
        return _out("insufficient_data", "low", "Not enough history to call it.",
                    f"Only {n} weeks of data. Need at least {MIN_WEEKS}.", detail)

    recent   = median(vals[-RECENT_WEEKS:])
    baseline = median(vals[-(RECENT_WEEKS + BASELINE_WEEKS):-RECENT_WEEKS])
    peak52   = max(vals[-52:])
    change   = _pct(recent, baseline)
    momentum = _pct(median(vals[-4:]), median(vals[-8:-4]))
    detail.update(recent=round(recent, 1), baseline=round(baseline, 1),
                  change_pct=change, peak_52w=round(peak52), momentum_pct=momentum)

    if max(recent, baseline) < MIN_VOLUME:
        return _out("insufficient_data", "low", "Too few searches to read.",
                    "Search volume is very low, so week-to-week changes are mostly noise. "
                    "Try a broader term.", detail)

    confidence = _confidence(vals, baseline)

    # 1) seasonal: this year's curve repeats last year's -------------------------
    if n >= 104:
        this_yr, last_yr = vals[-52:], vals[-104:-52]
        r = _corr(this_yr, last_yr)
        yoy = _pct(median(vals[-8:]), median(vals[-60:-52]))
        detail.update(seasonal_corr=None if r is None else round(r, 2), yoy_pct=yoy)
        if r is not None and r >= SEASONAL_CORR and (max(this_yr) - min(this_yr)) >= SEASONAL_RANGE:
            cmp_txt = ""
            if yoy is not None:
                cmp_txt = (f" Compared with the same weeks last year it is "
                           f"{abs(yoy):.0f}% {'higher' if yoy > 0 else 'lower'}.")
            return _out("seasonal", confidence, f"{name} follows a yearly cycle.",
                        "This year's curve closely matches last year's, so ups and downs are "
                        "mostly the calendar, not a new trend." + cmp_txt, detail)

    # 2) spike: a short burst, not a trend -------------------------------------
    window = vals[-26:]
    med_w = max(median(window), 1)
    hot = [v for v in window if v >= SPIKE_RATIO * med_w]
    if hot and len(hot) <= SPIKE_MAX_WEEKS:
        spike_peak = max(hot)
        detail.update(spike_weeks=len(hot), spike_peak=round(spike_peak))
        if vals[-1] >= SPIKE_RATIO * med_w:        # still happening
            return _out("spiking", "low", f"{name} just jumped. Too early to call.",
                        f"Interest is about {vals[-1] / med_w:.1f}x its usual level in the last "
                        f"{len(hot)} week(s). Check back in 2-3 weeks to see if it holds. "
                        "Sudden jumps are often tied to an event or news moment.", detail)
        if recent < 0.5 * spike_peak:              # already over
            return _out("one_off_spike", confidence, f"{name} had a one-off spike.",
                        f"Interest hit {spike_peak:.0f}/100 for {len(hot)} week(s), then fell back "
                        f"to near its usual level ({recent:.0f}/100). That pattern usually means "
                        "an event or news moment, not a lasting trend.", detail)

    # 3) trend direction ---------------------------------------------------------
    chg = 0.0 if change is None else change
    base_txt = (f"The last {RECENT_WEEKS} weeks average {recent:.0f}/100 versus "
                f"{baseline:.0f}/100 over the 6 months before")
    if chg >= RISE_PCT:
        if recent >= PEAK_NEAR_MAX * peak52 and (momentum is None or momentum <= PEAK_FLAT_PCT):
            return _out("peaking", confidence, f"{name} is near its peak.",
                        f"{base_txt} (+{chg:.0f}%), and it is within 15% of its highest point this "
                        "year with growth flattening. It may be close to its top.", detail)
        return _out("rising", confidence, f"{name} is rising.",
                    f"{base_txt} (+{chg:.0f}%), and it is still moving up.", detail)
    if chg <= FALL_PCT:
        return _out("fading", confidence, f"{name} is fading.",
                    f"{base_txt} ({chg:.0f}%).", detail)
    return _out("steady", confidence, f"{name} is holding steady.",
                f"{base_txt}, a change of {chg:+.0f}%. That is within the normal range.", detail)