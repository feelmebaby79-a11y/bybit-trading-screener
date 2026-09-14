import baseWorker from "./worker.js";
import { evaluateEntryTrigger } from "./entry_trigger.js";
import { runScheduledOiSurgeWatch } from "./oi_surge_alert.js";

const BYBIT_BASE = "https://api.bybit.com";
const WORKER_ORIGIN = "https://bybit-trading-screener.feelmebaby79.workers.dev";
const GITHUB_OWNER = "feelmebaby79-a11y";
const GITHUB_REPO = "bybit-trading-screener";
const GITHUB_BRANCH = "main";
const STATE_TTL_SECONDS = 60 * 60 * 24 * 7;
const FANOUT_BATCH_SIZE = 5;

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

function nullableNumber(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizeItem(x) {
  return {
    symbol: String(x?.symbol || "").trim().toUpperCase(),
    direction: String(x?.direction || "").trim().toUpperCase(),
    score: nullableNumber(x?.score),
    poi_low: nullableNumber(x?.poi_low),
    poi_high: nullableNumber(x?.poi_high),
    status: String(x?.status || "").trim().toUpperCase(),
    trigger_model: x?.trigger_model || null,
    updated_at: x?.updated_at || null,
    minimum_rr: nullableNumber(x?.minimum_rr),
    bucket: x?.bucket || null,
  };
}

async function fetchWatchlist() {
  const url = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/latest/watchlist.json?ts=${Date.now()}`;
  const r = await fetch(url, {
    cache: "no-store",
    headers: { accept: "application/json", "cache-control": "no-cache", "user-agent": "bybit-batched-watcher" },
  });
  if (!r.ok) throw new Error(`GitHub watchlist HTTP ${r.status}`);
  return r.json();
}

async function fetchCandles(symbol, interval, limit = 120) {
  const u = new URL(BYBIT_BASE + "/v5/market/kline");
  u.searchParams.set("category", "linear");
  u.searchParams.set("symbol", symbol);
  u.searchParams.set("interval", String(interval));
  u.searchParams.set("limit", String(limit));
  const r = await fetch(u, { headers: { accept: "application/json" } });
  if (!r.ok) throw new Error(`Bybit kline HTTP ${r.status}`);
  const d = await r.json();
  if (d.retCode !== 0) throw new Error(`Bybit kline ${d.retCode}: ${d.retMsg}`);
  return (Array.isArray(d.result?.list) ? d.result.list : []).map((x) => ({
    start: Number(x[0]), open: Number(x[1]), high: Number(x[2]), low: Number(x[3]), close: Number(x[4]),
  }));
}

async function fetchTicker(symbol) {
  const u = new URL(BYBIT_BASE + "/v5/market/tickers");
  u.searchParams.set("category", "linear");
  u.searchParams.set("symbol", symbol);
  const r = await fetch(u, { headers: { accept: "application/json" } });
  if (!r.ok) throw new Error(`Bybit ticker HTTP ${r.status}`);
  const d = await r.json();
  if (d.retCode !== 0) throw new Error(`Bybit ticker ${d.retCode}: ${d.retMsg}`);
  const p = Number(d.result?.list?.[0]?.lastPrice);
  if (!Number.isFinite(p)) throw new Error(`Invalid ticker for ${symbol}`);
  return p;
}

function closedOldestFirst(newestFirst) { return newestFirst.slice(1).reverse(); }
function inRange(v, l, h) { return v >= Math.min(l, h) && v <= Math.max(l, h); }
function touches(c, l, h) { return !!c && c.high >= Math.min(l, h) && c.low <= Math.max(l, h); }
function candleRange(c) { return Math.max(c.high - c.low, 1e-12); }
function candleBody(c) { return Math.abs(c.close - c.open); }
function priceKey(v) { return Number(v).toPrecision(12); }
function poiKey(p) { return `${p.symbol}:${p.direction}:${p.poi_tf || "MANUAL"}:${priceKey(p.poi_low)}:${priceKey(p.poi_high)}:${p.poi_created_at || "manual"}`; }

function htfDisplacement(cs, i, direction) {
  if (i < 5 || i >= cs.length) return false;
  const c = cs[i], prior = cs.slice(i - 5, i);
  const avgRange = prior.reduce((a, x) => a + candleRange(x), 0) / prior.length;
  const avgBody = prior.reduce((a, x) => a + candleBody(x), 0) / prior.length;
  const r = candleRange(c);
  const directional = direction === "LONG" ? c.close > c.open : c.close < c.open;
  const closeLocation = direction === "LONG" ? (c.close - c.low) / r : (c.high - c.close) / r;
  return directional && r >= Math.max(avgRange * 1.25, 1e-12) && candleBody(c) >= Math.max(avgBody * 1.35, r * 0.5) && closeLocation >= 0.65;
}

function latestDirectionalFvg(candles, direction, tf) {
  const cs = closedOldestFirst(candles);
  for (let i = cs.length - 1; i >= 2; i--) {
    const a = cs[i - 2], c = cs[i];
    let low = null, high = null, kind = null;
    if (direction === "LONG" && c.low > a.high) { low = a.high; high = c.low; kind = "BULL_FVG"; }
    if (direction === "SHORT" && c.high < a.low) { low = c.high; high = a.low; kind = "BEAR_FVG"; }
    if (low === null) continue;
    if (!htfDisplacement(cs, i - 1, direction)) continue;
    const invalid = cs.slice(i + 1).some((x) => direction === "LONG" ? x.close < low : x.close > high);
    if (invalid) continue;
    return { poi_low: low, poi_high: high, poi_tf: tf, poi_source: kind, poi_created_at: c.start };
  }
  return null;
}

function manualPoi(item) {
  if (item.poi_low === null || item.poi_high === null) return null;
  return { ...item, poi_tf: "1H/15m", poi_source: "MANUAL_VALIDATED", poi_created_at: "manual" };
}

async function resolvePois(item) {
  const [c15, c60] = await Promise.all([
    fetchCandles(item.symbol, "15", 100),
    fetchCandles(item.symbol, "60", 100),
  ]);
  const candidates = [
    manualPoi(item),
    latestDirectionalFvg(c15, item.direction, "15m"),
    latestDirectionalFvg(c60, item.direction, "1H"),
  ].filter(Boolean).map((p) => ({ ...item, ...p }));
  const seen = new Set(), out = [];
  for (const p of candidates) {
    const k = `${priceKey(p.poi_low)}:${priceKey(p.poi_high)}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ ...p, poi_id: poiKey(p) });
  }
  return out;
}

function cacheRequest(key) { return new Request(`https://state.bybit-trading-screener.invalid/${encodeURIComponent(key)}`); }
async function stateGetValue(env, key) {
  if (!key) return null;
  if (env.ENTRY_DEDUPE?.get) {
    const v = await env.ENTRY_DEDUPE.get(key);
    return v === null ? null : String(v);
  }
  try {
    const r = await caches.default.match(cacheRequest(key));
    return r ? await r.text() : null;
  } catch { return null; }
}
async function stateHas(env, key) { return (await stateGetValue(env, key)) !== null; }
async function statePutValue(env, key, value = "1") {
  if (!key) return;
  const v = String(value);
  if (env.ENTRY_DEDUPE?.put) {
    await env.ENTRY_DEDUPE.put(key, v, { expirationTtl: STATE_TTL_SECONDS });
    return;
  }
  try {
    await caches.default.put(cacheRequest(key), new Response(v, { headers: { "cache-control": `max-age=${STATE_TTL_SECONDS}` } }));
  } catch {}
}
async function statePut(env, key) { return statePutValue(env, key, "1"); }

function itemToParams(item) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(item)) if (v !== null && v !== undefined) p.set(k, String(v));
  return p;
}
function itemFromParams(u) {
  return normalizeItem({
    symbol: u.searchParams.get("symbol"), direction: u.searchParams.get("direction"), score: u.searchParams.get("score"),
    poi_low: u.searchParams.get("poi_low"), poi_high: u.searchParams.get("poi_high"), status: u.searchParams.get("status"),
    trigger_model: u.searchParams.get("trigger_model"), updated_at: u.searchParams.get("updated_at"), minimum_rr: u.searchParams.get("minimum_rr"),
    bucket: u.searchParams.get("bucket"),
  });
}

async function evaluateRealtimeOne(item, env) {
  if (item.status !== "ACTIVE") return [{ ...item, monitoring: false }];
  const [last, c1, pois] = await Promise.all([
    fetchTicker(item.symbol),
    fetchCandles(item.symbol, "1", 4),
    resolvePois(item),
  ]);
  const forming = c1[0] || null, closed1 = c1[1] || null, prevClosed1 = c1[2] || null;
  const out = [];
  for (const poi of pois) {
    const consumed = await stateHas(env, `consumed:${poi.poi_id}`);
    const inPoi = inRange(last, poi.poi_low, poi.poi_high);
    const formingTouch = touches(forming, poi.poi_low, poi.poi_high);
    const closedTouch = touches(closed1, poi.poi_low, poi.poi_high);
    const prevClosedTouch = touches(prevClosed1, poi.poi_low, poi.poi_high);
    const touchDetected = !consumed && (inPoi || formingTouch || closedTouch);
    out.push({ ...poi, monitoring: !consumed, consumed, last, in_poi: inPoi, touch_detected: touchDetected,
      first_arrival: touchDetected && !prevClosedTouch, arrival_basis: "ticker_or_1m_range_overlap",
      arrival_candle_start: formingTouch ? forming?.start : closedTouch ? closed1?.start : null, checked_at: new Date().toISOString() });
  }
  return out;
}

async function evaluateEntryOne(item, env) {
  if (item.status !== "ACTIVE") return [{ ...item, entry_monitoring: false }];
  const [c5, pois] = await Promise.all([fetchCandles(item.symbol, "5", 120), resolvePois(item)]);
  const m5 = closedOldestFirst(c5), latest5 = m5.at(-1)?.start || null, out = [];
  for (const poi of pois) {
    const consumed = await stateHas(env, `consumed:${poi.poi_id}`);
    if (consumed) { out.push({ ...poi, entry_monitoring: false, consumed: true, latest_closed_5m: latest5 }); continue; }
    const touchRaw = await stateGetValue(env, `poi-touch-ts:${poi.poi_id}`), touchTs = Number(touchRaw);
    if (!Number.isFinite(touchTs) || touchTs <= 0) {
      out.push({ ...poi, entry_monitoring: true, consumed: false, entry_ready: false, fresh_entry_candidate: false,
        entry_stage: "5m:WAIT_POI_TOUCH", poi_touch_start: null, latest_closed_5m: latest5, checked_at: new Date().toISOString() });
      continue;
    }
    const e = evaluateEntryTrigger({ candles5m: m5, item: { ...poi, poi_touch_start: touchTs } });
    const t = e.trigger || null;
    const fresh = !!(e.ready && t && t.tf === "5m" && t.retrace_start === latest5);
    out.push({ ...poi, poi_touch_start: touchTs, entry_monitoring: true, consumed: false, entry_ready: e.ready,
      fresh_entry_candidate: fresh, entry_stage: e.stage, trigger: t, latest_closed_5m: latest5, checked_at: new Date().toISOString() });
  }
  return out;
}

async function fanout(items, path) {
  const results = [];
  for (let i = 0; i < items.length; i += FANOUT_BATCH_SIZE) {
    const batch = items.slice(i, i + FANOUT_BATCH_SIZE);
    const settled = await Promise.all(batch.map(async (item) => {
      const url = `${WORKER_ORIGIN}${path}?${itemToParams(item).toString()}`;
      try {
        const r = await fetch(url, { headers: { accept: "application/json", "x-batched-child": "1" } });
        const d = await r.json();
        if (!r.ok || d?.ok !== true) throw new Error(d?.error || `HTTP ${r.status}`);
        return Array.isArray(d.items) ? d.items : [];
      } catch (e) {
        return [{ ...item, monitoring: path.includes("realtime") ? true : undefined, entry_monitoring: path.includes("entry") ? true : undefined, error: e?.message || String(e) }];
      }
    }));
    for (const group of settled) results.push(...group);
  }
  return results;
}

async function aggregateRealtime() {
  const p = await fetchWatchlist();
  const items = (Array.isArray(p?.items) ? p.items : []).map(normalizeItem);
  const results = await fanout(items, "/internal/realtime-one");
  return { ok: true, generated_at: new Date().toISOString(), watchlist_updated_at: p?.updated_at || null,
    execution_mode: "per_symbol_fanout", item_count: items.length, error_count: results.filter((x) => x?.error).length,
    arrivals: results.filter((x) => x.touch_detected === true), items: results };
}

async function aggregateEntry() {
  const p = await fetchWatchlist();
  const items = (Array.isArray(p?.items) ? p.items : []).map(normalizeItem);
  const results = await fanout(items, "/internal/entry-one");
  return { ok: true, generated_at: new Date().toISOString(), watchlist_updated_at: p?.updated_at || null,
    execution_mode: "per_symbol_fanout", item_count: items.length, error_count: results.filter((x) => x?.error).length,
    candidates: results.filter((x) => x.fresh_entry_candidate === true), items: results };
}

function formatPrice(v) {
  const n = Number(v); if (!Number.isFinite(n)) return "n/a";
  if (n >= 1000) return n.toFixed(2); if (n >= 1) return n.toFixed(4); if (n >= .01) return n.toFixed(5); return n.toFixed(8);
}
async function sendTelegram(env, message) {
  const token = String(env.TELEGRAM_BOT_TOKEN || "").trim(), chatId = String(env.TELEGRAM_CHAT_ID || "").trim();
  if (!token || !chatId) throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing");
  const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text: message, parse_mode: "HTML", disable_web_page_preview: true }) });
  const d = await r.json(); if (!r.ok || d.ok !== true) throw new Error(`Telegram send failed: ${r.status}`); return d;
}
function validPoi(i) {
  const low = Number(i?.poi_low), high = Number(i?.poi_high), tf = String(i?.poi_tf || ""), source = String(i?.poi_source || "");
  return !i?.error && Number.isFinite(low) && Number.isFinite(high) && low > 0 && high > 0 && !!i?.poi_id && ["1H", "15m", "1H/15m"].includes(tf) && !!source;
}
function buildPoiTouchMessage(i) {
  return `🚨 <b>POI 감지</b>\n<b>${i.symbol} ${i.direction}</b> / ${i.score ?? "-"}점\nPOI TF: <b>${i.poi_tf}</b>\n현재가: <code>${formatPrice(i.last)}</code>\nPOI: <code>${formatPrice(i.poi_low)} ~ ${formatPrice(i.poi_high)}</code>\n\n<b>POI 도달은 진입이 아닙니다.</b>\n다음 확인: liquidity sweep → MSS/CHoCH + displacement → strict FVG/validated OB retracement\n<b>No retrace = no trade.</b>`;
}
function buildEntryMessage(i) {
  const t = i.trigger || {}, z = Array.isArray(t.entry_zone) ? t.entry_zone : [];
  return `🎯 <b>ENTRY CANDIDATE</b>\n<b>${i.symbol} ${i.direction}</b> / ${i.score ?? "-"}점\nEntry TF: <b>5m</b>\nEntry zone: <code>${formatPrice(z[0])} ~ ${formatPrice(z[1])}</code>\nStructural SL: <code>${formatPrice(t.structural_sl)}</code>\nTP1: <code>${formatPrice(t.tp1)}</code>\nTP2: <code>${formatPrice(t.tp2)}</code>\n\n<b>시장가 추격 금지. No retrace = no trade.</b>`;
}

async function runSafePoiWatch(env) {
  const evaluated = await aggregateRealtime();
  const sent = [], errors = [];
  for (const i of evaluated.arrivals) {
    if (!validPoi(i) || i.poi_source === "MANUAL_VALIDATED") continue;
    const touchKey = `poi-touch:${i.poi_id}`, touchTsKey = `poi-touch-ts:${i.poi_id}`;
    try {
      if (await stateHas(env, touchKey)) continue;
      const touchTs = Date.now();
      await sendTelegram(env, buildPoiTouchMessage(i));
      await statePutValue(env, touchTsKey, touchTs); await statePut(env, touchKey);
      sent.push({ symbol: i.symbol, poi_id: i.poi_id });
    } catch (e) { errors.push({ symbol: i.symbol, error: e?.message || String(e) }); }
  }
  console.log(JSON.stringify({ event: "batched_poi_cron", generated_at: evaluated.generated_at, sent, errors, evaluation_errors: evaluated.error_count }));
}

async function runSafeEntryWatch(env) {
  const evaluated = await aggregateEntry();
  const sent = [], errors = [];
  for (const i of evaluated.candidates) {
    const eventId = i?.trigger?.event_id;
    if (!validPoi(i) || !eventId) continue;
    const entryKey = `entry:${eventId}`, consumedKey = `consumed:${i.poi_id}`;
    try {
      if (await stateHas(env, consumedKey) || await stateHas(env, entryKey)) continue;
      await sendTelegram(env, buildEntryMessage(i));
      await statePut(env, entryKey); await statePut(env, consumedKey);
      sent.push({ symbol: i.symbol, event_id: eventId, poi_id: i.poi_id });
    } catch (e) { errors.push({ symbol: i.symbol, error: e?.message || String(e) }); }
  }
  console.log(JSON.stringify({ event: "batched_entry_cron", generated_at: evaluated.generated_at, sent, errors, evaluation_errors: evaluated.error_count }));
}

function shouldRunEntryCron(controller) {
  const scheduled = Number(controller?.scheduledTime), when = Number.isFinite(scheduled) ? new Date(scheduled) : new Date();
  return when.getUTCMinutes() % 5 === 1;
}

export default {
  async fetch(request, env, ctx) {
    const u = new URL(request.url);
    try {
      if (request.method === "GET" && u.pathname === "/internal/realtime-one") return json({ ok: true, items: await evaluateRealtimeOne(itemFromParams(u), env) });
      if (request.method === "GET" && u.pathname === "/internal/entry-one") return json({ ok: true, items: await evaluateEntryOne(itemFromParams(u), env) });
      if (request.method === "GET" && u.pathname === "/realtime-watch") return json(await aggregateRealtime());
      if (request.method === "GET" && u.pathname === "/entry-watch") return json(await aggregateEntry());
      return baseWorker.fetch(request, env, ctx);
    } catch (e) {
      return json({ ok: false, error: e?.message || String(e) }, 500);
    }
  },
  async scheduled(controller, env, ctx) {
    const jobs = [runSafePoiWatch(env).catch((e) => console.error("POI watch failed", e))];
    if (shouldRunEntryCron(controller)) jobs.push(runSafeEntryWatch(env).catch((e) => console.error("Entry watch failed", e)));
    jobs.push(runScheduledOiSurgeWatch(env, controller?.scheduledTime).catch((e) => console.error("OI surge watch failed", e)));
    ctx.waitUntil(Promise.all(jobs));
  },
};
