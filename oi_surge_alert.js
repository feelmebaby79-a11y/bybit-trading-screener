const BYBIT_BASE="https://api.bybit.com",STATE_TTL_SECONDS=172800,SNAPSHOT_TTL_SECONDS=21600,OI_ALERT_THRESHOLD_1M=30;
function cacheRequest(k){return new Request(`https://oi-alert-state.invalid/${encodeURIComponent(k)}`)}
async function stateGet(e,k){if(e.ENTRY_DEDUPE?.get)return await e.ENTRY_DEDUPE.get(k);try{const r=await caches.default.match(cacheRequest(k));return r?await r.text():null}catch{return null}}
async function stateSet(e,k,v,ttl=STATE_TTL_SECONDS){if(e.ENTRY_DEDUPE?.put){await e.ENTRY_DEDUPE.put(k,String(v),{expirationTtl:ttl});return}try{await caches.default.put(cacheRequest(k),new Response(String(v),{headers:{"cache-control":`max-age=${ttl}`}}))}catch{}}
async function stateHas(e,k){return(await stateGet(e,k))!==null}async function statePut(e,k){return stateSet(e,k,"1")}
async function bybit(path,p={}){const u=new URL(BYBIT_BASE+path);Object.entries(p).forEach(([k,v])=>u.searchParams.set(k,String(v)));const r=await fetch(u);if(!r.ok)throw Error(`Bybit HTTP ${r.status}`);const d=await r.json();if(d.retCode!==0)throw Error(d.retMsg);return d.result||{}}
async function fetchAllTickers(){const list=(await bybit("/v5/market/tickers",{category:"linear"})).list||[];const out={};for(const x of list){const s=String(x.symbol||"").toUpperCase(),oi=+x.openInterest,last=+x.lastPrice;if(!/^[A-Z0-9]{2,40}USDT$/.test(s)||!Number.isFinite(oi)||oi<=0||!Number.isFinite(last)||last<=0)continue;out[s]={oi,last,turnover24h:+x.turnover24h||0}}return out}
async function fetch1mVolumeContext(symbol){const list=(await bybit("/v5/market/kline",{category:"linear",symbol,interval:"1",limit:3})).list||[];if(list.length<3)return{};const closed=list[1]||[],prev=list[2]||[];const turnover=+closed[6],prevTurnover=+prev[6],open=+closed[1],close=+closed[4];return{volume1m_turnover:Number.isFinite(turnover)?turnover:null,volume1m_change_pct:Number.isFinite(turnover)&&Number.isFinite(prevTurnover)&&prevTurnover>0?(turnover/prevTurnover-1)*100:null,closed_price1m_pct:Number.isFinite(open)&&open>0&&Number.isFinite(close)?(close/open-1)*100:null}}
async function snapGet(env,minute){const v=await stateGet(env,`oi-all:${minute}`);if(v===null)return null;try{return JSON.parse(v)}catch{return null}}
async function snapPut(env,minute,obj){return stateSet(env,`oi-all:${minute}`,JSON.stringify(obj),SNAPSHOT_TTL_SECONDS)}
function pct(n,p){return Number.isFinite(n)&&Number.isFinite(p)&&p>0?(n/p-1)*100:null}
function metric(cur,prev,s,field){return prev?.[s]?pct(cur[s]?.[field],prev[s]?.[field]):null}
function classify(x){const vals=[x.oi5,x.oi15,x.oi1h,x.oi4h],persistent=vals.filter(Number.isFinite).length>=2&&vals.filter(Number.isFinite).every(v=>v>0),acceleration=Number.isFinite(x.oi1)&&Number.isFinite(x.oi5)?x.oi1*5>x.oi5*1.2:Number.isFinite(x.oi15)&&Number.isFinite(x.oi1h)&&x.oi15>0&&x.oi1h>0&&x.oi15*4>x.oi1h*1.2;if(!Number.isFinite(x.oi1)||x.oi1<OI_ALERT_THRESHOLD_1M)return{hit:false,level:null,persistent,acceleration};return{hit:true,level:"EXTREME_SURGE",persistent,acceleration}}
async function sendTelegram(e,m){const t=String(e.TELEGRAM_BOT_TOKEN||"").trim(),c=String(e.TELEGRAM_CHAT_ID||"").trim();if(!t||!c)throw Error("Telegram env missing");const r=await fetch(`https://api.telegram.org/bot${t}/sendMessage`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({chat_id:c,text:m,parse_mode:"HTML"})});if(!r.ok)throw Error(`Telegram ${r.status}`)}
function f(v){return Number.isFinite(v)?`${v>=0?"+":""}${v.toFixed(2)}%`:"n/a"}
function money(v){if(!Number.isFinite(v))return"n/a";if(v>=1e9)return`$${(v/1e9).toFixed(2)}B`;if(v>=1e6)return`$${(v/1e6).toFixed(2)}M`;if(v>=1e3)return`$${(v/1e3).toFixed(1)}K`;return`$${v.toFixed(0)}`}
function message(x){return`🚨🚨 <b>OI +30% 급등</b>\n<b>${x.symbol}</b>\n\n<b>OI 변화</b>\n1m: <b>${f(x.oi1)}</b> | 5m: ${f(x.oi5)} | 15m: ${f(x.oi15)}\n1H: ${f(x.oi1h)} | 4H: ${f(x.oi4h)}\n\n<b>거래량 / 거래대금</b>\n최근 확정 1m 거래대금: <b>${money(x.volume1m_turnover)}</b>\n직전 1m 대비: <b>${f(x.volume1m_change_pct)}</b>\n\n<b>가격 변화</b>\n실시간 1m: <b>${f(x.price1m)}</b>\n확정 1m 봉: <b>${f(x.closed_price1m_pct)}</b>\n5m: ${f(x.price5m)} | 1H: ${f(x.price1h)}\n\n지속 증가: <b>${x.persistent?"YES":"NO"}</b> / 가속: <b>${x.acceleration?"YES":"NO"}</b>\n\n<b>1분 OI +30.00% 이상만 알림</b>\n<b>OI 알림은 ENTRY 신호가 아닙니다.</b>`}
export async function runScheduledOiSurgeWatch(env,scheduledTime=Date.now()){
 const w=new Date(+scheduledTime||Date.now()),m=Math.floor(w.getTime()/60000),cur=await fetchAllTickers();
 const [p1,p5,p15,p60,p240]=await Promise.all([snapGet(env,m-1),snapGet(env,m-5),snapGet(env,m-15),snapGet(env,m-60),snapGet(env,m-240)]);
 await snapPut(env,m,cur);
 const hits=[],sent=[],errors=[];
 for(const symbol of Object.keys(cur))try{
  const x={symbol,oi1:metric(cur,p1,symbol,"oi"),oi5:metric(cur,p5,symbol,"oi"),oi15:metric(cur,p15,symbol,"oi"),oi1h:metric(cur,p60,symbol,"oi"),oi4h:metric(cur,p240,symbol,"oi"),price1m:metric(cur,p1,symbol,"last"),price5m:metric(cur,p5,symbol,"last"),price1h:metric(cur,p60,symbol,"last"),turnover24h:cur[symbol].turnover24h};
  const c=classify(x);if(!c.hit)continue;Object.assign(x,c);Object.assign(x,await fetch1mVolumeContext(symbol));hits.push(x);
  const key=`oi-alert:${symbol}:${c.level}:${w.toISOString().slice(0,13)}`;if(await stateHas(env,key))continue;
  await sendTelegram(env,message(x));await statePut(env,key);sent.push(x);
 }catch(e){errors.push({symbol,error:e?.message||String(e)})}
 return{ok:true,generated_at:new Date().toISOString(),universe_source:"BYBIT_LINEAR_ALL_USDT",alert_threshold_1m_pct:OI_ALERT_THRESHOLD_1M,checked_symbols:Object.keys(cur).length,hits,telegram_sent:sent,errors};
}
