import baseWorker from "./worker.js";

const BYBIT_BASE = "https://api.bybit.com";
const GITHUB_OWNER = "feelmebaby79-a11y";
const GITHUB_REPO = "bybit-trading-screener";
const GITHUB_BRANCH = "main";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json;charset=UTF-8", "cache-control": "no-store", "access-control-allow-origin": "*" } });
}
function isAuthorizedWorkerRequest(request, env) {
  const expectedKey = String(env.WORKER_ACCESS_KEY || "").trim();
  if (!expectedKey) return false;
  const authorization = request.headers.get("Authorization") || "";
  return authorization.startsWith("Bearer ") && authorization.slice(7).trim() === expectedKey;
}
async function fetchWatchlist() {
  const url = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/latest/watchlist.json`;
  const response = await fetch(url, { headers: { accept: "application/json", "user-agent": "bybit-realtime-poi-watcher" } });
  if (!response.ok) throw new Error(`GitHub watchlist HTTP ${response.status}`);
  return response.json();
}
async function fetchTicker(symbol) {
  const url = new URL(BYBIT_BASE + "/v5/market/tickers");
  url.searchParams.set("category", "linear"); url.searchParams.set("symbol", symbol);
  const response = await fetch(url.toString(), { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`Bybit ticker HTTP ${response.status}`);
  const data = await response.json();
  if (data.retCode !== 0) throw new Error(`Bybit ticker ${data.retCode}: ${data.retMsg}`);
  const last = Number(data.result?.list?.[0]?.lastPrice);
  if (!Number.isFinite(last)) throw new Error(`Invalid ticker for ${symbol}`);
  return last;
}
async function fetchRecentMinuteCandles(symbol) {
  const url = new URL(BYBIT_BASE + "/v5/market/kline");
  url.searchParams.set("category", "linear"); url.searchParams.set("symbol", symbol); url.searchParams.set("interval", "1"); url.searchParams.set("limit", "4");
  const response = await fetch(url.toString(), { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`Bybit kline HTTP ${response.status}`);
  const data = await response.json();
  if (data.retCode !== 0) throw new Error(`Bybit kline ${data.retCode}: ${data.retMsg}`);
  return (Array.isArray(data.result?.list) ? data.result.list : []).map(row => ({ start:Number(row[0]), open:Number(row[1]), high:Number(row[2]), low:Number(row[3]), close:Number(row[4]) }));
}
function inRange(value, low, high) { const lo=Math.min(low,high), hi=Math.max(low,high); return value>=lo && value<=hi; }
function candleTouchesRange(candle, low, high) {
  if (!candle || !Number.isFinite(candle.low) || !Number.isFinite(candle.high)) return false;
  const lo=Math.min(low,high), hi=Math.max(low,high); return candle.high>=lo && candle.low<=hi;
}
function nullableNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const n=Number(value); return Number.isFinite(n) ? n : null;
}
function normalizeItem(item) {
  return { symbol:String(item?.symbol||"").trim().toUpperCase(), direction:String(item?.direction||"").trim().toUpperCase(), score:nullableNumber(item?.score), poi_low:nullableNumber(item?.poi_low), poi_high:nullableNumber(item?.poi_high), status:String(item?.status||"").trim().toUpperCase(), trigger_model:item?.trigger_model||null };
}
function formatPrice(value) { if(!Number.isFinite(value))return "n/a"; if(value>=1000)return value.toFixed(2); if(value>=1)return value.toFixed(4); if(value>=0.01)return value.toFixed(5); return value.toFixed(8); }
async function sendTelegram(env,message) {
  const token=String(env.TELEGRAM_BOT_TOKEN||"").trim(), chatId=String(env.TELEGRAM_CHAT_ID||"").trim();
  if(!token||!chatId) throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing");
  const response=await fetch(`https://api.telegram.org/bot${token}/sendMessage`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({chat_id:chatId,text:message,parse_mode:"HTML",disable_web_page_preview:true})});
  const data=await response.json(); if(!response.ok||data.ok!==true) throw new Error(`Telegram send failed: ${response.status} ${JSON.stringify(data).slice(0,300)}`); return data;
}
async function evaluateRealtimeWatchlist() {
  const payload=await fetchWatchlist(); const items=(Array.isArray(payload?.items)?payload.items:[]).map(normalizeItem); const results=[];
  for(const item of items) {
    if(item.status!=="ACTIVE"||item.poi_low===null||item.poi_high===null){results.push({...item,monitoring:false});continue;}
    try {
      const [last,candles]=await Promise.all([fetchTicker(item.symbol),fetchRecentMinuteCandles(item.symbol)]);
      const currentInPoi=inRange(last,item.poi_low,item.poi_high), justClosed=candles.length>=2?candles[1]:null, priorClosed=candles.length>=3?candles[2]:null;
      const justClosedTouched=candleTouchesRange(justClosed,item.poi_low,item.poi_high), priorClosedTouched=candleTouchesRange(priorClosed,item.poi_low,item.poi_high);
      const firstArrival=justClosedTouched&&!priorClosedTouched;
      results.push({...item,monitoring:true,last,in_poi:currentInPoi,first_arrival:firstArrival,arrival_basis:"closed_1m_range_transition",arrival_candle_start:justClosed?.start||null,arrival_candle_high:Number.isFinite(justClosed?.high)?justClosed.high:null,arrival_candle_low:Number.isFinite(justClosed?.low)?justClosed.low:null,previous_candle_touched_poi:priorClosedTouched,checked_at:new Date().toISOString()});
    } catch(error){results.push({...item,monitoring:true,error:error?.message||String(error)});}
  }
  return {ok:true,generated_at:new Date().toISOString(),watchlist_updated_at:payload?.updated_at||null,arrivals:results.filter(x=>x.first_arrival===true),items:results};
}
function buildPoiMessage(item,testMode=false) {
  const prefix=testMode?"🧪 <b>TEST MODE</b>\n":"🚨 <b>POI 도착 알림</b>\n";
  return `${prefix}<b>${item.symbol} ${item.direction}</b> / ${item.score??"-"}점\n현재가: <code>${formatPrice(item.last)}</code>\nPOI: <code>${formatPrice(item.poi_low)} ~ ${formatPrice(item.poi_high)}</code>\n\n<b>POI 도착은 진입 신호가 아닙니다.</b>\n다음 확인: ${item.trigger_model||"liquidity sweep → MSS/CHoCH + displacement → FVG/validated OB retracement"}\n<b>No retrace = no trade.</b>`;
}
async function runScheduledPoiWatch(env) {
  const result=await evaluateRealtimeWatchlist(); const sent=[],errors=[];
  for(const item of result.arrivals){try{await sendTelegram(env,buildPoiMessage(item,false));sent.push(item.symbol);}catch(error){errors.push({symbol:item.symbol,error:error?.message||String(error)});}}
  console.log(JSON.stringify({event:"poi_cron",generated_at:result.generated_at,arrivals:result.arrivals.map(x=>x.symbol),telegram_sent:sent,telegram_errors:errors}));
  return {...result,telegram_sent:sent,telegram_errors:errors};
}
async function runSyntheticPoiTest(env,symbol) {
  const last=await fetchTicker(symbol), width=Math.max(Math.abs(last)*0.0005,1e-12);
  const item={symbol,direction:"TEST",score:100,poi_low:last-width,poi_high:last+width,last,trigger_model:"liquidity sweep → MSS/CHoCH + displacement → FVG/validated OB retracement"};
  await sendTelegram(env,buildPoiMessage(item,true)); return {ok:true,test:true,symbol,last,synthetic_poi:[item.poi_low,item.poi_high],telegram_sent:true};
}
export default {
  async fetch(request,env,ctx) {
    const url=new URL(request.url);
    if(request.method==="GET"&&url.pathname==="/realtime-watch"){try{return json(await evaluateRealtimeWatchlist());}catch(error){return json({ok:false,error:error?.message||String(error)},500);}}
    if(request.method==="POST"&&url.pathname==="/test-poi-telegram"){
      if(!isAuthorizedWorkerRequest(request,env))return json({ok:false,error:"Unauthorized"},401);
      const symbol=String(url.searchParams.get("symbol")||"BTCUSDT").trim().toUpperCase(); if(!/^[A-Z0-9]{3,30}USDT$/.test(symbol))return json({ok:false,error:"Invalid symbol"},400);
      try{return json(await runSyntheticPoiTest(env,symbol));}catch(error){return json({ok:false,error:error?.message||String(error)},500);}
    }
    return baseWorker.fetch(request,env,ctx);
  },
  async scheduled(controller,env,ctx){ctx.waitUntil(runScheduledPoiWatch(env).catch(error=>console.error("Realtime POI watch failed",error)));}
};
