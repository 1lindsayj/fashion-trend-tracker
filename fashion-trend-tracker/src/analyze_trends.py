import pandas as pd

def calculate_growth(df):
    # avg for the current
    recent = df.tail(13).mean().values[0] 
    # avg for last 3 months
    past = df.head(13).mean().values[0]

    if past == 0:
        return 0
    
    growth = (recent - past) / past
    return growth

# determine trend status based on growth percentage
def get_trend_status(growth):
    if growth >= 0.15:
        return "rising"
    elif growth <= -0.10:
        return "fading"
    else:
        return "stable for now"