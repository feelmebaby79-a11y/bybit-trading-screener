import realtimeWorker from "./worker_realtime.js";
import { runScheduledOiSurgeWatch } from "./oi_surge_alert.js";

const GITHUB_OWNER = "feelmebaby79-a11y";
const GITHUB_REPO = "bybit-trading-screener";
const GITHUB_BRANCH = "main";
const STATE_TTL_SECONDS = 60 * 60 * 24 * 7;

function formatPrice(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "n/a";
  if (n >= 1000) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4);
  if (n >= 0.01) return n.toFixed(5);
  return n.toFixed(8);
}

function cacheRequest(key) {
  return new Request(`https://state.bybit-trading-screener.invalid/${encodeURIComponent(key)}`);
}

async function stateHas(env, key) {
  if (!key) return false;
  if (env.ENTRY_DEDUPE?.get) return (await env.ENTRY_DEDUPE.get(key)) !== null;
  try {
    return !!(await caches.default.match(cacheRequest(key)));
  } catch {
    return false;
  }
}

async function statePut(env, key) {
  if (!key) return;
  if (env.ENTRY_DEDUPE?.put) {
    await env.ENTRY_DEDUPE.put(key, "1", { expirationTtl: STATE_TTL_SECONDS });
    return;
  }
  try {
    await caches.default.put(
      cacheRequest(key),
      new Response("1", { headers: { "cache-control": `max-age=${STATE_TTL_SECONDS}` } })
    );
  } catch {}
}

async function sendTelegram(env, message) {
  const token = String(env.TELEGRAM_BOT_TOKEN || "").trim();
  const chatId = String(env.TELEGRAM_CHAT_ID || "").trim();
  if (!token || !chatId) throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing");
  const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text: message,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  const d = await r.json();
  if (!r.ok || d.ok !== true) {
    throw new Error(`Telegram send failed: ${r.status} ${JSON.stringify(d).slice(0, 300)}`);
  }
  return d;
}

async function fetchCurrentWatchlist() {
  const url = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/latest/watchlist.json?ts=${Date.now()}`;
  const r = await fetch(url, {
    cache: "no-store",
    headers: {
      accept: "application/json",
      "cache-control": "no-cache",
      "user-agent": "bybit-safe-alert-wrapper",
    },
  });
  if (!r.ok) throw new Error(`GitHub watchlist HTTP ${r.status}`);
  return r.json();
}

function activeSymbols(watchlist) {
  return new Set(
    (Array.isArray(watchlist?.items) ? watchlist.items : [])
      .filter(x => String(x?.status || "").toUpperCase() === "ACTIVE")
      .map(x => String(x?.symbol || "").trim().toUpperCase())
      .filter(Boolean)
  );
}

function validPoi(i) {
  const low = Number(i?.poi_low);
  const high = Number(i?.poi_high);
  const tf = String(i?.poi_tf || "");
  const source = String(i?.poi_source || "");
  return (
    !i?.error &&
    Number.isFinite(low) &&
    Number.isFinite(high) &&
    low > 0 &&
    high > 0 &&
    !!i?.poi_id &&
    ["1H", "15m", "1H/15m"].includes(tf) &&
    !!source &&
    source !== "undefined"
  );
}

function buildNewPoiMessage(i) {
  return `🆕 <b>새 POI 감지</b>\n<b>${i.symbol} ${i.direction}</b> / ${i.score ?? "-"}점\nPOI TF: <b>${i.poi_tf}</b>\nPOI Source: <b>${i.poi_source}</b>\nPOI: <code>${formatPrice(i.poi_low)} ~ ${formatPrice(i.poi_high)}</code>\n\n오늘 추천종목 장중 구조 감시에서 새 POI 후보가 확인되었습니다.\nEntry TF: <b>5m only</b>`;
}

function buildPoiArrivalMessage(i) {
  return `🚨 <b>POI 도착 알림</b>\n<b>${i.symbol} ${i.direction}</b> / ${i.score ?? "-"}점\nPOI TF: <b>${i.poi_tf}</b>\nPOI Source: <b>${i.poi_source}</b>\nEntry TF: <b>5m</b>\n현재가: <code>${formatPrice(i.last)}</code>\nPOI: <code>${formatPrice(i.poi_low)} ~ ${formatPrice(i.poi_high)}</code>\n\n<b>POI 도착은 진입 신호가 아닙니다.</b>\n다음 확인: ${i.trigger_model || "liquidity sweep → MSS/CHoCH + displacement → FVG/validated OB retracement"}\n<b>No retrace = no trade.</b>`;
}

function buildEntryMessage(i) {
  const t = i.trigger || {};
  const z = Array.isArray(t.entry_zone) ? t.entry_zone : [];
  const kind = t.zone?.kind || "FVG/OB";
  const rr1 = Number.isFinite(Number(t.rr_tp1)) ? Number(t.rr_tp1).toFixed(2) : "n/a";
  const rr2 = Number.isFinite(Number(t.rr_tp2)) ? Number(t.rr_tp2).toFixed(2) : "n/a";
  return `🎯 <b>ENTRY CANDIDATE</b>\n<b>${i.symbol} ${i.direction}</b> / ${i.score ?? "-"}점\nPOI TF: <b>${i.poi_tf}</b>\nEntry TF: <b>5m</b>\nTrigger: sweep → MSS/CHoCH + displacement → ${kind} retracement\nEntry zone: <code>${formatPrice(z[0])} ~ ${formatPrice(z[1])}</code>\nEntry mid: <code>${formatPrice(t.entry_mid)}</code>\nStructural SL: <code>${formatPrice(t.structural_sl)}</code>\nTP1: <code>${formatPrice(t.tp1)}</code> / R:R <b>${rr1}:1</b>\nTP2: <code>${formatPrice(t.tp2)}</code> / R:R <b>${rr2}:1</b>\n\n<b>진입 후보입니다. 시장가 추격 금지. No retrace = no trade.</b>`;
}

async function callRealtimeEndpoint(path, env, ctx) {
  const response = await realtimeWorker.fetch(new Request(`https://internal${path}`), env, ctx);
  const payload = await response.json();
  if (!response.ok || payload?.ok !== true) {
    throw new Error(`${path} failed: ${response.status} ${JSON.stringify(payload).slice(0, 300)}`);
  }
  return payload;
}

async function runSafePoiWatch(env, ctx) {
  const evaluated = await callRealtimeEndpoint("/realtime-watch", env, ctx);
  const current = await fetchCurrentWatchlist();
  const active = activeSymbols(current);
  const sameVersion = String(evaluated.watchlist_updated_at || "") === String(current.updated_at || "");
  const newPoiSent = [];
  const arrivalSent = [];
  const suppressed = [];
  const errors = [];

  if (!sameVersion) {
    console.log(JSON.stringify({
      event: "safe_poi_cron",
      stale_evaluation_suppressed: true,
      evaluated_watchlist_updated_at: evaluated.watchlist_updated_at,
      current_watchlist_updated_at: current.updated_at,
    }));
    return { ok: true, stale_evaluation_suppressed: true };
  }

  for (const i of Array.isArray(evaluated.items) ? evaluated.items : []) {
    const symbol = String(i?.symbol || "").toUpperCase();
    if (!active.has(symbol) || !i?.monitoring || !validPoi(i) || i.poi_source === "MANUAL_VALIDATED") {
      if (i?.monitoring && !validPoi(i)) suppressed.push({ symbol, reason: i?.error ? "evaluation_error" : "invalid_poi" });
      continue;
    }
    const key = `announced:${i.poi_id}`;
    try {
      if (!(await stateHas(env, key))) {
        await sendTelegram(env, buildNewPoiMessage(i));
        await statePut(env, key);
        newPoiSent.push({ symbol, poi_id: i.poi_id });
      }
    } catch (e) {
      errors.push({ symbol, type: "new_poi", error: e?.message || String(e) });
    }
  }

  for (const i of Array.isArray(evaluated.arrivals) ? evaluated.arrivals : []) {
    const symbol = String(i?.symbol || "").toUpperCase();
    if (!active.has(symbol) || !validPoi(i)) {
      suppressed.push({ symbol, reason: "invalid_or_stale_arrival" });
      continue;
    }
    try {
      await sendTelegram(env, buildPoiArrivalMessage(i));
      arrivalSent.push({ symbol, poi_id: i.poi_id });
    } catch (e) {
      errors.push({ symbol, type: "arrival", error: e?.message || String(e) });
    }
  }

  console.log(JSON.stringify({
    event: "safe_poi_cron",
    generated_at: evaluated.generated_at,
    watchlist_updated_at: current.updated_at,
    active_symbols: [...active],
    new_poi_sent: newPoiSent,
    arrivals_sent: arrivalSent,
    suppressed,
    errors,
  }));
  return { ok: true, new_poi_sent: newPoiSent, arrivals_sent: arrivalSent, suppressed, errors };
}

async function runSafeEntryWatch(env, ctx) {
  const evaluated = await callRealtimeEndpoint("/entry-watch", env, ctx);
  const current = await fetchCurrentWatchlist();
  const active = activeSymbols(current);
  const sameVersion = String(evaluated.watchlist_updated_at || "") === String(current.updated_at || "");
  const sent = [];
  const suppressed = [];
  const errors = [];

  if (!sameVersion) {
    console.log(JSON.stringify({
      event: "safe_entry_cron",
      stale_evaluation_suppressed: true,
      evaluated_watchlist_updated_at: evaluated.watchlist_updated_at,
      current_watchlist_updated_at: current.updated_at,
    }));
    return { ok: true, stale_evaluation_suppressed: true };
  }

  for (const i of Array.isArray(evaluated.candidates) ? evaluated.candidates : []) {
    const symbol = String(i?.symbol || "").toUpperCase();
    const eventId = i?.trigger?.event_id;
    if (!active.has(symbol) || !validPoi(i) || !i?.fresh_entry_candidate || !eventId) {
      suppressed.push({ symbol, reason: "invalid_or_stale_entry" });
      continue;
    }
    const entryKey = `entry:${eventId}`;
    const consumedKey = `consumed:${i.poi_id}`;
    try {
      if (await stateHas(env, consumedKey)) {
        suppressed.push({ symbol, reason: "poi_consumed" });
        continue;
      }
      if (await stateHas(env, entryKey)) {
        suppressed.push({ symbol, reason: "duplicate_entry" });
        continue;
      }
      await sendTelegram(env, buildEntryMessage(i));
      await statePut(env, entryKey);
      await statePut(env, consumedKey);
      sent.push({ symbol, event_id: eventId, poi_id: i.poi_id });
    } catch (e) {
      errors.push({ symbol, type: "entry", error: e?.message || String(e) });
    }
  }

  console.log(JSON.stringify({
    event: "safe_entry_cron",
    generated_at: evaluated.generated_at,
    watchlist_updated_at: current.updated_at,
    active_symbols: [...active],
    sent,
    suppressed,
    errors,
  }));
  return { ok: true, sent, suppressed, errors };
}

function shouldRunEntryCron(controller) {
  const scheduled = Number(controller?.scheduledTime);
  const when = Number.isFinite(scheduled) ? new Date(scheduled) : new Date();
  return when.getUTCMinutes() % 5 === 1;
}

export default {
  async fetch(request, env, ctx) {
    return realtimeWorker.fetch(request, env, ctx);
  },

  async scheduled(controller, env, ctx) {
    const jobs = [runSafePoiWatch(env, ctx)];
    if (shouldRunEntryCron(controller)) jobs.push(runSafeEntryWatch(env, ctx));
    jobs.push(
      runScheduledOiSurgeWatch(env, controller?.scheduledTime)
        .catch(e => console.error("OI surge watch failed", e))
    );
    ctx.waitUntil(
      Promise.all(jobs).catch(e => console.error("Scheduled watcher failed", e))
    );
  }
};
