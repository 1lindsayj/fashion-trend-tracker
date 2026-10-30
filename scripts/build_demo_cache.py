"""
scripts/build_demo_cache.py  
saves google trends data for a few seed trends into demo_cache/ so the app still works
(and is clearly labelled "saved sample") when google hits rate-limits or a reviewer has no network.

should run it ONCE from the repo root while google is answering, then commit the demo_cache/ folder:
    python scripts/build_demo_cache.py
it is slow on purpose: it waits between requests so google doesn't block it.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app as A  # noqa: E402

SEEDS = sorted(set(A.TRENDS_OF_DAY + ["y2k fashion", "streetwear", "balletcore"]))
COMPARES = [["streetwear", "y2k fashion", "balletcore"]]          # the comparison from my work sample
PAUSE, RETRIES = 10, 3


def grab(label, kws, key):
    for attempt in range(1, RETRIES + 1):
        try:
            entry = A._series_entry(A._fetch_frame(kws), kws)
            A.cache_write(key, entry, folder=A.DEMO_DIR)
            print(f"  saved  {label}")
            return True
        except A.NoData:
            print(f"  no data for {label}, skipping")
            return False
        except A.TrendsUnavailable as e:
            wait = 60 * attempt
            print(f"  google said no ({e}); waiting {wait}s (attempt {attempt}/{RETRIES})")
            time.sleep(wait)
    print(f"  GAVE UP on {label}. Run the script again later.")
    return False


if __name__ == "__main__":
    ok = 0
    for kw in SEEDS:
        ok += grab(kw, [kw], "trend:" + kw)
        time.sleep(PAUSE)
    for kws in COMPARES:
        key = "compare:" + "|".join(sorted(k.lower() for k in kws))
        ok += grab(" vs ".join(kws), kws, key)
        time.sleep(PAUSE)
    print(f"\n{ok} snapshot(s) written to {A.DEMO_DIR}. Commit that folder.")