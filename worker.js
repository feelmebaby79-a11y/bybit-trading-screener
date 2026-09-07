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
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json;charset=UTF-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*",
      ...extraHeaders,
    },
  });
}

function validSymbol(symbol) {
  return /^[A-Z0-9]{3,30}USDT$/.test(symbol);
}

async function hmacSha256Hex(secret, message) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
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
    throw new Error("BYBIT_API_KEY or BYBIT_API_SECRET is missing");
  }

  const timestamp = Date.now().toString();
  const recvWindow = "5000";
  const queryString = "category=linear&settleCoin=USDT";
  const signPayload = timestamp + apiKey + recvWindow + queryString;
  const signature = await hmacSha256Hex(apiSecret, signPayload);
  const url = BYBIT_BASE + "/v5/position/list?" + queryString;

  const response = await fetch(url, {
    headers: {
      "X-BAPI-API-KEY": apiKey,
      "X-BAPI-TIMESTAMP": timestamp,
      "X-BAPI-SIGN": signature,
      "X-BAPI-RECV-WINDOW": recvWindow,
      "Accept": "application/json",
    },
  });

  const data = await response.json();

  if (!response.ok) throw new Error(`Bybit HTTP ${response.status}`);
  if (data.retCode !== 0) throw new Error(`Bybit ${data.retCode}: ${data.retMsg}`);

  return (data.result?.list || [])
    .filter((position) => Number(position.size) > 0)
    .map((position) => ({
      symbol: position.symbol,
      side: position.side,
      size: position.size,
      avgPrice: position.avgPrice,
      markPrice: position.markPrice,
      leverage: position.leverage,
      positionValue: position.positionValue,
      unrealisedPnl: position.unrealisedPnl,
      liqPrice: position.liqPrice,
    }));
}

function isAuthorizedWorkerRequest(request, env) {
  const expectedKey = String(env.WORKER_ACCESS_KEY || "").trim();
  if (!expectedKey) return false;
  const authorization = request.headers.get("Authorization") || "";
  if (!authorization.startsWith("Bearer ")) return false;
  const suppliedKey = authorization.slice(7).trim();
  return suppliedKey.length > 0 && suppliedKey === expectedKey;
}

async function fetchGitHubJSON(filename) {
  const url = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/latest/${filename}`;
  const response = await fetch(url, {
    headers: {
      accept: "application/json",
      "user-agent": "bybit-trading-screener-worker",
    },
  });
  if (!response.ok) throw new Error(`GitHub ${filename} HTTP ${response.status}`);
  return await response.json();
}

async function fetchKlines(symbol, interval, limit = 200) {
  const apiUrl = new URL(BYBIT_BASE + "/v5/market/kline");
  apiUrl.searchParams.set("category", "linear");
  apiUrl.searchParams.set("symbol", symbol);
  apiUrl.searchParams.set("interval", interval);
  apiUrl.searchParams.set("limit", String(limit));

  const response = await fetch(apiUrl.toString(), {
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Bybit HTTP ${response.status}`);
  const data = await response.json();
  if (data.retCode !== 0) throw new Error(`Bybit: ${data.retMsg}`);
  return data.result.list;
}

async function fetchLastPrice(symbol) {
  const apiUrl = new URL(BYBIT_BASE + "/v5/market/tickers");
  apiUrl.searchParams.set("category", "linear");
  apiUrl.searchParams.set("symbol", symbol);
  const response = await fetch(apiUrl.toString(), { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`Bybit HTTP ${response.status}`);
  const data = await response.json();
  if (data.retCode !== 0) throw new Error(`Bybit: ${data.retMsg}`);
  const item = data.result?.list?.[0];
  const last = Number(item?.lastPrice);
  if (!Number.isFinite(last)) throw new Error(`Invalid ticker for ${symbol}`);
  return last;
}

function normalizeWatchItem(item) {
  const symbol = String(item?.symbol || "").trim().toUpperCase();
  const direction = String(item?.direction || "").trim().toUpperCase();
  const status = String(item?.status || "ACTIVE").trim().toUpperCase();
  const poiLow = Number(item?.poi_low);
  const poiHigh = Number(item?.poi_high);
  const score = Number(item?.score);

  return {
    symbol,
    direction,
    score: Number.isFinite(score) ? score : null,
    poi_low: Number.isFinite(poiLow) ? poiLow : null,
    poi_high: Number.isFinite(poiHigh) ? poiHigh : null,
    status,
    trigger_model: item?.trigger_model || null,
    note: item?.note || null,
    updated_at: item?.updated_at || null,
  };
}

async function evaluateWatchlist() {
  const payload = await fetchGitHubJSON("watchlist.json");
  const items = Array.isArray(payload?.items) ? payload.items.map(normalizeWatchItem) : [];
  const results = [];

  for (const item of items) {
    if (!validSymbol(item.symbol)) {
      results.push({ ...item, ok: false, error: "Invalid symbol" });
      continue;
    }
    if (item.status !== "ACTIVE") {
      results.push({ ...item, ok: true, monitoring: false, reason: `status=${item.status}` });
      continue;
    }
    if (item.poi_low === null || item.poi_high === null) {
      results.push({ ...item, ok: true, monitoring: false, reason: "POI not defined" });
      continue;
    }

    try {
      const last = await fetchLastPrice(item.symbol);
      const low = Math.min(item.poi_low, item.poi_high);
      const high = Math.max(item.poi_low, item.poi_high);
      const inPoi = last >= low && last <= high;
      results.push({
        ...item,
        ok: true,
        monitoring: true,
        last,
        in_poi: inPoi,
        checked_at: new Date().toISOString(),
      });
    } catch (error) {
      results.push({ ...item, ok: false, monitoring: true, error: error?.message || String(error) });
    }
  }

  return {
    ok: true,
    generated_at: new Date().toISOString(),
    watchlist_updated_at: payload?.updated_at || null,
    count: results.length,
    poi_hits: results.filter((x) => x.in_poi === true),
    items: results,
  };
}

// Diagnostic only.
async function probeBybit(host, path) {
  const probeId = crypto.randomUUID();
  const url = new URL(host + path);
  if (path === "/v5/market/kline") {
    url.searchParams.set("category", "linear");
    url.searchParams.set("symbol", "BTCUSDT");
    url.searchParams.set("interval", "5");
    url.searchParams.set("limit", "1");
  }
  const started = Date.now();
  try {
    const response = await fetch(url.toString(), {
      headers: {
        accept: "application/json",
        "cdn-request-id": probeId,
        "user-agent": "bybit-trading-screener-diagnostic/1.0",
      },
    });
    const body = await response.text();
    return {
      host, path, status: response.status, ok: response.ok,
      elapsedMs: Date.now() - started,
      contentType: response.headers.get("content-type"),
      server: response.headers.get("server"),
      cfRay: response.headers.get("cf-ray"),
      retryAfter: response.headers.get("retry-after"),
      probeId, bodyPrefix: body.slice(0, 500),
    };
  } catch (error) {
    return { host, path, status: null, ok: false, elapsedMs: Date.now() - started, probeId, fetchError: error?.message || String(error) };
  }
}

export default {
  async fetch(request, env) {
    try {
      const incoming = new URL(request.url);

      if (request.method === "GET" && incoming.pathname === "/diagnose-bybit") {
        const hosts = ["https://api.bybit.com", "https://api.bytick.com"];
        const paths = ["/v5/market/time", "/v5/market/kline"];
        const probes = await Promise.all(hosts.flatMap((host) => paths.map((path) => probeBybit(host, path))));
        return json({
          ok: true,
          diagnosticOnly: true,
          productionBybitBase: BYBIT_BASE,
          generatedAt: new Date().toISOString(),
          worker: {
            colo: request.cf?.colo || null,
            country: request.cf?.country || null,
            region: request.cf?.region || null,
            city: request.cf?.city || null,
            timezone: request.cf?.timezone || null,
          },
          probes,
        });
      }

      if (request.method === "GET" && incoming.pathname === "/test-positions") {
        const positions = await fetchOpenPositions(env);
        return json({ ok: true, test: "Bybit Private Position API", positionCount: positions.length, positions });
      }

      if (request.method === "GET" && incoming.pathname === "/position-symbols") {
        if (!isAuthorizedWorkerRequest(request, env)) return json({ ok: false, error: "Unauthorized" }, 401);
        const positions = await fetchOpenPositions(env);
        const symbols = [...new Set(positions.map((position) => position.symbol).filter(validSymbol))].sort();
        return json({ ok: true, source: "Bybit Private Position API", fetchedAt: new Date().toISOString(), positionCount: symbols.length, symbols });
      }

      if (request.method === "GET" && incoming.pathname === "/scan") {
        const scan = await fetchGitHubJSON("scan.json");
        return json({
          ...scan,
          repository: `${GITHUB_OWNER}/${GITHUB_REPO}`,
          servedAt: new Date().toISOString(),
          longCount: Array.isArray(scan.longs) ? scan.longs.length : 0,
          shortCount: Array.isArray(scan.shorts) ? scan.shorts.length : 0,
        });
      }

      if (request.method === "GET" && incoming.pathname === "/watchlist") {
        const watchlist = await fetchGitHubJSON("watchlist.json");
        return json({ ...watchlist, servedAt: new Date().toISOString() });
      }

      if (request.method === "GET" && incoming.pathname === "/check-watchlist") {
        const result = await evaluateWatchlist();
        return json(result);
      }

      if (request.method === "GET" && incoming.pathname === "/run-scan") {
        if (!env.GITHUB_TOKEN) return json({ ok: false, error: "GITHUB_TOKEN secret is missing" }, 500);
        const workflowUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/bybit-scan.yml/dispatches`;
        const response = await fetch(workflowUrl, {
          method: "POST",
          headers: {
            "Accept": "application/vnd.github+json",
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bybit-trading-screener",
          },
          body: JSON.stringify({ ref: GITHUB_BRANCH }),
        });
        if (!response.ok) {
          const errorText = await response.text();
          return json({ ok: false, status: response.status, error: errorText }, response.status);
        }
        return json({ ok: true, status: "started", message: "Latest Bybit HTF/LTF scan started", repository: `${GITHUB_OWNER}/${GITHUB_REPO}`, workflow: "bybit-scan.yml", startedAt: new Date().toISOString() });
      }

      if (request.method !== "GET") return json({ ok: false, error: "GET requests only" }, 405);

      if (incoming.pathname === "/") {
        const symbol = (incoming.searchParams.get("symbol") || "BTCUSDT").toUpperCase();
        if (!validSymbol(symbol)) return json({ ok: false, error: "Invalid symbol" }, 400);
        const timeframes = { "1D": "D", "4H": "240", "1H": "60", "15m": "15", "5m": "5" };
        const entries = await Promise.all(Object.entries(timeframes).map(async ([name, interval]) => {
          const candles = await fetchKlines(symbol, interval, 200);
          return [name, candles];
        }));
        return json({ ok: true, symbol, source: "Bybit V5", fetchedAt: new Date().toISOString(), timeframes: Object.fromEntries(entries) });
      }

      if (!ALLOWED_PATHS.has(incoming.pathname)) return json({ retCode: 10001, retMsg: "Path not allowed" }, 403);
      const upstream = new URL(BYBIT_BASE + incoming.pathname);
      for (const [key, value] of incoming.searchParams) if (ALLOWED_PARAMS.has(key)) upstream.searchParams.append(key, value);
      if (incoming.pathname !== "/v5/market/time" && !upstream.searchParams.has("category")) upstream.searchParams.set("category", "linear");
      const response = await fetch(upstream.toString(), { headers: { accept: "application/json" } });
      const text = await response.text();
      return new Response(text, {
        status: response.status,
        headers: {
          "content-type": response.headers.get("content-type") || "application/json;charset=UTF-8",
          "cache-control": "no-store",
          "access-control-allow-origin": "*",
        },
      });
    } catch (error) {
      return json({ ok: false, error: error?.message || "Worker error" }, 500);
    }
  },
};
