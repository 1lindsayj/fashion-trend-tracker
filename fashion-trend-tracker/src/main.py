from fetch_trends import fetch_trend
from analyze_trends import calculate_growth, get_trend_status
from visualize import plot_trend
import pandas as pd
import matplotlib.pyplot as plt

trend_keywords = ["streetwear", "y2k fashion", "balletcore"]

results = {}

for keyword in trend_keywords:
    df = fetch_trend(keyword)

    if df is None:
        continue

    growth = calculate_growth(df)
    status = get_trend_status(growth)

    results[keyword] = {
        "data" : df ,
        "growth" : round(growth, 2),
        "status" : status
    }

    print(f"{keyword}: {status} ({round(growth, 2)}%)")
    plot_trend(df, keyword)

if results:
    combined = pd.concat(
        [info["data"] for info in results.values()],
        axis = 1
    )
    combined.columns = results.keys()

    combined.plot()
    plt.title("fashion trends overview")
    plt.xlabel("date")
    plt.ylabel("interest")
    plt.tight_layout()
    plt.show()