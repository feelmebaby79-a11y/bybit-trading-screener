const BYBIT_BASE = "https://api.bybit.com";

const ALLOWED_PATHS = new Set([
  "/v5/market/time",
  "/v5/market/kline",
  "/v5/market/tickers",
  "/v5/market/instruments-info",
]);

const ALLOWED_PARAMS = new Set([
  "category",
  "symbol",
  "interval",
  "limit",
  "cursor",
]);

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json;charset=UTF-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*",
    },
  });
}

export default {
  async fetch(request) {
    try {
      if (request.method !== "GET") {
        return json(
          {
            retCode: 10001,
            retMsg: "GET requests only",
          },
          405
        );
      }

      const incoming = new URL(request.url);

      // -------------------------------------------------
      // 1) Convenient chart endpoint
      // /?symbol=BTCUSDT
      // -------------------------------------------------
      if (incoming.pathname === "/") {
        const symbol = (
          incoming.searchParams.get("symbol") || "BTCUSDT"
        ).toUpperCase();

        if (!/^[A-Z0-9]{3,30}USDT$/.test(symbol)) {
          return json(
            {
              ok: false,
              error: "Invalid symbol",
            },
            400
          );
        }

        const timeframes = {
          "1D": "D",
          "4H": "240",
          "1H": "60",
          "15m": "15",
          "5m": "5",
        };

        const entries = await Promise.all(
          Object.entries(timeframes).map(
            async ([name, interval]) => {
              const apiUrl = new URL(
                BYBIT_BASE + "/v5/market/kline"
              );

              apiUrl.searchParams.set(
                "category",
                "linear"
              );

              apiUrl.searchParams.set(
                "symbol",
                symbol
              );

              apiUrl.searchParams.set(
                "interval",
                interval
              );

              apiUrl.searchParams.set(
                "limit",
                "200"
              );

              const response = await fetch(
                apiUrl.toString(),
                {
                  headers: {
                    accept: "application/json",
                  },
                }
              );

              if (!response.ok) {
                throw new Error(
                  `Bybit ${name} HTTP ${response.status}`
                );
              }

              const data = await response.json();

              if (data.retCode !== 0) {
                throw new Error(
                  `Bybit ${name}: ${data.retMsg}`
                );
              }

              return [name, data.result.list];
            }
          )
        );

        return json({
          ok: true,
          symbol,
          source: "Bybit V5",
          timeframes: Object.fromEntries(entries),
        });
      }

      // -------------------------------------------------
      // 2) Restricted Bybit market-data proxy
      // -------------------------------------------------
      if (!ALLOWED_PATHS.has(incoming.pathname)) {
        return json(
          {
            retCode: 10001,
            retMsg: "Path not allowed",
          },
          403
        );
      }

      const upstream = new URL(
        BYBIT_BASE + incoming.pathname
      );

      for (const [key, value] of incoming.searchParams) {
        if (ALLOWED_PARAMS.has(key)) {
          upstream.searchParams.append(
            key,
            value
          );
        }
      }

      // Force public linear market data when relevant
      if (
        incoming.pathname !== "/v5/market/time" &&
        !upstream.searchParams.has("category")
      ) {
        upstream.searchParams.set(
          "category",
          "linear"
        );
      }

      const response = await fetch(
        upstream.toString(),
        {
          headers: {
            accept: "application/json",
          },
        }
      );

      const text = await response.text();

      return new Response(text, {
        status: response.status,
        headers: {
          "content-type":
            response.headers.get("content-type") ||
            "application/json;charset=UTF-8",
          "cache-control": "no-store",
          "access-control-allow-origin": "*",
        },
      });
    } catch (error) {
      return json(
        {
          retCode: 10000,
          retMsg: error?.message || "Worker error",
        },
        500
      );
    }
  },
};
