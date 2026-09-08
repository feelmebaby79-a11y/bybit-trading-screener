import baseWorker from "./worker.js";
import { evaluateEntryTrigger } from "./entry_trigger.js";

const BYBIT_BASE="https://api.bybit.com";
const GITHUB_OWNER="feelmebaby79-a11y",GITHUB_REPO="bybit-trading-screener",GITHUB_BRANCH="main";
const STATE_TTL_SECONDS=60*60*24*7;
const stateMemory=new Map();

function json(data,status=200){return new Response(JSON.stringify(data),{status,headers:{"content-type":"application/json;charset=UTF-8","cache-control":"no-store","access-control-allow-origin":"*"}});}
function isAuthorizedWorkerRequest(request,env){const k=String(env.WORKER_ACCESS_KEY||"").trim(),a=request.headers.get("Authorization")||"";return!!k&&a.startsWith("Bearer ")&&a.slice(7).trim()===k;}
async function fetchWatchlist(){const r=await fetch(`https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/latest/watchlist.json`,{headers:{accept:"application/json","user-agent":"bybit-realtime-poi-watcher"}});if(!r.ok)throw new Error(`GitHub watchlist HTTP ${r.status}`);return r.json();}
async function fetchTicker(symbol){const u=new URL(BYBIT_BASE+"/v5/market/tickers");u.searchParams.set("category","linear");u.searchParams.set("symbol",symbol);const r=await fetch(u,{headers:{accept:"application/json"}});if(!r.ok)throw new Error(`Bybit ticker HTTP ${r.status}`);const d=await r.json();if(d.retCode!==0)throw new Error(`Bybit ticker ${d.retCode}: ${d.retMsg}`);const p=Number(d.result?.list?.[0]?.lastPrice);if(!Number.isFinite(p))throw new Error(`Invalid ticker for ${symbol}`);return p;}
async function fetchCandles(symbol,interval,limit=120){const u=new URL(BYBIT_BASE+"/v5/market/kline");u.searchParams.set("category","linear");u.searchParams.set("symbol",symbol);u.searchParams.set("interval",String(interval));u.searchParams.set("limit",String(limit));const r=await fetch(u,{headers:{accept:"application/json"}});if(!r.ok)throw new Error(`Bybit kline HTTP ${r.status}`);const d=await r.json();if(d.retCode!==0)throw new Error(`Bybit kline ${d.retCode}: ${d.retMsg}`);return(Array.isArray(d.result?.list)?d.result.list:[]).map(x=>({start:Number(x[0]),open:Number(x[1]),high:Number(x[2]),low:Number(x[3]),close:Number(x[4])}));}
async function fetchRecentMinuteCandles(symbol){return fetchCandles(symbol,"1",4);}
function closedOldestFirst(newestFirst){return newestFirst.slice(1).reverse();}
function inRange(v,l,h){return v>=Math.min(l,h)&&v<=Math.max(l,h);}
function touches(c,l,h){return!!c&&Number.isFinite(c.low)&&Number.isFinite(c.high)&&c.high>=Math.min(l,h)&&c.low<=Math.max(l,h);}
function nullableNumber(v){if(v===null||v===undefined||v==="")return null;const n=Number(v);return Number.isFinite(n)?n:null;}
function normalizeItem(x){return{symbol:String(x?.symbol||"").trim().toUpperCase(),direction:String(x?.direction||"").trim().toUpperCase(),score:nullableNumber(x?.score),poi_low:nullableNumber(x?.poi_low),poi_high:nullableNumber(x?.poi_high),status:String(x?.status||"").trim().toUpperCase(),trigger_model:x?.trigger_model||null,updated_at:x?.updated_at||null};}
function formatPrice(v){if(!Number.isFinite(v))return"n/a";if(v>=1000)return v.toFixed(2);if(v>=1)return v.toFixed(4);if(v>=.01)return v.toFixed(5);return v.toFixed(8);}
function priceKey(v){return Number(v).toPrecision(12);}
function poiKey(p){return `${p.symbol}:${p.direction}:${p.poi_tf||"MANUAL"}:${priceKey(p.poi_low)}:${priceKey(p.poi_high)}:${p.poi_created_at||"manual"}`;}
function cacheRequest(key){return new Request(`https://state.bybit-trading-screener.invalid/${encodeURIComponent(key)}`);}
function pruneState(now=Date.now()){for(const[k,v]of stateMemory.entries())if(now-v>STATE_TTL_SECONDS*1000)stateMemory.delete(k);}
async function stateHas(env,key){if(!key)return false;if(env.ENTRY_DEDUPE?.get)return(await env.ENTRY_DEDUPE.get(key))!==null;pruneState();if(stateMemory.has(key))return true;try{return !!(await caches.default.match(cacheRequest(key)));}catch{return false;}}
async function statePut(env,key){if(!key)return;if(env.ENTRY_DEDUPE?.put){await env.ENTRY_DEDUPE.put(key,"1",{expirationTtl:STATE_TTL_SECONDS});return;}stateMemory.set(key,Date.now());try{await caches.default.put(cacheRequest(key),new Response("1",{headers:{"cache-control":`max-age=${STATE_TTL_SECONDS}`}}));}catch{}}

function candleRange(c){return Math.max(c.high-c.low,1e-12);}
function candleBody(c){return Math.abs(c.close-c.open);}
function htfDisplacement(cs,i,direction){
  if(i<5||i>=cs.length)return false;
  const c=cs[i],prior=cs.slice(i-5,i),avgRange=prior.reduce((a,x)=>a+candleRange(x),0)/prior.length,avgBody=prior.reduce((a,x)=>a+candleBody(x),0)/prior.length,r=candleRange(c);
  const directional=direction==="LONG"?c.close>c.open:c.close<c.open;
  const closeLocation=direction==="LONG"?(c.close-c.low)/r:(c.high-c.close)/r;
  return directional&&r>=Math.max(avgRange*1.25,1e-12)&&candleBody(c)>=Math.max(avgBody*1.35,r*0.5)&&closeLocation>=0.65;
}
function latestDirectionalFvg(candles,direction,tf){
  const cs=closedOldestFirst(candles);
  for(let i=cs.length-1;i>=2;i--){
    const a=cs[i-2],c=cs[i];
    let low=null,high=null,kind=null;
    if(direction==="LONG"&&c.low>a.high){low=a.high;high=c.low;kind="BULL_FVG";}
    if(direction==="SHORT"&&c.high<a.low){low=c.high;high=a.low;kind="BEAR_FVG";}
    if(low===null)continue;
    if(!htfDisplacement(cs,i-1,direction))continue;
    const later=cs.slice(i+1);
    const invalid=later.some(x=>direction==="LONG"?x.close<low:x.close>high);
    if(invalid)continue;
    return{poi_low:low,poi_high:high,poi_tf:tf,poi_source:kind,poi_created_at:c.start};
  }
  return null;
}
function manualPoi(item){if(item.poi_low===null||item.poi_high===null)return null;return{...item,poi_tf:"1H/15m",poi_source:"MANUAL_VALIDATED",poi_created_at:"manual"};}
async function resolvePois(item){
  if(item.status!=="ACTIVE")return[];
  const [c15,c60]=await Promise.all([fetchCandles(item.symbol,"15",100),fetchCandles(item.symbol,"60",100)]);
  const candidates=[manualPoi(item),latestDirectionalFvg(c15,item.direction,"15m"),latestDirectionalFvg(c60,item.direction,"1H")].filter(Boolean).map(p=>({...item,...p}));
  const seen=new Set(),out=[];
  for(const p of candidates){const k=`${priceKey(p.poi_low)}:${priceKey(p.poi_high)}`;if(seen.has(k))continue;seen.add(k);out.push({...p,poi_id:poiKey(p)});}
  return out;
}

async function sendTelegram(env,message){const token=String(env.TELEGRAM_BOT_TOKEN||"").trim(),chatId=String(env.TELEGRAM_CHAT_ID||"").trim();if(!token||!chatId)throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing");const r=await fetch(`https://api.telegram.org/bot${token}/sendMessage`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({chat_id:chatId,text:message,parse_mode:"HTML",disable_web_page_preview:true})});const d=await r.json();if(!r.ok||d.ok!==true)throw new Error(`Telegram send failed: ${r.status} ${JSON.stringify(d).slice(0,300)}`);return d;}
function buildPoiMessage(i,test=false,label="TEST MODE"){const prefix=test?`🧪 <b>${label}</b>\n`:"🚨 <b>POI 도착 알림</b>\n";return`${prefix}<b>${i.symbol} ${i.direction}</b> / ${i.score??"-"}점\nPOI TF: <b>${i.poi_tf||"1H / 15m"}</b>\nPOI Source: <b>${i.poi_source||"validated"}</b>\nEntry TF: <b>5m</b>\n현재가: <code>${formatPrice(i.last)}</code>\nPOI: <code>${formatPrice(i.poi_low)} ~ ${formatPrice(i.poi_high)}</code>\n\n<b>POI 도착은 진입 신호가 아닙니다.</b>\n다음 확인: ${i.trigger_model||"liquidity sweep → MSS/CHoCH + displacement → FVG/validated OB retracement"}\n<b>No retrace = no trade.</b>`;}
function buildNewPoiMessage(i){return`🆕 <b>새 POI 감지</b>\n<b>${i.symbol} ${i.direction}</b> / ${i.score??"-"}점\nPOI TF: <b>${i.poi_tf}</b>\nPOI Source: <b>${i.poi_source}</b>\nPOI: <code>${formatPrice(i.poi_low)} ~ ${formatPrice(i.poi_high)}</code>\n\n아침 선정 코인 장중 구조 감시에서 새 POI 후보가 확인되었습니다.\nEntry TF: <b>5m only</b>`;}
function buildEntryMessage(item,t,test=false){const z=t.entry_zone||[],kind=t.zone?.kind||"FVG/OB",prefix=test?"🧪 <b>ENTRY TEST MODE</b>\n":"🎯 <b>ENTRY CANDIDATE</b>\n";return`${prefix}<b>${item.symbol} ${item.direction}</b> / ${item.score??"-"}점\nPOI TF: <b>${item.poi_tf||"1H / 15m"}</b>\nEntry TF: <b>5m</b>\nTrigger: sweep → MSS/CHoCH + displacement → ${kind} retracement\nEntry zone: <code>${formatPrice(z[0])} ~ ${formatPrice(z[1])}</code>\nEntry mid: <code>${formatPrice(t.entry_mid)}</code>\nStructural SL: <code>${formatPrice(t.structural_sl)}</code>\nTP1 (2R): <code>${formatPrice(t.tp1)}</code>\nTP2 (3R): <code>${formatPrice(t.tp2)}</code>\n\n<b>${test?"테스트 알림입니다. 실제 진입 신호가 아닙니다.":"진입 후보입니다. 시장가 추격 금지."}</b>\n이 ENTRY 발생으로 해당 POI는 consumed 처리됩니다.\nNo retrace = no trade.`;}

async function evaluateRealtimeWatchlist(env){
  const p=await fetchWatchlist(),items=(Array.isArray(p?.items)?p.items:[]).map(normalizeItem),results=[];
  for(const item of items){
    if(item.status!=="ACTIVE"){results.push({...item,monitoring:false});continue;}
    try{
      const [last,c1,pois]=await Promise.all([fetchTicker(item.symbol),fetchRecentMinuteCandles(item.symbol),resolvePois(item)]);
      const j=c1.length>=2?c1[1]:null,q=c1.length>=3?c1[2]:null;
      for(const poi of pois){
        const consumed=await stateHas(env,`consumed:${poi.poi_id}`);
        const jt=touches(j,poi.poi_low,poi.poi_high),qt=touches(q,poi.poi_low,poi.poi_high);
        results.push({...poi,monitoring:!consumed,consumed,last,in_poi:inRange(last,poi.poi_low,poi.poi_high),first_arrival:!consumed&&jt&&!qt,arrival_basis:"closed_1m_range_transition",arrival_candle_start:j?.start||null,arrival_candle_high:Number.isFinite(j?.high)?j.high:null,arrival_candle_low:Number.isFinite(j?.low)?j.low:null,previous_candle_touched_poi:qt,checked_at:new Date().toISOString()});
      }
    }catch(e){results.push({...item,monitoring:true,error:e?.message||String(e)});}
  }
  return{ok:true,generated_at:new Date().toISOString(),watchlist_updated_at:p?.updated_at||null,arrivals:results.filter(x=>x.first_arrival===true),items:results};
}

async function evaluateEntryWatchlist(env){
  const p=await fetchWatchlist(),items=(Array.isArray(p?.items)?p.items:[]).map(normalizeItem),results=[];
  for(const item of items){
    if(item.status!=="ACTIVE"){results.push({...item,entry_monitoring:false});continue;}
    try{
      const [c5,pois]=await Promise.all([fetchCandles(item.symbol,"5",120),resolvePois(item)]),m5=closedOldestFirst(c5),latest5=m5.at(-1)?.start||null;
      for(const poi of pois){
        const consumed=await stateHas(env,`consumed:${poi.poi_id}`);
        if(consumed){results.push({...poi,entry_monitoring:false,consumed:true,latest_closed_5m:latest5});continue;}
        const e=evaluateEntryTrigger({candles5m:m5,item:poi}),t=e.trigger||null;
        const fresh=!!(e.ready&&t&&t.tf==="5m"&&t.retrace_start===latest5);
        results.push({...poi,entry_monitoring:true,consumed:false,entry_ready:e.ready,fresh_entry_candidate:fresh,entry_stage:e.stage,trigger:t,latest_closed_5m:latest5,checked_at:new Date().toISOString()});
      }
    }catch(err){results.push({...item,entry_monitoring:true,error:err?.message||String(err)});}
  }
  return{ok:true,generated_at:new Date().toISOString(),watchlist_updated_at:p?.updated_at||null,candidates:results.filter(x=>x.fresh_entry_candidate===true),items:results};
}

async function runScheduledPoiWatch(env){
  const r=await evaluateRealtimeWatchlist(env),sent=[],newPoiSent=[],errors=[];
  for(const i of r.items.filter(x=>x.monitoring&&x.poi_source!=="MANUAL_VALIDATED")){
    const k=`announced:${i.poi_id}`;
    try{if(!(await stateHas(env,k))){await sendTelegram(env,buildNewPoiMessage(i));await statePut(env,k);newPoiSent.push({symbol:i.symbol,poi_id:i.poi_id});}}catch(e){errors.push({symbol:i.symbol,type:"new_poi",error:e?.message||String(e)});}
  }
  for(const i of r.arrivals){try{await sendTelegram(env,buildPoiMessage(i));sent.push({symbol:i.symbol,poi_id:i.poi_id});}catch(e){errors.push({symbol:i.symbol,type:"arrival",error:e?.message||String(e)});}}
  console.log(JSON.stringify({event:"poi_cron",generated_at:r.generated_at,new_poi_sent:newPoiSent,arrivals:r.arrivals.map(x=>({symbol:x.symbol,poi_id:x.poi_id})),telegram_sent:sent,telegram_errors:errors}));
  return{...r,new_poi_sent:newPoiSent,telegram_sent:sent,telegram_errors:errors};
}
async function runScheduledEntryWatch(env){
  const r=await evaluateEntryWatchlist(env),sent=[],duplicates=[],consumed=[],errors=[];
  for(const i of r.candidates){
    const eventId=i.trigger?.event_id,entryKey=`entry:${eventId}`,consumedKey=`consumed:${i.poi_id}`;
    try{
      if(await stateHas(env,consumedKey)){consumed.push({symbol:i.symbol,poi_id:i.poi_id});continue;}
      if(await stateHas(env,entryKey)){duplicates.push({symbol:i.symbol,event_id:eventId});continue;}
      await sendTelegram(env,buildEntryMessage(i,i.trigger));
      await statePut(env,entryKey);
      await statePut(env,consumedKey);
      sent.push({symbol:i.symbol,event_id:eventId,poi_id:i.poi_id});
    }catch(e){errors.push({symbol:i.symbol,event_id:eventId,poi_id:i.poi_id,error:e?.message||String(e)});}
  }
  console.log(JSON.stringify({event:"entry_cron",generated_at:r.generated_at,candidates:r.candidates.map(x=>({symbol:x.symbol,event_id:x.trigger?.event_id,poi_id:x.poi_id})),telegram_sent:sent,duplicates_suppressed:duplicates,consumed_suppressed:consumed,telegram_errors:errors}));
  return{...r,telegram_sent:sent,duplicates_suppressed:duplicates,consumed_suppressed:consumed,telegram_errors:errors};
}
async function runSyntheticPoiTest(env,symbol,label="TEST MODE"){const last=await fetchTicker(symbol),w=Math.max(Math.abs(last)*.0005,1e-12),i={symbol,direction:"TEST",score:100,poi_low:last-w,poi_high:last+w,last,poi_tf:"1H/15m",poi_source:"SYNTHETIC",trigger_model:"liquidity sweep → MSS/CHoCH + displacement → FVG/validated OB retracement"};await sendTelegram(env,buildPoiMessage(i,true,label));return{ok:true,test:true,symbol,last,synthetic_poi:[i.poi_low,i.poi_high],telegram_sent:true};}
async function runSyntheticEntryTest(env,symbol){const last=await fetchTicker(symbol),risk=Math.max(Math.abs(last)*.002,1e-12),item={symbol,direction:"LONG",score:100,poi_tf:"1H/15m"},t={tf:"5m",zone:{kind:"FVG"},entry_zone:[last-risk*.25,last+risk*.25],entry_mid:last,structural_sl:last-risk,tp1:last+2*risk,tp2:last+3*risk,event_id:`TEST:${symbol}:${Date.now()}`};await sendTelegram(env,buildEntryMessage(item,t,true));return{ok:true,test:true,type:"entry",symbol,telegram_sent:true,event_id:t.event_id};}
function shouldRunEntryCron(controller){const scheduled=Number(controller?.scheduledTime);const when=Number.isFinite(scheduled)?new Date(scheduled):new Date();return when.getUTCMinutes()%5===1;}

export default{
  async fetch(request,env,ctx){
    const u=new URL(request.url);
    if(request.method==="GET"&&u.pathname==="/realtime-watch"){try{return json(await evaluateRealtimeWatchlist(env));}catch(e){return json({ok:false,error:e?.message||String(e)},500);}}
    if(request.method==="GET"&&u.pathname==="/entry-watch"){try{return json(await evaluateEntryWatchlist(env));}catch(e){return json({ok:false,error:e?.message||String(e)},500);}}
    if(request.method==="POST"&&u.pathname==="/test-poi-telegram"){if(!isAuthorizedWorkerRequest(request,env))return json({ok:false,error:"Unauthorized"},401);const s=String(u.searchParams.get("symbol")||"BTCUSDT").trim().toUpperCase();if(!/^[A-Z0-9]{3,30}USDT$/.test(s))return json({ok:false,error:"Invalid symbol"},400);try{return json(await runSyntheticPoiTest(env,s));}catch(e){return json({ok:false,error:e?.message||String(e)},500);}}
    if(request.method==="POST"&&u.pathname==="/test-entry-telegram"){if(!isAuthorizedWorkerRequest(request,env))return json({ok:false,error:"Unauthorized"},401);const s=String(u.searchParams.get("symbol")||"BTCUSDT").trim().toUpperCase();if(!/^[A-Z0-9]{3,30}USDT$/.test(s))return json({ok:false,error:"Invalid symbol"},400);try{return json(await runSyntheticEntryTest(env,s));}catch(e){return json({ok:false,error:e?.message||String(e)},500);}}
    if(request.method==="POST"&&u.pathname==="/test-scheduled-poi"){if(!isAuthorizedWorkerRequest(request,env))return json({ok:false,error:"Unauthorized"},401);const s=String(u.searchParams.get("symbol")||"BTCUSDT").trim().toUpperCase();if(!/^[A-Z0-9]{3,30}USDT$/.test(s))return json({ok:false,error:"Invalid symbol"},400);try{const r=await runSyntheticPoiTest(env,s,"SCHEDULED PATH TEST");console.log(JSON.stringify({event:"scheduled_path_test",symbol:s,telegram_sent:true,at:new Date().toISOString()}));return json({...r,path:"scheduled_handler_dependencies",cron_configured:true});}catch(e){return json({ok:false,error:e?.message||String(e)},500);}}
    return baseWorker.fetch(request,env,ctx);
  },
  async scheduled(controller,env,ctx){
    const jobs=[runScheduledPoiWatch(env)];
    if(shouldRunEntryCron(controller))jobs.push(runScheduledEntryWatch(env));
    ctx.waitUntil(Promise.all(jobs).catch(e=>console.error("Realtime watch failed",e)));
  }
};
