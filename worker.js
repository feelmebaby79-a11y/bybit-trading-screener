export default {
  async fetch(request) {
    const url = new URL(request.url);
    const symbol = (url.searchParams.get("symbol") || "BTCUSDT").toUpperCase();

    if (!/^[A-Z0-9]{3,20}USDT$/.test(symbol)) {
      return Response.json({ ok: false, error: "Invalid symbol" }, { status: 400 });
    }

    const timeframes = {
      "1D": "D",
      "4H": "240",
      "1H": "60",
      "15m": "15",
      "5m": "5"
    };

    try {
      const entries = await Promise.all(
        Object.entries(timeframes).map(async ([name, interval]) => {
          const apiUrl =
            "https://api.bybit.com/v5/market/kline" +
            "?category=linear" +
            "&symbol=" + encodeURIComponent(symbol) +
            "&interval=" + interval +
            "&limit=200";

          const response = await fetch(apiUrl);

          if (!response.ok) {
            throw new Error(`Bybit ${name} HTTP ${response.status}`);
          }

          const data = await response.json();

          if (data.retCode !== 0) {
            throw new Error(`Bybit ${name}: ${data.retMsg}`);
          }

          return [name, data.result.list];
        })
      );

      return Response.json({
        ok: true,
        symbol,
        source: "Bybit V5",
        timeframes: Object.fromEntries(entries)
      });

    } catch (error) {
      return Response.json(
        {
          ok: false,
          symbol,
          error: error.message
        },
        { status: 500 }
      );
    }
  }
};
