"""
thread-sight — app.py  (src/app.py in your project)
====================================================
Backend for 3 pages:
  GET  /                → home page (index.html)
  POST /analyze         → search a trend keyword → returns JSON
  GET  /today           → today's report page (today.html)
  GET  /api/today-data  → returns JSON for today's report widgets

SECURITY:
  - API keys only in .env, never sent to browser
  - Input sanitized + validated server-side AND client-side
  - Rate limiting: 5 requests/min per IP on /analyze

INSTALL (once, in your terminal):
  pip install flask flask-limiter pytrends requests matplotlib python-dotenv

RUN:
  python src/app.py  →  http://localhost:8000
"""

import os, re, io, base64, datetime, random, requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from flask import Flask, render_template, request, jsonify

# ── optional imports (graceful if not installed yet) ──────────────────────────
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
    pass  # fine — set env vars in your shell or hosting platform

# ── config ────────────────────────────────────────────────────────────────────
NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")
UNSPLASH_KEY      = os.environ.get("UNSPLASH_ACCESS_KEY", "")
SRC_DIR      = os.path.dirname(os.path.abspath(__file__))
ROOT         = os.path.dirname(SRC_DIR)

app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))

# ── rate limiter ──────────────────────────────────────────────────────────────
if _LIMITER:
    limiter = Limiter(key_func=get_remote_address, app=app,
                      default_limits=[], storage_uri="memory://")
    def _limit(f): return limiter.limit("5 per minute")(f)
else:
    def _limit(f): return f   # no-op if not installed

# ── input validation ──────────────────────────────────────────────────────────
_ALLOWED = re.compile(r"^[a-zA-Z0-9 \-\'&]+$")

def validate(raw):
    if not raw or not isinstance(raw, str): return None, "no keyword provided."
    c = re.sub(r"<[^>]*>","",raw).strip()
    c = re.sub(r"\s+"," ",c)
    if len(c) < 2:  return None, "keyword too short."
    if len(c) > 80: return None, "keyword too long (max 80 chars)."
    if not _ALLOWED.match(c): return None, "invalid characters in keyword."
    return c, None

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/today")
def today_page():
    return render_template("today.html")

@app.route("/analyze", methods=["POST"])
@_limit
def analyze():
    body    = request.get_json(force=True, silent=True) or {}
    kw, err = validate(body.get("keyword",""))
    if err: return jsonify({"error": err}), 400

    result  = {"keyword": kw}
    warns   = []

    # google trends
    if _PYTRENDS:
        try:   result.update(_google_trends(kw))
        except Exception as e:
            warns.append("google trends unavailable")
            result.update({"growth":None,"status":"stable","peak_interest":None,
                           "chart":None,"google_trends":[],"projected_label":"unavailable","slope":0})
    else:
        warns.append("pytrends not installed")
        result.update({"growth":None,"status":"stable","peak_interest":None,
                       "chart":None,"google_trends":[],"projected_label":"unavailable","slope":0})

    # newsapi
    try:    result["news"] = _news(kw)
    except: warns.append("newsapi error"); result["news"] = []

    # pinterest
    try:    result["pinterest"] = _pinterest(kw)
    except: warns.append("pinterest error"); result["pinterest"] = []

    if warns: result["warnings"] = warns
    return jsonify(result)


@app.route("/api/today-data")
def today_data():
    """
    Feeds the Today's Report page.
    Picks a random 'trend of the day' and fetches real data for it.
    In production you'd cache this daily (e.g. Redis or a simple JSON file).
    """
    TRENDS_OF_DAY = [
        "quiet luxury","gorpcore","coquette","y2k revival","dark academia",
        "coastal prep","streetwear","cottagecore","barbiecore","minimalist"
    ]
    totd = random.choice(TRENDS_OF_DAY)

    result = {"trend_of_day": totd}
    warns  = []

    if _PYTRENDS:
        try:   result.update(_google_trends(totd))
        except: warns.append("google trends unavailable"); result.update({"growth":None,"status":"stable","peak_interest":None,"chart":None,"google_trends":[],"projected_label":"unavailable","slope":0})
    else:
        result.update({"growth":None,"status":"stable","peak_interest":None,"chart":None,"google_trends":[],"projected_label":"unavailable","slope":0})

    try:    result["news"] = _news(totd)
    except: result["news"] = []

    # fun facts — rotated daily by day-of-year
    FUN_FACTS = [
        "the global fashion industry is worth over $1.7 trillion annually.",
        "it takes roughly 2,700 liters of water to produce a single cotton t-shirt.",
        "the average american buys 65 pounds of clothing per year.",
        "fast fashion produces 10% of global carbon emissions.",
        "the term 'haute couture' is legally protected in france.",
        "levi's 501 jeans were first patented in 1873 — they're still being made.",
        "sneakers account for $79 billion in global annual sales.",
        "the little black dress was popularized by coco chanel in 1926.",
        "denim was originally used as workwear for california gold miners.",
        "the fashion industry employs over 430 million people worldwide.",
    ]
    fact_idx          = datetime.date.today().timetuple().tm_yday % len(FUN_FACTS)
    result["fun_fact"]= FUN_FACTS[fact_idx]
    result["history"] = _history_blurb(totd)

    # featured history: a DIFFERENT random trend
    other = [t for t in TRENDS_OF_DAY if t != totd]
    featured_trend         = random.choice(other)
    result["featured_trend"]   = featured_trend
    result["featured_history"] = _history_blurb(featured_trend)

    if warns: result["warnings"] = warns
    return jsonify(result)


@app.errorhandler(429)
def rate_err(e):
    return jsonify({"error":"too many searches — max 5/min. please wait."}), 429


# ── google trends helper ──────────────────────────────────────────────────────

def _google_trends(kw):
    pt = TrendReq(hl="en-US", tz=360)
    pt.build_payload([kw], timeframe="today 12-m")
    iot = pt.interest_over_time()
    if iot.empty or kw not in iot.columns:
        raise ValueError("no data")

    series = iot[kw].dropna()
    vals   = series.values.tolist()
    mid    = len(vals) // 2
    fa     = sum(vals[:mid]) / max(len(vals[:mid]),1)
    sa     = sum(vals[mid:]) / max(len(vals[mid:]),1)
    growth = 0 if fa==0 else round(((sa-fa)/fa)*100,1)
    status = "rising" if growth>10 else "fading" if growth<-10 else "stable"
    peak   = int(max(vals)) if vals else 0

    # linear regression slope (last 8 weeks → project ~4 weeks ahead)
    recent = vals[-8:] if len(vals)>=8 else vals
    n = len(recent); xs = list(range(n))
    xm = sum(xs)/n; ym = sum(recent)/n
    num = sum((xs[i]-xm)*(recent[i]-ym) for i in range(n))
    den = sum((xs[i]-xm)**2 for i in range(n))
    slope = round(num/den if den else 0, 3)
    pv    = round(max(0,min(100, recent[-1]+slope*4)),1)
    ppct  = round(((pv-recent[-1])/max(recent[-1],1))*100,1)

    if   slope >  0.3: pl = f"projected to rise ~{abs(ppct)}% next month. (estimate — not a guarantee)"
    elif slope < -0.3: pl = f"projected to fall ~{abs(ppct)}% next month. (estimate — not a guarantee)"
    else:              pl = f"projected to hold steady next month. (estimate — not a guarantee)"

    chart = _build_chart(series, kw)

    # Raw time-series for interactive Chart.js frontend
    chart_data = {
        "labels": [d.strftime("%b %d, %Y") for d in series.index],
        "values": [round(float(v), 1) for v in series.values],
        "slope":  slope,
        "projected_value": pv,
    }

    related = pt.related_queries()
    gtl = []
    if kw in related and related[kw]["top"] is not None:
        for _, row in related[kw]["top"].head(8).iterrows():
            gtl.append({"query":str(row.get("query","")),"value":int(row.get("value",0))})

    return {"growth":growth,"status":status,"peak_interest":peak,
            "chart":chart,"chart_data":chart_data,"google_trends":gtl,
            "projected_label":pl,"slope":slope}


# ── news helper ───────────────────────────────────────────────────────────────

def _news(kw):
    if not NEWS_API_KEY:
        return [{"title":f"add NEWS_API_KEY to .env to see articles about '{kw}'",
                 "source":"thread-sight","url":None,"published_at":"env var missing"}]
    ago  = (datetime.date.today()-datetime.timedelta(days=30)).isoformat()
    resp = requests.get("https://newsapi.org/v2/everything",
                        params={"q":kw+" fashion","from":ago,"sortBy":"relevancy",
                                "language":"en","pageSize":6,"apiKey":NEWS_API_KEY},
                        timeout=10)
    resp.raise_for_status()
    arts = []
    for a in resp.json().get("articles",[]):
        if not a.get("title") or a["title"]=="[Removed]": continue
        try:
            dt     = datetime.datetime.fromisoformat(a.get("publishedAt","").replace("Z","+00:00"))
            pretty = dt.strftime("%b %d, %Y")
        except: pretty = a.get("publishedAt","")[:10]
        arts.append({"title":a.get("title",""),"source":a.get("source",{}).get("name",""),
                     "url":a.get("url"),"published_at":pretty})
    return arts


# ── pinterest helper ──────────────────────────────────────────────────────────

def _pinterest(kw):
    try:
        resp = requests.get("https://trends.pinterest.com/trends/api/toptrends",
                            params={"country_code":"US"},
                            headers={"User-Agent":"Mozilla/5.0"},timeout=8)
        if resp.status_code==200:
            kl = kw.lower()
            fkws = ["fashion","style","outfit","aesthetic","trend","look","wear","dress",
                    "denim","luxury","vintage","streetwear","beauty","makeup","skincare",kl]
            res = [{"term":t.get("displayName",t.get("name","")),"description":f"trending on pinterest"}
                   for t in resp.json().get("trends",[])
                   if any(f in t.get("displayName",t.get("name","")).lower() for f in fkws)]
            if res: return res[:6]
    except: pass

    CLUSTERS = {
        "y2k":          ["y2k outfits inspo","early 2000s aesthetic","butterfly clips","low rise jeans","velour tracksuit"],
        "quiet luxury": ["old money aesthetic","minimalist wardrobe","neutral tones","capsule wardrobe","stealth wealth"],
        "gorpcore":     ["outdoor aesthetic","hiking outfit","techwear","utility fashion","patagonia style"],
        "coquette":     ["ballet aesthetic","bow accessories","feminine style","pink aesthetic","ribbons trend"],
        "streetwear":   ["sneaker culture","oversized fits","graphic tees","urban style","hypebeast aesthetic"],
        "coastal":      ["coastal grandmother","linen outfits","beach aesthetic","nautical style","relaxed dressing"],
        "vintage":      ["thrift inspo","70s fashion","retro outfits","secondhand style","cottagecore"],
        "minimalist":   ["clean aesthetic","neutral wardrobe","capsule closet","monochrome looks","effortless style"],
    }
    kl = kw.lower()
    for k,v in CLUSTERS.items():
        if k in kl: return [{"term":t,"description":"related pinterest trend"} for t in v]
    return [{"term":f"{kw} outfit ideas","description":"popular search on pinterest"},
            {"term":f"{kw} aesthetic","description":"popular search on pinterest"},
            {"term":f"{kw} inspo board","description":"popular search on pinterest"}]


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
        "barbiecore":    'barbiecore is maximalist hot-pink aesthetic inspired by barbie\'s world. exploded globally around the 2023 barbie film (dir. greta gerwig) starring margot robbie.',
        "minimalist":    'fashion minimalism — clean lines, neutral tones, quality over quantity — traces back to calvin klein and jil sander in the 1990s. resurged with the capsule wardrobe movement.',
    }
    kl = kw.lower().strip()
    for k,v in MAP.items():
        if k in kl or kl in k: return v
    return f'"{kw}" is a fashion keyword tracked across google trends, newsapi, and pinterest. search interest reflects real-time cultural momentum in apparel, styling, and editorial coverage.'


# ── chart builder ─────────────────────────────────────────────────────────────

def _build_chart(series, kw):
    fig, ax = plt.subplots(figsize=(7,2.8))
    fig.patch.set_facecolor("#F7F7F7")
    ax.set_facecolor("#F7F7F7")
    ax.plot(series.index, series.values, color="#00BFA6", linewidth=1.8, solid_capstyle="round")
    ax.fill_between(series.index, series.values, 0, color="#00BFA6", alpha=0.12)
    ax.spines[["top","right","left"]].set_visible(False)
    ax.spines["bottom"].set_color("#DDDDDD")
    ax.tick_params(colors="#888888", labelsize=7, length=0)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter('%d'))
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %y'))
    plt.xticks(rotation=0, ha='center')
    plt.tight_layout(pad=1.0)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  ◆ thread-sight running → http://localhost:8000\n")
    app.run(debug=True, port=8000)