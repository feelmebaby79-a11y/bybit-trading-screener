import baseWorker from "./worker.js";
import { evaluateEntryTrigger } from "./entry_trigger.js";
import { runScheduledOiSurgeWatch } from "./oi_surge_alert.js";

const BYBIT_BASE="https://api.bybit.com";
const GITHUB_OWNER="feelmebaby79-a11y",GITHUB_REPO="bybit-trading-screener",GITHUB_BRANCH="main";
const STATE_TTL_SECONDS=60*60*24*7;
const SHARD_COUNT=12;
// Deployment check: preserve existing SMC50 monitoring behavior.
// One-shot deploy verification: Telegram transport smoke test is triggered by commit marker.

function json(data,status=200){return new Response(JSON.stringify(data),{status,headers:{"content-type":"application/json;charset=UTF-8","cache-control":"no-store","access-control-allow-origin":"*"}})}
function nullableNumber(v){if(v===null||v===undefined||v==="")return null;const n=Number(v);return Number.isFinite(n)?n:null}
function normalizeItem(x){const configured=nullableNumber(x?.minimum_rr),minimum_rr=configured!==null&&configured>=1.5?configured:1.5;return{symbol:String(x?.symbol||"").trim().toUpperCase(),direction:String(x?.direction||"").trim().toUpperCase(),score:nullableNumber(x?.score),poi_low:nullableNumber(x?.poi_low),poi_high:nullableNumber(x?.poi_high),status:String(x?.status||"").trim().toUpperCase(),trigger_model:x?.trigger_model||null,updated_at:x?.updated_at||null,minimum_rr,bucket:x?.bucket||null,poi_tf:x?.poi_tf||null,poi_source:x?.poi_source||null,smc_swing_length:nullableNumber(x?.smc_swing_length),score_scale:nullableNumber(x?.score_scale),score_breakdown:x?.score_breakdown||null}}
async function fetchWatchlist(){const u=`https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}/latest/watchlist.json?ts=${Date.now()}`,r=await fetch(u,{cache:"no-store",headers:{accept:"application/json","cache-control":"no-cache","user-agent":"bybit-sharded-watcher"}});if(!r.ok)throw Error(`GitHub watchlist HTTP ${r.status}`);return r.json()}
async function fetchTicker(s){const u=new URL(BYBIT_BASE+"/v5/market/tickers");u.searchParams.set("category","linear");u.searchParams.set("symbol",s);const r=await fetch(u);if(!r.ok)throw Error(`Bybit ticker HTTP ${r.status}`);const d=await r.json();if(d.retCode!==0)throw Error(d.retMsg);const p=+d.result?.list?.[0]?.lastPrice;if(!Number.isFinite(p))throw Error(`Invalid ticker ${s}`);return p}
async function fetchCandles(s,i,l=120){const u=new URL(BYBIT_BASE+"/v5/market/kline");u.searchParams.set("category","linear");u.searchParams.set("symbol",s);u.searchParams.set("interval",String(i));u.searchParams.set("limit",String(l));const r=await fetch(u);if(!r.ok)throw Error(`Bybit kline HTTP ${r.status}`);const d=await r.json();if(d.retCode!==0)throw Error(d.retMsg);return(Array.isArray(d.result?.list)?d.result.list:[]).map(x=>({start:+x[0],open:+x[1],high:+x[2],low:+x[3],close:+x[4]}))}
function closedOldestFirst(a){return a.slice(1).reverse()}function inRange(v,l,h){return v>=Math.min(l,h)&&v<=Math.max(l,h)}function touches(c,l,h){return!!c&&c.high>=Math.min(l,h)&&c.low<=Math.max(l,h)}function candleRange(c){return Math.max(c.high-c.low,1e-12)}function candleBody(c){return Math.abs(c.close-c.open)}function priceKey(v){return Number(v).toPrecision(12)}function poiKey(p){return`${p.symbol}:${p.direction}:${p.poi_tf||"MANUAL"}:${priceKey(p.poi_low)}:${priceKey(p.poi_high)}:${p.poi_source||"UNKNOWN"}`}
function htfDisplacement(cs,i,d){if(i<5||i>=cs.length)return false;const c=cs[i],pr=cs.slice(i-5,i),ar=pr.reduce((a,x)=>a+candleRange(x),0)/pr.length,ab=pr.reduce((a,x)=>a+candleBody(x),0)/pr.length,r=candleRange(c),dir=d==="LONG"?c.close>c.open:c.close<c.open,loc=d==="LONG"?(c.close-c.low)/r:(c.high-c.close)/r;return dir&&r>=Math.max(ar*1.25,1e-12)&&candleBody(c)>=Math.max(ab*1.35,r*.5)&&loc>=.65}
function latestDirectionalFvg(candles,d,tf){const cs=closedOldestFirst(candles);for(let i=cs.length-1;i>=2;i--){const a=cs[i-2],c=cs[i];let low=null,high=null,kind=null;if(d==="LONG"&&c.low>a.high){low=a.high;high=c.low;kind="BULL_FVG"}if(d==="SHORT"&&c.high<a.low){low=c.high;high=a.low;kind="BEAR_FVG"}if(low===null)continue;if(!htfDisplacement(cs,i-1,d))continue;if(cs.slice(i+1).some(x=>d==="LONG"?x.close<low:x.close>high))continue;return{poi_low:low,poi_high:high,poi_tf:tf,poi_source:kind,poi_created_at:c.start}}return null}
function manualPoi(i){if(i.poi_low===null||i.poi_high===null)return null;return{...i,poi_tf:"1H/15m",poi_source:"MANUAL_VALIDATED",poi_created_at:"manual"}}
async function resolvePois(i){if(i.smc_swing_length!==50||i.score_scale!==100||!Number.isFinite(i.score)||i.poi_low===null||i.poi_high===null||!["15m","1H"].includes(i.poi_tf)||!["SMC50_OB","SMC50_FVG"].includes(i.poi_source))return [];const p={...i,poi_created_at:"SMC50"};return [{...p,poi_id:poiKey(p)}]}
function cacheRequest(k){return new Request(`https://state.bybit-trading-screener.invalid/${encodeURIComponent(k)}`)}
async function stateGet(env,k){if(!k)return null;if(env.ENTRY_DEDUPE?.get){const v=await env.ENTRY_DEDUPE.get(k);return v===null?null:String(v)}try{const r=await caches.default.match(cacheRequest(k));return r?await r.text():null}catch{return null}}
async function stateHas(env,k){return(await stateGet(env,k))!==null}
async function stateSet(env,k,v="1"){if(!k)return;if(env.ENTRY_DEDUPE?.put){await env.ENTRY_DEDUPE.put(k,String(v),{expirationTtl:STATE_TTL_SECONDS});return}try{await caches.default.put(cacheRequest(k),new Response(String(v),{headers:{"cache-control":`max-age=${STATE_TTL_SECONDS}`}}))}catch{}}
function shardItems(items,shard,shards){return items.filter((_,idx)=>idx%shards===shard)}
function parseShard(u){const shards=Math.max(1,Math.min(24,Number(u.searchParams.get("shards")||SHARD_COUNT)|0)),shard=Math.max(0,Math.min(shards-1,Number(u.searchParams.get("shard")||0)|0));return{shard,shards}}
async function evalRealtimeShard(env,shard,shards){const p=await fetchWatchlist(),all=(Array.isArray(p?.items)?p.items:[]).map(normalizeItem),items=shardItems(all,shard,shards),results=[];for(const batch of Array.from({length:Math.ceil(items.length/8)},(_,n)=>items.slice(n*8,n*8+8))){await Promise.all(batch.map(async item=>{if(item.status!=="ACTIVE"){results.push({...item,monitoring:false});continue}try{const pois=await resolvePois(item);if(!pois.length){results.push({...item,monitoring:false,reason:"SMC50_POI_NOT_AVAILABLE"});continue}const[last,c1]=await Promise.all([fetchTicker(item.symbol),fetchCandles(item.symbol,"1",6)]);for(const poi of pois){const consumed=await stateHas(env,`consumed:${poi.poi_id}`),inPoi=inRange(last,poi.poi_low,poi.poi_high),recent=c1.slice(0,5),hits=recent.filter(c=>touches(c,poi.poi_low,poi.poi_high)),touchDetected=!consumed&&(inPoi||hits.length>0),arrival=hits.length?Math.min(...hits.map(c=>c.start)):null;let persistedTouch=Number(await stateGet(env,`poi-touch-ts:${poi.poi_id}`));if(touchDetected&&(!Number.isFinite(persistedTouch)||persistedTouch<=0)){persistedTouch=Number(arrival)||Date.now();await stateSet(env,`poi-touch-ts:${poi.poi_id}`,persistedTouch)}results.push({...poi,monitoring:!consumed,consumed,last,in_poi:inPoi,touch_detected:touchDetected,first_arrival:touchDetected,arrival_basis:"ticker_or_recent_1m_overlap",arrival_candle_start:arrival,poi_touch_start:Number.isFinite(persistedTouch)&&persistedTouch>0?persistedTouch:null,checked_at:new Date().toISOString()})}}catch(e){results.push({...item,monitoring:true,error:e?.message||String(e)})}}))}return{ok:true,generated_at:new Date().toISOString(),watchlist_updated_at:p?.updated_at||null,execution_mode:"sharded",shard,shards,item_count:items.length,error_count:results.filter(x=>x.error).length,arrivals:results.filter(x=>x.touch_detected===true),items:results}}
async function evalEntryShard(env,shard,shards){const p=await fetchWatchlist(),all=(Array.isArray(p?.items)?p.items:[]).map(normalizeItem),items=shardItems(all,shard,shards),results=[];for(const batch of Array.from({length:Math.ceil(items.length/8)},(_,n)=>items.slice(n*8,n*8+8))){await Promise.all(batch.map(async item=>{if(item.status!=="ACTIVE"){results.push({...item,entry_monitoring:false});continue}try{const pois=await resolvePois(item);if(!pois.length){results.push({...item,entry_monitoring:false,reason:"SMC50_POI_NOT_AVAILABLE"});continue}const c5=await fetchCandles(item.symbol,"5",120),m5=closedOldestFirst(c5),latest5=m5.at(-1)?.start||null;for(const poi of pois){const consumed=await stateHas(env,`consumed:${poi.poi_id}`);if(consumed){results.push({...poi,entry_monitoring:false,consumed:true,latest_closed_5m:latest5});continue}const touchTs=Number(await stateGet(env,`poi-touch-ts:${poi.poi_id}`));if(!Number.isFinite(touchTs)||touchTs<=0){results.push({...poi,entry_monitoring:true,consumed:false,entry_ready:false,fresh_entry_candidate:false,entry_stage:"5m:WAIT_POI_TOUCH",poi_touch_start:null,latest_closed_5m:latest5,checked_at:new Date().toISOString()});continue}const e=evaluateEntryTrigger({candles5m:m5,item:{...poi,poi_touch_start:touchTs}}),live=liveEntryScore(poi,m5,e),t=e.trigger||null,fresh=!!(e.ready&&t&&t.tf==="5m"&&t.retrace_start===latest5);results.push({...poi,...live,poi_touch_start:touchTs,entry_monitoring:true,consumed:false,entry_ready:e.ready,fresh_entry_candidate:fresh,entry_stage:e.stage,trigger:t,latest_closed_5m:latest5,checked_at:new Date().toISOString()})}}catch(e){results.push({...item,entry_monitoring:true,error:e?.message||String(e)})}}))}return{ok:true,generated_at:new Date().toISOString(),watchlist_updated_at:p?.updated_at||null,execution_mode:"sharded",shard,shards,item_count:items.length,error_count:results.filter(x=>x.error).length,candidates:results.filter(x=>x.fresh_entry_candidate===true),items:results}}

// Live score recomputation from fresh CLOSED 5m candles. Static HTF fields remain
// explicitly labeled as scan-time values until their timeframes are refreshed.
function trendFromCandles(cs){if(cs.length<52)return null;const c=cs.at(-1),prior=cs.slice(-51,-1);const hi=Math.max(...prior.map(x=>x.high)),lo=Math.min(...prior.map(x=>x.low));return c.close>hi?"bullish":c.close<lo?"bearish":c.close>cs.at(-2).close?"bullish":"bearish"}
function locationFromCandles(cs){const w=cs.slice(-50);if(w.length<3)return"neutral";const hi=Math.max(...w.map(x=>x.high)),lo=Math.min(...w.map(x=>x.low));const p=(cs.at(-1).close-lo)/Math.max(hi-lo,1e-12);return p<=.05?"discount":p>=.95?"premium":p>=.475&&p<=.525?"equilibrium":"neutral"}
function sweepFromCandles(cs,side){if(cs.length<27)return false;const p=cs.slice(-26,-1),c=cs.at(-1);const x=side==="LONG"?Math.min(...p.map(y=>y.low)):Math.max(...p.map(y=>y.high));return side==="LONG"?c.low<x&&c.close>x:c.high>x&&c.close<x}
function mssFromCandles(cs,side){if(cs.length<52)return false;const p=cs.slice(-51,-1),c=cs.at(-1);return side==="LONG"?c.close>Math.max(...p.map(y=>y.high)):c.close<Math.min(...p.map(y=>y.low))}
async function liveTimeframeScore(item,m5,entry){
 const tf=await Promise.all(["D","240","60","15","30"].map(i=>fetchCandles(item.symbol,i,200).then(closedOldestFirst)));
 const [d,h4,h1,m15,m30]=tf;
 const btc=await Promise.all(["30","60","240"].map(i=>fetchCandles("BTCUSDT",i,4).then(closedOldestFirst)));
 const trend=item.direction==="LONG"?"bullish":"bearish",fav=item.direction==="LONG"?"discount":"premium",loc=locationFromCandles(h1);
 const htf=(trendFromCandles(d)===trend?13:0)+(trendFromCandles(h4)===trend?12:0);
 const oneh=(trendFromCandles(h1)===trend?8:0)+(loc===fav?7:loc==="equilibrium"?4:0);
 const ict=(trendFromCandles(m15)===trend?5:0)+(sweepFromCandles(m15,item.direction)?5:0)+(mssFromCandles(m15,item.direction)?5:0)+(htfDisplacement(m15,m15.length-1,item.direction)?5:0);
 const chase=Math.min(3,Math.max(0,Math.abs(m15.at(-1).close/m15.at(-2).close-1)*30));
 const entryloc=(loc===fav?7:loc==="equilibrium"?4:0)+3-chase;
 let good=0;for(let i=0;i<3;i++){const a=[m30,h1,h4][i],b=btc[i];if(a.length<2||b.length<2)continue;const delta=a.at(-1).close/a.at(-2).close-b.at(-1).close/b.at(-2).close;if(item.direction==="LONG"?delta>0:delta<0)good++}
 return{HTF:htf,"1H_STRUCTURE_LOCATION":oneh,"15M_ICT":ict,"ENTRY_LOCATION_CHASE":entryloc,"BTC_MARKET_ENV":good>=2?5:good===1?2:0};
}
function liveEntryScore(item,candles5m,entry){
 const parts={...(item.score_breakdown||{})};
 const c=candles5m.at(-1),prev=candles5m.at(-2);
 const direction=item.direction==="LONG"?1:-1;
 const fvg=!!(candles5m.length>=3&&(direction===1?c.low>candles5m.at(-3).high:c.high<candles5m.at(-3).low));
 const trigger=entry?.trigger||{};
 const rrRaw=trigger.live_rr??entry?.diagnostics?.m5?.live_rr;const rr=rrRaw==null?NaN:Number(rrRaw);
 parts["5M_FVG_OB_RETRACE"]=(fvg?5:0)+(entry?.ready?10:0);
 if(Number.isFinite(rr))parts.RR=rr>=2?10:rr>=1.5?8:rr>=1?4:0;
 const score=Math.max(0,Math.min(100,Object.values(parts).reduce((sum,v)=>sum+(Number(v)||0),0)));
 return{score,score_breakdown:parts,score_updated_at:new Date().toISOString(),score_mode:"PARTIAL_LIVE_5M",score_live_components:["5M_FVG_OB_RETRACE",...(Number.isFinite(rr)?["RR"]:[])],score_scan_components:["HTF","1H_STRUCTURE_LOCATION","15M_ICT","ENTRY_LOCATION_CHASE","BTC_MARKET_ENV"]};
}
function formatPrice(v){const n=+v;if(!Number.isFinite(n))return"n/a";if(n>=1000)return n.toFixed(2);if(n>=1)return n.toFixed(4);if(n>=.01)return n.toFixed(5);return n.toFixed(8)}
async function sendTelegram(env,m){const t=String(env.TELEGRAM_BOT_TOKEN||"").trim(),c=String(env.TELEGRAM_CHAT_ID||"").trim();if(!t||!c)throw Error("Telegram env missing");const r=await fetch(`https://api.telegram.org/bot${t}/sendMessage`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({chat_id:c,text:m,parse_mode:"HTML",disable_web_page_preview:true})});if(!r.ok)throw Error(`Telegram ${r.status}`)}
function validPoi(i){const l=+i?.poi_low,h=+i?.poi_high,tf=String(i?.poi_tf||""),src=String(i?.poi_source||"");return!i?.error&&Number.isFinite(l)&&Number.isFinite(h)&&l>0&&h>0&&!!i?.poi_id&&["1H","15m","1H/15m"].includes(tf)&&!!src}
function poiMsg(i){return`🚨 <b>POI 감지</b>
<b>${i.symbol} ${i.direction}</b> / ${i.score??"-"}점
POI TF: <b>${i.poi_tf}</b>
현재가: <code>${formatPrice(i.last)}</code>
POI: <code>${formatPrice(i.poi_low)} ~ ${formatPrice(i.poi_high)}</code>

<b>POI 도달은 진입이 아닙니다.</b>
다음 확인: liquidity sweep → MSS/CHoCH + displacement → strict FVG/validated OB retracement
<b>No retrace = no trade.</b>`}
function entryMsg(i){const t=i.trigger||{},z=Array.isArray(t.entry_zone)?t.entry_zone:[];return`🎯 <b>ENTRY CANDIDATE</b>
<b>${i.symbol} ${i.direction}</b> / ${i.score??"-"}점
Entry TF: <b>5m</b>
Entry zone: <code>${formatPrice(z[0])} ~ ${formatPrice(z[1])}</code>
Structural SL: <code>${formatPrice(t.structural_sl)}</code>
TP1: <code>${formatPrice(t.tp1)}</code>
TP2: <code>${formatPrice(t.tp2)}</code>

<b>시장가 추격 금지. No retrace = no trade.</b>`}
async function processPoiAlerts(env,e){for(const i of e.arrivals||[]){if(!validPoi(i)||i.smc_swing_length!==50||i.score_scale!==100||!Number.isFinite(i.score))continue;const k=`poi-touch:${i.poi_id}`;if(await stateHas(env,k))continue;await sendTelegram(env,poiMsg(i));await stateSet(env,k,"1")}}
async function processEntryAlerts(env,e){for(const i of e.candidates||[]){const id=i?.trigger?.event_id;if(!validPoi(i)||i.smc_swing_length!==50||i.score_scale!==100||!Number.isFinite(i.score)||!id)continue;const ck=`consumed:${i.poi_id}`,ek=`entry:${id}`;if(await stateHas(env,ck)||await stateHas(env,ek))continue;await sendTelegram(env,entryMsg(i));await stateSet(env,ek,"1");await stateSet(env,ck,"1")}}
async function runMarketShard(env,shard){try{const realtime=await evalRealtimeShard(env,shard,SHARD_COUNT);await processPoiAlerts(env,realtime);const entry=await evalEntryShard(env,shard,SHARD_COUNT);await processEntryAlerts(env,entry)}catch(e){console.error("Realtime/ENTRY shard failed",e)}}
function isAuthorizedWorkerRequest(request,env){const expected=String(env.WORKER_ACCESS_KEY||"").trim(),auth=String(request.headers.get("authorization")||"").trim();return!!expected&&auth===`Bearer ${expected}`}
async function runSyntheticPoiTest(env,symbol){const last=await fetchTicker(symbol),w=Math.max(Math.abs(last)*.0005,1e-12),i={symbol,direction:"TEST",score:100,poi_low:last-w,poi_high:last+w,poi_tf:"1H/15m",poi_source:"SYNTHETIC",poi_created_at:"test",last};i.poi_id=poiKey(i);await sendTelegram(env,poiMsg(i));return{ok:true,test:true,symbol,last,telegram_sent:true}}

export default{async fetch(request,env,ctx){const u=new URL(request.url),{shard,shards}=parseShard(u);try{if(request.method==="GET"&&u.pathname==="/realtime-watch")return json(await evalRealtimeShard(env,shard,shards));if(request.method==="GET"&&u.pathname==="/entry-watch")return json(await evalEntryShard(env,shard,shards));if(request.method==="POST"&&u.pathname==="/test-poi-telegram"){if(!isAuthorizedWorkerRequest(request,env))return json({ok:false,error:"Unauthorized"},401);const s=String(u.searchParams.get("symbol")||"BTCUSDT").trim().toUpperCase();return json(await runSyntheticPoiTest(env,s))}return baseWorker.fetch(request,env,ctx)}catch(e){return json({ok:false,error:e?.message||String(e)},500)}},async scheduled(controller,env,ctx){const d=new Date(Number(controller?.scheduledTime)||Date.now()),minute=d.getUTCMinutes(),SHARDS_PER_MINUTE=4,first=(minute*SHARDS_PER_MINUTE)%SHARD_COUNT,shards=Array.from({length:SHARDS_PER_MINUTE},(_,i)=>(first+i)%SHARD_COUNT);ctx.waitUntil(Promise.all([...shards.map(shard=>runMarketShard(env,shard)),runScheduledOiSurgeWatch(env,controller?.scheduledTime).catch(e=>console.error("OI surge failed",e))]))}};
