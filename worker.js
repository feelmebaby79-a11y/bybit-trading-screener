const BYBIT_BASE = "https://api.bybit.com";

const GITHUB_OWNER = "feelmebaby79-a11y";
const GITHUB_REPO = "bybit-trading-screener";
const GITHUB_BRANCH = "main";

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

function json(data, status = 200, extraHeaders = {}) {
  return new Response(
    JSON.stringify(data),
    {
      status,
      headers: {
        "content-type": "application/json;charset=UTF-8",
        "cache-control": "no-store",
        "access-control-allow-origin": "*",
        ...extraHeaders,
      },
    }
  );
}

function validSymbol(symbol) {
  return /^[A-Z0-9]{3,30}USDT$/.test(symbol);
}

// ======================================================
// Bybit Private API authentication
// ======================================================

async function hmacSha256Hex(secret, message) {
  const encoder = new TextEncoder();

  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    {
      name: "HMAC",
      hash: "SHA-256",
    },
    false,
    ["sign"]
  );

  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(message)
  );

  return Array.from(new Uint8Array(signature))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchOpenPositions(env) {
  const apiKey = String(env.BYBIT_API_KEY || "").trim();
  const apiSecret = String(env.BYBIT_API_SECRET || "").trim();

  if (!apiKey || !apiSecret) {
    throw new Error(
      "BYBIT_API_KEY or BYBIT_API_SECRET is missing"
    );
  }

  const timestamp = Date.now().toString();
  const recvWindow = "5000";

  // IMPORTANT:
  // The exact same query string is used for signing and request URL.
  const queryString = "category=linear&settleCoin=USDT";

  const signPayload =
    timestamp +
    apiKey +
    recvWindow +
    queryString;

  const signature = await hmacSha256Hex(
    apiSecret,
    signPayload
  );

  const url =
    BYBIT_BASE +
    "/v5/position/list?" +
    queryString;

  const response = await fetch(
    url,
    {
      headers: {
        "X-BAPI-API-KEY": apiKey,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": recvWindow,
        "Accept": "application/json",
      },
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      `Bybit HTTP ${response.status}`
    );
  }

  if (data.retCode !== 0) {
    throw new Error(
      `Bybit ${data.retCode}: ${data.retMsg}`
    );
  }

  return (data.result?.list || [])
    .filter(
      (position) =>
        Number(position.size) > 0
    )
    .map(
      (position) => ({
        symbol: position.symbol,
        side: position.side,
        size: position.size,
        avgPrice: position.avgPrice,
        markPrice: position.markPrice,
        leverage: position.leverage,
        positionValue: position.positionValue,
        unrealisedPnl: position.unrealisedPnl,
        liqPrice: position.liqPrice,
      })
    );
}

// ======================================================
// GitHub token authentication
//
// Used for /position-symbols so account position symbols
// are not exposed through an unauthenticated public URL.
// GitHub Actions will send:
// Authorization: Bearer <GITHUB_TOKEN>
// ======================================================

function isAuthorizedGitHubRequest(request, env) {
  if (!env.GITHUB_TOKEN) {
    return false;
  }

  const authorization =
    request.headers.get("Authorization") || "";

  if (!authorization.startsWith("Bearer ")) {
    return false;
  }

  const suppliedToken =
    authorization.slice(7).trim();

  const expectedToken =
    String(env.GITHUB_TOKEN).trim();

  return (
    suppliedToken.length > 0 &&
    suppliedToken === expectedToken
  );
}

// ======================================================
// CSV parser
// ======================================================

function csvLine(line) {
  const values = [];

  let current = "";
  let quoted = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];

    if (char === '"') {
      if (
        quoted &&
        line[i + 1] === '"'
      ) {
        current += '"';
        i++;
      } else {
        quoted = !quoted;
      }
    } else if (
      char === "," &&
      !quoted
    ) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }

  values.push(current);

  return values;
}

function parseCSV(text) {
  const lines = text
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter(
      (line) =>
        line.trim() !== ""
    );

  if (lines.length < 2) {
    return [];
  }

  const headers = csvLine(lines[0]);

  return lines
    .slice(1)
    .map(
      (line) => {
        const values = csvLine(line);

        const row = {};

        headers.forEach(
          (header, index) => {
            let value =
              values[index] ?? "";

            if (value === "True") {
              value = true;
            } else if (
              value === "False"
            ) {
              value = false;
            } else if (
              value !== "" &&
              !Number.isNaN(
                Number(value)
              )
            ) {
              value = Number(value);
            }

            row[header] = value;
          }
        );

        return row;
      }
    );
}

async function fetchGitHubCSV(filename) {
  const url =
    `https://raw.githubusercontent.com/` +
    `${GITHUB_OWNER}/${GITHUB_REPO}/` +
    `${GITHUB_BRANCH}/latest/` +
    `${filename}`;

  const response = await fetch(
    url,
    {
      headers: {
        accept: "text/plain",
        "user-agent":
          "bybit-trading-screener-worker",
      },
    }
  );

  if (!response.ok) {
    throw new Error(
      `GitHub ${filename} HTTP ${response.status}`
    );
  }

  return parseCSV(
    await response.text()
  );
}

async function fetchGitHubJSON(filename) {
  const url =
    `https://raw.githubusercontent.com/` +
    `${GITHUB_OWNER}/${GITHUB_REPO}/` +
    `${GITHUB_BRANCH}/latest/` +
    `${filename}`;

  const response = await fetch(
    url,
    {
      headers: {
        accept: "application/json",
        "user-agent":
          "bybit-trading-screener-worker",
      },
    }
  );

  if (!response.ok) {
    throw new Error(
      `GitHub ${filename} HTTP ${response.status}`
    );
  }

  return await response.json();
}

// ======================================================
// Public Bybit kline
// ======================================================

async function fetchKlines(
  symbol,
  interval,
  limit = 200
) {
  const apiUrl =
    new URL(
      BYBIT_BASE +
      "/v5/market/kline"
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
    String(limit)
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
      `Bybit HTTP ${response.status}`
    );
  }

  const data = await response.json();

  if (data.retCode !== 0) {
    throw new Error(
      `Bybit: ${data.retMsg}`
    );
  }

  return data.result.list;
}

// ======================================================
// Worker
// ======================================================

export default {
  async fetch(request, env) {
    try {
      const incoming =
        new URL(request.url);

      // =================================================
      // TEST ONLY:
      // actual Bybit open positions
      //
      // /test-positions
      //
      // Kept temporarily because it has already been
      // verified successfully.
      // =================================================

      if (
        request.method === "GET" &&
        incoming.pathname ===
          "/test-positions"
      ) {
        const positions =
          await fetchOpenPositions(env);

        return json({
          ok: true,
          test:
            "Bybit Private Position API",
          positionCount:
            positions.length,
          positions,
        });
      }

      // =================================================
      // PRIVATE:
      // current position symbols for GitHub Actions
      //
      // GET /position-symbols
      // Authorization: Bearer GITHUB_TOKEN
      //
      // Only symbols are returned.
      // No size / avg price / PnL / liquidation price.
      // =================================================

      if (
        request.method === "GET" &&
        incoming.pathname ===
          "/position-symbols"
      ) {
        if (
          !isAuthorizedGitHubRequest(
            request,
            env
          )
        ) {
          return json(
            {
              ok: false,
              error: "Unauthorized",
            },
            401
          );
        }

        const positions =
          await fetchOpenPositions(env);

        const symbols =
          [
            ...new Set(
              positions
                .map(
                  (position) =>
                    position.symbol
                )
                .filter(validSymbol)
            ),
          ].sort();

        return json({
          ok: true,
          source:
            "Bybit Private Position API",
          fetchedAt:
            new Date().toISOString(),
          positionCount:
            symbols.length,
          symbols,
        });
      }

      // =================================================
      // Latest scan
      //
      // /scan
      // =================================================

      if (
        request.method === "GET" &&
        incoming.pathname ===
          "/scan"
      ) {
        const scan =
          await fetchGitHubJSON(
            "scan.json"
          );

        return json({
          ...scan,
          repository:
            `${GITHUB_OWNER}/` +
            `${GITHUB_REPO}`,
          servedAt:
            new Date().toISOString(),
          longCount:
            Array.isArray(
              scan.longs
            )
              ? scan.longs.length
              : 0,
          shortCount:
            Array.isArray(
              scan.shorts
            )
              ? scan.shorts.length
              : 0,
        });
      }

      // =================================================
      // Run GitHub Actions scan
      //
      // /run-scan
      // =================================================

      if (
        request.method === "GET" &&
        incoming.pathname ===
          "/run-scan"
      ) {
        if (!env.GITHUB_TOKEN) {
          return json(
            {
              ok: false,
              error:
                "GITHUB_TOKEN secret is missing",
            },
            500
          );
        }

        const workflowUrl =
          `https://api.github.com/repos/` +
          `${GITHUB_OWNER}/` +
          `${GITHUB_REPO}` +
          `/actions/workflows/` +
          `bybit-scan.yml/dispatches`;

        const response =
          await fetch(
            workflowUrl,
            {
              method: "POST",

              headers: {
                "Accept":
                  "application/vnd.github+json",

                "Authorization":
                  `Bearer ${env.GITHUB_TOKEN}`,

                "X-GitHub-Api-Version":
                  "2022-11-28",

                "User-Agent":
                  "bybit-trading-screener",
              },

              body:
                JSON.stringify({
                  ref:
                    GITHUB_BRANCH,
                }),
            }
          );

        if (!response.ok) {
          const errorText =
            await response.text();

          return json(
            {
              ok: false,
              status:
                response.status,
              error:
                errorText,
            },
            response.status
          );
        }

        return json({
          ok: true,
          status: "started",
          message:
            "Latest Bybit HTF/LTF scan started",
          repository:
            `${GITHUB_OWNER}/` +
            `${GITHUB_REPO}`,
          workflow:
            "bybit-scan.yml",
          startedAt:
            new Date().toISOString(),
        });
      }

      // =================================================
      // From here: GET requests only
      // =================================================

      if (request.method !== "GET") {
        return json(
          {
            ok: false,
            error:
              "GET requests only",
          },
          405
        );
      }

      // =================================================
      // Individual symbol 5TF raw candles
      //
      // /?symbol=BTCUSDT
      // =================================================

      if (
        incoming.pathname === "/"
      ) {
        const symbol =
          (
            incoming
              .searchParams
              .get("symbol") ||
            "BTCUSDT"
          ).toUpperCase();

        if (!validSymbol(symbol)) {
          return json(
            {
              ok: false,
              error:
                "Invalid symbol",
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

        const entries =
          await Promise.all(
            Object.entries(
              timeframes
            ).map(
              async (
                [name, interval]
              ) => {
                const candles =
                  await fetchKlines(
                    symbol,
                    interval,
                    200
                  );

                return [
                  name,
                  candles,
                ];
              }
            )
          );

        return json({
          ok: true,
          symbol,
          source: "Bybit V5",
          fetchedAt:
            new Date().toISOString(),
          timeframes:
            Object.fromEntries(
              entries
            ),
        });
      }

      // =================================================
      // Restricted public Bybit proxy
      // =================================================

      if (
        !ALLOWED_PATHS.has(
          incoming.pathname
        )
      ) {
        return json(
          {
            retCode: 10001,
            retMsg:
              "Path not allowed",
          },
          403
        );
      }

      const upstream =
        new URL(
          BYBIT_BASE +
          incoming.pathname
        );

      for (
        const [key, value]
        of incoming.searchParams
      ) {
        if (
          ALLOWED_PARAMS.has(key)
        ) {
          upstream
            .searchParams
            .append(
              key,
              value
            );
        }
      }

      if (
        incoming.pathname !==
          "/v5/market/time" &&
        !upstream
          .searchParams
          .has("category")
      ) {
        upstream
          .searchParams
          .set(
            "category",
            "linear"
          );
      }

      const response =
        await fetch(
          upstream.toString(),
          {
            headers: {
              accept:
                "application/json",
            },
          }
        );

      const text =
        await response.text();

      return new Response(
        text,
        {
          status:
            response.status,
          headers: {
            "content-type":
              response.headers.get(
                "content-type"
              ) ||
              "application/json;charset=UTF-8",
            "cache-control":
              "no-store",
            "access-control-allow-origin":
              "*",
          },
        }
      );

    } catch (error) {
      return json(
        {
          ok: false,
          error:
            error?.message ||
            "Worker error",
        },
        500
      );
    }
  },
};
