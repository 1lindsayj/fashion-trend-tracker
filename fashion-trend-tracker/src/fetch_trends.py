# this part of the code will fetch trending data from external apis
from pytrends.request import TrendReq
import pandas as pd

pyTrends = TrendReq(hl="en-US", tz=360)

# this function pulls trend data for a given keyword
def fetch_trend(keyword, timeframe="today 12-m"):
    try:
        pyTrends.build_payload([keyword], timeframe=timeframe)
        data = pyTrends.interest_over_time()

        if data.empty:
            print(f"no data found for {keyword}")
            return None
        
        data = data.drop(columns=["isPartial"])
        return data

    except Exception as e:
        print(f"error fetching data for {keyword}: {e}")
        return None
