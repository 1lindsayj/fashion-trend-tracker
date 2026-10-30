"""
thread-sight — src/app.py   
=============================================================================

Routes
  GET  /                 home page
  GET  /today            today's report page
  GET  /compare          compare page                          [new]
  POST /analyze          one keyword  -> verdict + chart data
  POST /api/compare      2-4 keywords -> shared chart + verdicts [new]
  GET  /api/today-data   trend of the day (same trend all day)   [new]
  GET  /api/lookbook     real photos via the Unsplash API        [new]

RUN (from the repo root):
  pip install -r requirements.txt
  python src/app.py   ->  http://localhost:8000
"""
import base64
import datetime
import hashlib
import io
import json
import logging
import os
import re
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import requests
from flask import Flask, jsonify, render_template, request

from verdict import analyze_series, rescale          

log = logging.getLogger("thread-sight")
logging.basicConfig(level=logging.INFO)

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _LIMITER = True
except ImportError:
    _LIMITER = False
    print("[thread-sight] tip: pip install flask-limiter  (rate limiting disabled)")

try:
    from pytrends.request import TrendReq
    _PYTRENDS = True
except ImportError:
    _PYTRENDS = False
    print("[thread-sight] tip: pip install pytrends  (google trends disabled)")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── config ────────────────────────────────────────────────────────────────────
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
TRENDS_GEO   = os.environ.get("TRENDS_GEO", "")        # "" = worldwide, "US" = United States
TIMEFRAME    = "today 5-y"                              # [new] 5y of weekly data -> seasonality check
FRESH_SECONDS = 12 * 3600                               # [new] reuse a saved result for 12h

SRC_DIR   = os.path.dirname(os.path.abspath(__file__))
ROOT      = os.path.dirname(SRC_DIR)                    # repo root (after flattening, see [#13])
CACHE_DIR = os.environ.get("TS_CACHE_DIR", os.path.join(ROOT, "cache"))        # [new] live cache (git-ignored)
DEMO_DIR  = os.path.join(ROOT, "demo_cache")            # [new] committed snapshots (scripts/build_demo_cache.py)

app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))

# ── rate limiter ──────────────────────────────────────────────────────────────
if _LIMITER:
    limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[], storage_uri="memory://")

    def _limit(spec):
        return limiter.limit(spec)
else:
    def _limit(spec):                                   # no-op if not installed
        return lambda f: f


# ── input validation ──────────────────────────────────────────────────────────
# [new] accents/other alphabets + "." and "/" are now allowed 
_ALLOWED = re.compile(r"^[\w \-'&./]+$", re.UNICODE)


def validate(raw):
    if not raw or not isinstance(raw, str):
        return None, "no keyword provided."
    c = re.sub(r"<[^>]*>", "", raw).strip()
    c = re.sub(r"\s+", " ", c)
    if len(c) < 2:
        return None, "keyword too short."
    if len(c) > 80:
        return None, "keyword too long (max 80 chars)."
    if not _ALLOWED.match(c):
        return None, "invalid characters in keyword."
    return c, None


# ── tiny helpers: errors, memo, cache ─────────────────────────────────────────
class TrendsUnavailable(Exception):
    """Google didn't answer (rate limit, network, library problem). Safe to retry later."""


class NoData(Exception):
    """Google answered, but has no (or too little) data for this term."""


_MEM = {}


def memo(key, ttl, fn):
    """Remember fn()'s result in memory for ttl seconds. Exceptions are never cached."""
    hit = _MEM.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _MEM[key] = (time.time(), val)
    return val


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _cache_name(key):
    return hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:16] + ".json"


def cache_read(key):
    """Newest saved result for key: live cache first, then the committed demo snapshot."""
    for source, folder in (("live", CACHE_DIR), ("demo", DEMO_DIR)):
        path = os.path.join(folder, _cache_name(key))
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    entry = json.load(f)
                entry["_source"] = source
                return entry
            except (OSError, ValueError):
                log.warning("unreadable cache file %s", path)
    return None


def cache_write(key, entry, folder=None):
    folder = folder or CACHE_DIR
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _cache_name(key))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f)
    os.replace(tmp, path)                               # never leaves a half-written file


def _is_fresh(entry):
    if not entry or entry.get("_source") != "live":
        return False
    saved = datetime.datetime.fromisoformat(entry["saved_at"])
    return (_now() - saved).total_seconds() < FRESH_SECONDS


def _pretty_date(iso):
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%b %d, %Y").lower()
    except (TypeError, ValueError):
        return "an earlier date"


# ── google trends ────
def _fetch_frame(kws):
    """Weekly interest for 1-4 keywords. Raises TrendsUnavailable or NoData — never returns fake data."""
    if not _PYTRENDS:
        raise TrendsUnavailable("pytrends is not installed")
    try:
        # NOTE [#1]: do not pass retries=/backoff_factor= to TrendReq. pytrends 4.9.2 builds an urllib3
        # retry(method_whitelist=...) that urllib3 2.x rejects. handle retries in the caller.
        pt = TrendReq(hl="en-US", tz=360)
        pt.build_payload(kws, timeframe=TIMEFRAME, geo=TRENDS_GEO)
        iot = pt.interest_over_time()
    except Exception as e:                              # 429s, timeouts, library errors
        raise TrendsUnavailable(f"{type(e).__name__}: {e}") from e

    if iot is None or iot.empty:
        raise NoData("empty response")

    # [new] drop Google's "partial" newest week; was incomplete and drags the recent trend down
    if "isPartial" in iot.columns:
        iot = iot[~iot["isPartial"].astype(bool)].drop(columns=["isPartial"])
    missing = [k for k in kws if k not in iot.columns]
    if missing or iot.empty:
        raise NoData("missing columns: %s" % missing)
    return iot


def _series_entry(iot, kws):
    return {
        "saved_at": _now().isoformat(),
        "dates": [d.strftime("%Y-%m-%d") for d in iot.index],
        "series": {k: [round(float(v), 1) for v in iot[k].fillna(0).tolist()] for k in kws},
    }


def _series_for(entry, kw):
    """Cache keys are case-insensitive, so look the series up the same way."""
    for k, v in entry["series"].items():
        if k.lower() == kw.lower():
            return v
    raise NoData("series missing for %r" % kw)


def _with_cache(key, fetch):
    """CHANGED [#1][#10] fresh cache -> live fetch -> stale cache/demo snapshot -> error.
    Returns (entry, stale). Raises NoData / TrendsUnavailable. Never invents a result."""
    cached = cache_read(key)
    if _is_fresh(cached):
        return cached, False
    try:
        entry = fetch()
        cache_write(key, entry)
        entry["_source"] = "live"
        return entry, False
    except NoData:
        raise
    except TrendsUnavailable as e:
        log.warning("google trends unavailable for %r: %s", key, e)
        if cached:
            return cached, True
        raise


def get_trend(kw):
    return _with_cache("trend:" + kw, lambda: _series_entry(_fetch_frame([kw]), [kw]))


def get_compare(kws):
    key = "compare:" + "|".join(sorted(k.lower() for k in kws))
    return _with_cache(key, lambda: _series_entry(_fetch_frame(kws), kws))


# ── response builders ─────────────────────────────────────────────────────────
def _stale_fields(entry, stale):
    return {
        "stale": stale,
        "data_source": entry.get("_source", "live"),
        "cached_at": entry.get("saved_at"),
        "cached_at_pretty": _pretty_date(entry.get("saved_at")),
    }


def build_analysis(kw, entry, stale):
    values = _series_for(entry, kw)
    dates = entry["dates"]
    v = analyze_series(values, name=kw)                 # [new] verdicts judge each term against ITS OWN history; the chart shows them on one shared scale
    return {
        "keyword": kw,
        "status": v["label"],
        "label_display": v["label_display"],
        "confidence": v["confidence"],
        "headline": v["headline"],
        "reason": v["reason"],
        "detail": v["detail"],
        "growth": v["detail"].get("change_pct"),
        "chart": _build_chart(dates[-52:], values[-52:]),
        "chart_data": {
            "labels": [datetime.date.fromisoformat(d).strftime("%b %d, %Y") for d in dates],
            "values": values,
        },
        **_stale_fields(entry, stale),
    }


# ── routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/today")
def today_page():
    return render_template("today.html")


@app.route("/compare")                                   # [new] this route did not exist (404)
def compare_page():
    return render_template("compare.html")


def _err_response(e):
    """CHANGED [#1] a failed lookup is an ERROR, not a 'stable' verdict."""
    if isinstance(e, NoData):
        return jsonify({"error": "google doesn't have enough search data for that term. try something broader.",
                        "code": "no_data"}), 404
    return jsonify({"error": "google trends isn't responding right now (usually a temporary rate limit). "
                             "try again in a minute.", "code": "trends_unavailable"}), 503


@app.route("/analyze", methods=["POST"])
@_limit("10 per minute")
def analyze():
    body = request.get_json(force=True, silent=True) or {}
    kw, err = validate(body.get("keyword", ""))
    if err:
        return jsonify({"error": err}), 400
    try:
        entry, stale = get_trend(kw)
    except (NoData, TrendsUnavailable) as e:
        return _err_response(e)

    result = build_analysis(kw, entry, stale)
    warns = []
    articles, total, news_warn = _news(kw)               # [new] more accurate total, honest warnings
    result.update({"news": articles, "news_total": total})
    if news_warn:
        warns.append(news_warn)
    if warns:
        result["warnings"] = warns
    return jsonify(result)


@app.route("/api/compare", methods=["POST"])             # [new] this route did not exist (404)
@_limit("6 per minute")
def api_compare():
    body = request.get_json(force=True, silent=True) or {}
    raw = body.get("keywords", [])
    if not isinstance(raw, list) or not 2 <= len(raw) <= 4:
        return jsonify({"error": "pick 2 to 4 trends to compare."}), 400
    kws, seen = [], set()
    for r in raw:
        kw, err = validate(r)
        if err:
            return jsonify({"error": f"'{r}': {err}"}), 400
        if kw.lower() not in seen:
            seen.add(kw.lower())
            kws.append(kw)
    if len(kws) < 2:
        return jsonify({"error": "pick 2 to 4 different trends."}), 400
    try:
        entry, stale = get_compare(kws)
    except (NoData, TrendsUnavailable) as e:
        return _err_response(e)

    items = []
    for kw in kws:
        # verdicts judge each term against ITS OWN history; the chart shows them on one shared scale
        v = analyze_series(rescale(_series_for(entry, kw)), name=kw)
        items.append({"keyword": kw, "status": v["label"], "label_display": v["label_display"],
                      "confidence": v["confidence"], "reason": v["reason"], "detail": v["detail"]})
    return jsonify({
        "keywords": kws,
        "labels": [datetime.date.fromisoformat(d).strftime("%b %d, %Y") for d in entry["dates"]],
        "series": {kw: _series_for(entry, kw) for kw in kws},
        "items": items,
        "headline": _compare_headline(items),
        **_stale_fields(entry, stale),
    })


def _compare_headline(items):
    """One-sentence takeaway for the compare page (data storytelling)."""
    rising = [i for i in items if i["status"] in ("rising", "peaking") and i["detail"].get("change_pct") is not None]
    if rising:
        best = max(rising, key=lambda i: i["detail"]["change_pct"])
        return (f"{best['keyword']} has the strongest momentum: up {best['detail']['change_pct']:.0f}% "
                f"versus its own prior 6 months.")
    spiking = [i for i in items if i["status"] in ("spiking", "one_off_spike")]
    if spiking:
        return f"{spiking[0]['keyword']} looks like a short spike, not a lasting climb. None of these is clearly rising."
    return "None of these is clearly rising right now. See each trend's verdict below."


# [new] "trend of the day" is picked by DATE (same all day) instead of random.choice per request
TRENDS_OF_DAY = ["quiet luxury", "gorpcore", "coquette", "y2k revival", "dark academia",
                 "coastal prep", "streetwear", "cottagecore", "barbiecore", "minimalist"]
FUN_FACTS = [
    "the term 'haute couture' is legally protected in france. — Fédération de la Haute Couture et de la Mode",
    "levi's 501 jeans were first patented in 1873 — they're still being made. — Levi Strauss & Co.",
    "denim became popular as durable workwear for california gold miners. — California State Capitol Museum – Denim Exhibit",
    "the little black dress was popularized by coco chanel in 1926. —  Victoria & Albert Museum",
    "the global fashion industry is worth over $1.7 trillion annually. — KPMG – Consumer & Retail Industry Analysis 2025",
    "it takes roughly 2,700 liters of water to produce a single cotton t-shirt. — The World Resources Institute",
    "the global fashion industry is responsible for an estimated 2–8% of global carbon emissions, with some estimates reaching 10%. — UN Environment Programme",
    "the fashion industry employs over 430 million people worldwide. — Solidarity Center – Global Garment and Textile Industries",
]


def pick_today(today=None):
    today = today or datetime.date.today()
    i = today.toordinal() % len(TRENDS_OF_DAY)
    return TRENDS_OF_DAY[i], TRENDS_OF_DAY[(i + 1) % len(TRENDS_OF_DAY)], today


@app.route("/api/today-data")
@_limit("30 per minute")                                  
def today_data():
    totd, featured, today = pick_today()
    result = {"trend_of_day": totd, "featured_trend": featured,
              "history": _history_blurb(totd), "featured_history": _history_blurb(featured),
              "fun_fact": FUN_FACTS[today.timetuple().tm_yday % len(FUN_FACTS)]}
    warns = []
    try:                                                  # [new] "unavailable" message instead of defaulting to "stable"
        entry, stale = get_trend(totd)
        a = build_analysis(totd, entry, stale)
        result.update({"status": a["status"], "label_display": a["label_display"], "headline": a["headline"],
                       "reason": a["reason"], "confidence": a["confidence"], "stale": a["stale"],
                       "cached_at_pretty": a["cached_at_pretty"]})
    except (NoData, TrendsUnavailable):
        result.update({"status": "unavailable", "label_display": "data unavailable"})
        warns.append("google trends isn't responding right now, so today's verdict is unavailable.")
    articles, total, news_warn = _news(totd)
    result.update({"news": articles, "news_total": total})
    if news_warn:
        warns.append(news_warn)
    if warns:
        result["warnings"] = warns
    return jsonify(result)


@app.route("/api/lookbook")                               # [new] real photos
@_limit("20 per minute")
def api_lookbook():
    kw, err = validate(request.args.get("q", ""))
    if err:
        return jsonify({"error": err}), 400
    if not UNSPLASH_KEY:
        return jsonify({"photos": [], "reason": "no_key"})
    try:
        return jsonify({"photos": memo("unsplash:" + kw.lower(), 24 * 3600, lambda: _unsplash(kw))})
    except Exception as e:
        log.warning("unsplash failed: %s", e)
        return jsonify({"photos": [], "reason": "unavailable"})


def _unsplash(kw):
    resp = requests.get("https://api.unsplash.com/search/photos",
                        params={"query": f"{kw} fashion outfit", "per_page": 8, "orientation": "portrait"},
                        headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"}, timeout=8)
    resp.raise_for_status()
    utm = "?utm_source=thread-sight&utm_medium=referral"   # unsplash API guidelines: credit the photographer(s)
    return [{"thumb": p["urls"]["small"], "url": p["urls"]["regular"], "alt": p.get("alt_description") or kw,
             "credit": p["user"]["name"], "credit_url": p["user"]["links"]["html"] + utm}
            for p in resp.json().get("results", [])]


@app.errorhandler(429)
def rate_err(e):
    return jsonify({"error": "too many searches — please wait a minute and try again."}), 429


# ── news helper ───────────────────────────────────────────────────────────────
def _news(kw):
    """CHANGED [#4] returns (articles, total, warning). total is NewsAPI's real 30-day count,
    not len(articles) (which was always capped at 6). No more placeholder 'article'."""
    if not NEWS_API_KEY:
        return [], None, "news is off. add NEWS_API_KEY to .env to see articles."

    def go():
        ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        resp = requests.get("https://newsapi.org/v2/everything",
                            params={"q": f'"{kw}" fashion', "from": ago, "sortBy": "relevancy",
                                    "language": "en", "pageSize": 6, "apiKey": NEWS_API_KEY}, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        arts = []
        for a in payload.get("articles", []):
            if not a.get("title") or a["title"] == "[Removed]":
                continue
            try:
                dt = datetime.datetime.fromisoformat(a.get("publishedAt", "").replace("Z", "+00:00"))
                pretty = dt.strftime("%b %d, %Y")
            except ValueError:
                pretty = a.get("publishedAt", "")[:10]
            arts.append({"title": a["title"], "source": a.get("source", {}).get("name", ""),
                         "url": a.get("url"), "published_at": pretty})
        return arts, int(payload.get("totalResults", len(arts)))

    try:                                                  # [new] note: NewsAPI's free tier only allows ~100 requests/day
        arts, total = memo("news:" + kw.lower(), 6 * 3600, go)
        return arts, total, None
    except Exception as e:                                
        log.warning("newsapi failed: %s", e)
        return [], None, "news is unavailable right now."


# ── history blurbs ────────────────────────────────────────────────────────────
def _history_blurb(kw):
    MAP = {
        "quiet luxury":  '"quiet luxury" emerged ~2022 as a reaction to logo-heavy fashion — neutral palettes, quality fabrics, minimal branding. think the row, loro piana, bottega veneta.',
        "gorpcore":      'gorpcore (gear + outdoor) peaked ~2021, blending technical hiking gear with everyday fashion. fleeces, trail runners, shell jackets went from trails to city streets.',
        "y2k revival":   'y2k fashion references the late 1990s–early 2000s — low-rise jeans, butterfly clips, velour tracksuits, and brash branding. revived around 2020–2022 via tiktok.',
        "coquette":      'coquette aesthetic draws from ballet, bows, and soft femininity — sheer fabrics, ribbons, pastels. went viral on tiktok around 2022–2023 as "balletcore" exploded.',
        "dark academia": 'dark academia blends preppy academics with gothic, literary references — tweed, plaid, turtlenecks. became a massive aesthetic movement on tumblr and tiktok in 2020.',
        "coastal prep":  'coastal prep references old-money east-coast americana — linen, navy stripes, boat shoes. intersects with "coastal grandmother" and nautical aesthetics.',
        "streetwear":    'streetwear traces its roots to 1980s skate and hip-hop culture. codified by brands like supreme, stüssy, and off-white. became a dominant luxury force by the 2010s.',
        "cottagecore":   'cottagecore romanticizes rural, pastoral life — linen, floral prints, handcraft. surged during the 2020 pandemic as people sought escapist, soft aesthetics online.',
        "barbiecore":    "barbiecore is maximalist hot-pink aesthetic inspired by barbie's world. exploded globally around the 2023 barbie film (dir. greta gerwig) starring margot robbie.",
        "minimalist":    'fashion minimalism — clean lines, neutral tones, quality over quantity — traces back to calvin klein and jil sander in the 1990s. resurged with the capsule wardrobe movement.',
    }
    kl = kw.lower().strip()
    for k, v in MAP.items():
        if k in kl or kl in k:
            return v
    # [new] removes pinterest as claimed source since it was removed during this rework. will be re-added as a source once all changes are made (likely future phase)
    return f'"{kw}" is a fashion keyword tracked with google search interest and recent news coverage.'


# ── chart builder (hero thumbnail) ────────────────────────────────────────────
def _build_chart(dates, values):
    """CHANGED [#3] takes plain lists (the cache stores lists, not a pandas Series)."""
    xs = [datetime.date.fromisoformat(d) for d in dates]
    fig, ax = plt.subplots(figsize=(7, 2.8))
    fig.patch.set_facecolor("#F7F7F7")
    ax.set_facecolor("#F7F7F7")
    ax.plot(xs, values, color="#00BFA6", linewidth=1.8, solid_capstyle="round")
    ax.fill_between(xs, values, 0, color="#00BFA6", alpha=0.12)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#DDDDDD")
    ax.tick_params(colors="#666666", labelsize=8, length=0)       # darker ticks for contrast
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%d"))
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    plt.tight_layout(pad=1.0)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n  ◆ thread-sight running → http://localhost:8000\n")
    # [new] debug is OFF unless opted in:  FLASK_DEBUG=1 python src/app.py
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 8000)))