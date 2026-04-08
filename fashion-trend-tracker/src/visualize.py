import matplotlib.pyplot as plt

# to plot trend of the keyword over time
def plot_trend(df, keyword):
    if df is None or df.empty:
        return
     
    plt.figure()
    plt.plot(df.index, df[keyword])
    plt.title(f"trend over time: {keyword}")
    plt.xlabel("date")
    plt.ylabel("interest")
    plt.show()