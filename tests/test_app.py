"""
tests/test_app.py   (new)   
Backend tests with Google Trends mocked out (no network needed). Run from the repo root:  pytest
"""
import datetime
import math
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app as A  # noqa: E402


class FakeTrends:
    """Stands in for pytrends.TrendReq. Set FakeTrends.mode to simulate Google's behaviour."""
    mode = "ok"          # "ok" | "429" | "empty"
    calls = 0

    def __init__(self, hl=None, tz=None, **kw):
        pass

    def build_payload(self, kws, timeframe=None, geo=None):
        self.kws = kws

    def interest_over_time(self):
        FakeTrends.calls += 1
        if FakeTrends.mode == "429":
            raise RuntimeError("The request failed: Google returned a response with code 429")
        if FakeTrends.mode == "empty":
            return pd.DataFrame()
        idx = pd.date_range(end="2026-10-18", periods=261, freq="W-SUN")
        df = pd.DataFrame({k: [40 + 3 * math.sin(i * 1.7) + n for i in range(261)]
                           for n, k in enumerate(self.kws)}, index=idx)
        df["isPartial"] = False
        df.iloc[-1, df.columns.get_loc("isPartial")] = True      # Google marks the newest week partial
        return df


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(A, "DEMO_DIR", str(tmp_path / "demo"))
    monkeypatch.setattr(A, "TrendReq", FakeTrends, raising=False)
    monkeypatch.setattr(A, "_PYTRENDS", True)
    monkeypatch.setattr(A, "NEWS_API_KEY", "")
    monkeypatch.setattr(A, "UNSPLASH_KEY", "")
    A._MEM.clear()
    FakeTrends.mode, FakeTrends.calls = "ok", 0
    if getattr(A, "limiter", None):
        A.limiter.enabled = False
    yield


@pytest.fixture
def client():
    return A.app.test_client()


# ── [new] failures are errors, never a defaulted "stable" ───────────────────────────
def test_google_down_without_cache_is_503_not_stable(client):
    FakeTrends.mode = "429"
    r = client.post("/analyze", json={"keyword": "quiet luxury"})
    assert r.status_code == 503
    body = r.get_json()
    assert body["code"] == "trends_unavailable"
    assert "status" not in body                      # no verdict at all


def test_no_data_is_404(client):
    FakeTrends.mode = "empty"
    r = client.post("/analyze", json={"keyword": "zzzz qqqq"})
    assert r.status_code == 404 and r.get_json()["code"] == "no_data"


def test_stale_cache_is_served_and_labelled_when_google_fails(client):
    assert client.post("/analyze", json={"keyword": "gorpcore"}).status_code == 200
    # age the saved copy past the freshness window, then break Google
    entry = A.cache_read("trend:gorpcore")
    entry["saved_at"] = (A._now() - datetime.timedelta(days=2)).isoformat()
    A.cache_write("trend:gorpcore", {k: v for k, v in entry.items() if k != "_source"})
    FakeTrends.mode = "429"
    r = client.post("/analyze", json={"keyword": "gorpcore"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["stale"] is True and body["status"] in A.analyze_series.__globals__["DISPLAY"]


def test_demo_snapshot_is_the_last_resort(client, tmp_path):
    FakeTrends.mode = "ok"
    entry = A._series_entry(A._fetch_frame(["coquette"]), ["coquette"])
    A.cache_write("trend:coquette", entry, folder=A.DEMO_DIR)
    FakeTrends.mode = "429"
    body = client.post("/analyze", json={"keyword": "coquette"}).get_json()
    assert body["stale"] is True and body["data_source"] == "demo"


def test_fresh_cache_does_not_call_google_again(client):
    client.post("/analyze", json={"keyword": "streetwear"})
    n = FakeTrends.calls
    client.post("/analyze", json={"keyword": "Streetwear"})      # different case, same key
    assert FakeTrends.calls == n


# ── [new] partial newest week is dropped ───────────────────────────────────────
def test_partial_week_is_dropped(client):
    body = client.post("/analyze", json={"keyword": "minimalist"}).get_json()
    labels = body["chart_data"]["labels"]
    assert len(labels) == 260                                    # 261 rows minus the partial one
    assert labels[-1] == "Oct 11, 2026"                          # not Oct 18


# ── [new] no placeholder article, honest warning ───────────────────────────────
def test_news_off_is_a_warning_not_a_fake_article(client):
    body = client.post("/analyze", json={"keyword": "cottagecore"}).get_json()
    assert body["news"] == [] and body["news_total"] is None
    assert any("news is off" in w for w in body["warnings"])


# ── [new] compare ──────────────────────────────────────────────────────────────
def test_compare_two_terms(client):
    r = client.post("/api/compare", json={"keywords": ["y2k fashion", "streetwear"]})
    assert r.status_code == 200
    body = r.get_json()
    assert set(body["series"]) == {"y2k fashion", "streetwear"}
    assert len(body["items"]) == 2 and body["headline"]


def test_compare_needs_two_terms(client):
    assert client.post("/api/compare", json={"keywords": ["only one"]}).status_code == 400


def test_compare_page_exists(client):
    assert client.get("/compare").status_code == 200


# ── [new] trend of the day is stable all day and degrades honestly ────────────
def test_trend_of_day_is_deterministic():
    d = datetime.date(2026, 10, 24)
    assert A.pick_today(d)[:2] == A.pick_today(d)[:2]
    assert A.pick_today(d)[0] != A.pick_today(d)[1]


def test_today_data_reports_unavailable_instead_of_stable(client):
    FakeTrends.mode = "429"
    body = client.get("/api/today-data").get_json()
    assert body["status"] == "unavailable"
    assert body["fun_fact"] and body["history"]                  # rest of the page still works


# ── [new] validation ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw", ["café", "y2k.", "balletcore/coquette", "quiet luxury", "kimono 着物"])
def test_validate_accepts(kw):
    assert A.validate(kw)[1] is None


@pytest.mark.parametrize("kw", ["", "a", "x" * 81, "drop;table", "a+b", None, 123])
def test_validate_rejects(kw):
    assert A.validate(kw)[0] is None


def test_validate_strips_html():
    assert A.validate("<b>gorpcore</b>")[0] == "gorpcore"


# ── static files resolve from the repo root ──────────────────────
def test_static_folder_is_at_repo_root():
    assert os.path.basename(A.app.static_folder) == "static"
    assert os.path.dirname(A.app.static_folder) == A.ROOT