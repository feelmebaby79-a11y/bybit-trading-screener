const BYBIT_BASE = "https://api.bybit.com";

// ======================================================
// GitHub repository
// ======================================================

const GITHUB_OWNER = "feelmebaby79-a11y";

const GITHUB_REPO =
  "bybit-trading-screener";

const GITHUB_BRANCH = "main";

// ======================================================
// Allowed Bybit proxy endpoints
// ======================================================

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

// ======================================================
// JSON response helper
// ======================================================

function json(
  data,
  status = 200,
  extraHeaders = {}
) {
  return new Response(
    JSON.stringify(data),
    {
      status,

      headers: {
        "content-type":
          "application/json;charset=UTF-8",

        "cache-control":
          "no-store",

        "access-control-allow-origin":
          "*",

        ...extraHeaders,
      },
    }
  );
}

// ======================================================
// Symbol validation
// ======================================================

function validSymbol(symbol) {
  return /^[A-Z0-9]{3,30}USDT$/.test(
    symbol
  );
}

// ======================================================
// CSV parser
// ======================================================

function csvLine(line) {
  const values = [];

  let current = "";
  let quoted = false;

  for (
    let i = 0;
    i < line.length;
    i++
  ) {
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

  const headers =
    csvLine(lines[0]);

  return lines
    .slice(1)
    .map((line) => {
      const values =
        csvLine(line);

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
            value =
              Number(value);
          }

          row[header] = value;
        }
      );

      return row;
    });
}

// ======================================================
// GitHub CSV fetch
// ======================================================

async function fetchGitHubCSV(
  filename
) {
  const url =
    `https://raw.githubusercontent.com/` +
    `${GITHUB_OWNER}/${GITHUB_REPO}/` +
    `${GITHUB_BRANCH}/latest/` +
    `${filename}`;

  const response =
    await fetch(
      url,
      {
        headers: {
          accept:
            "text/plain",

          "user-agent":
            "bybit-trading-screener-worker",
        },
      }
    );

  if (!response.ok) {
    throw new Error(
      `GitHub ${filename} ` +
      `HTTP ${response.status}`
    );
  }

  return parseCSV(
    await response.text()
  );
}

// ======================================================
// GitHub scan.json fetch
//
// /scan now reads the complete scan.json,
// including positions.BTCUSDT / COMPUSDT.
// ======================================================

async function fetchGitHubJSON(
  filename
) {
  const url =
    `https://raw.githubusercontent.com/` +
    `${GITHUB_OWNER}/${GITHUB_REPO}/` +
    `${GITHUB_BRANCH}/latest/` +
    `${filename}`;

  const response =
    await fetch(
      url,
      {
        headers: {
          accept:
            "application/json",

          "user-agent":
            "bybit-trading-screener-worker",
        },
      }
    );

  if (!response.ok) {
    throw new Error(
      `GitHub ${filename} ` +
      `HTTP ${response.status}`
    );
  }

  return await response.json();
}

// ======================================================
// Direct Bybit kline fetch
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

  const response =
    await fetch(
      apiUrl.toString(),
      {
        headers: {
          accept:
            "application/json",
        },
      }
    );

  if (!response.ok) {
    throw new Error(
      `Bybit HTTP ` +
      `${response.status}`
    );
  }

  const data =
    await response.json();

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

  async fetch(
    request,
    env
  ) {
    try {

      if (
        request.method !== "GET"
      ) {
        return json(
          {
            ok: false,
            error:
              "GET requests only",
          },
          405
        );
      }

      const incoming =
        new URL(request.url);

      // =================================================
      // 1. Latest complete market scan
      //
      // /scan
      //
      // Reads latest/scan.json.
      //
      // Includes:
      // - generated_at
      // - universe_count
      // - positions
      // - longs
      // - shorts
      // =================================================

      if (
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
            new Date()
              .toISOString(),

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
      // 2. Run GitHub Actions screener on demand
      //
      // /run-scan
      // =================================================

      if (
        incoming.pathname ===
        "/run-scan"
      ) {

        if (
          !env.GITHUB_TOKEN
        ) {
          return json(
            {
              ok: false,

              error:
                "GITHUB_TOKEN secret " +
                "is missing",
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
                  `Bearer ` +
                  `${env.GITHUB_TOKEN}`,

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

          status:
            "started",

          message:
            "Latest Bybit HTF/LTF " +
            "scan started",

          repository:
            `${GITHUB_OWNER}/` +
            `${GITHUB_REPO}`,

          workflow:
            "bybit-scan.yml",

          startedAt:
            new Date()
              .toISOString(),
        });
      }

      // =================================================
      // 3. Individual coin raw 5TF data
      //
      // /?symbol=BTCUSDT
      // /?symbol=COMPUSDT
      //
      // Returns:
      // 1D
      // 4H
      // 1H
      // 15m
      // 5m
      //
      // 200 candles each
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

        if (
          !validSymbol(symbol)
        ) {
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
            Object
              .entries(
                timeframes
              )
              .map(
                async (
                  [
                    name,
                    interval
                  ]
                ) => {

                  const candles =
                    await fetchKlines(
                      symbol,
                      interval,
                      200
                    );

                  return [
                    name,
                    candles
                  ];
                }
              )
          );

        return json({
          ok: true,

          symbol,

          source:
            "Bybit V5",

          fetchedAt:
            new Date()
              .toISOString(),

          timeframes:
            Object.fromEntries(
              entries
            ),
        });
      }

      // =================================================
      // 4. Restricted Bybit public-market proxy
      //
      // Used by GitHub Actions screener
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
        const [
          key,
          value
        ]
        of incoming.searchParams
      ) {
        if (
          ALLOWED_PARAMS.has(
            key
          )
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
              "application/json;" +
              "charset=UTF-8",

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
