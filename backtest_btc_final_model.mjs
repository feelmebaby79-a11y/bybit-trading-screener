import { evaluateEntryTrigger } from './entry_trigger.js';

const BASE='https://api.bybit.com';
const SYMBOL='BTCUSDT';
const DAY=86400000;
const TFMS={'5':300000,'15':900000,'60':3600000};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

async function fetchKlines(interval,start,end){
  const out=[]; let cursor=end;
  while(cursor>=start){
    const u=new URL(BASE+'/v5/market/kline');
    u.searchParams.set('category','linear');u.searchParams.set('symbol',SYMBOL);u.searchParams.set('interval',interval);u.searchParams.set('limit','1000');u.searchParams.set('end',String(cursor));
    const r=await fetch(u,{headers:{accept:'application/json'}}); if(!r.ok) throw new Error(`HTTP ${r.status} ${interval}`);
    const d=await r.json(); if(d.retCode!==0) throw new Error(`Bybit ${d.retCode} ${d.retMsg}`);
    const list=(d.result?.list||[]).map(x=>({start:+x[0],open:+x[1],high:+x[2],low:+x[3],close:+x[4]}));
    if(!list.length) break;
    for(const c of list) if(c.start>=start&&c.start<=end) out.push(c);
    const oldest=Math.min(...list.map(x=>x.start)); if(oldest<=start) break; cursor=oldest-1; await sleep(30);
  }
  const m=new Map(out.map(c=>[c.start,c])); return [...m.values()].sort((a,b)=>a.start-b.start);
}
function range(c){return Math.max(c.high-c.low,1e-12)} function body(c){return Math.abs(c.close-c.open)}
function htfDisplacement(cs,i,direction){if(i<5||i>=cs.length)return false;const c=cs[i],prior=cs.slice(i-5,i),ar=prior.reduce((a,x)=>a+range(x),0)/prior.length,ab=prior.reduce((a,x)=>a+body(x),0)/prior.length,r=range(c);const directional=direction==='LONG'?c.close>c.open:c.close<c.open;const loc=direction==='LONG'?(c.close-c.low)/r:(c.high-c.close)/r;return directional&&r>=Math.max(ar*1.25,1e-12)&&body(c)>=Math.max(ab*1.35,r*0.5)&&loc>=0.65}
function latestDirectionalFvgClosed(cs,direction,tf){
  for(let i=cs.length-1;i>=2;i--){const a=cs[i-2],c=cs[i];let low=null,high=null,kind=null;if(direction==='LONG'&&c.low>a.high){low=a.high;high=c.low;kind='BULL_FVG'}if(direction==='SHORT'&&c.high<a.low){low=c.high;high=a.low;kind='BEAR_FVG'}if(low===null)continue;if(!htfDisplacement(cs,i-1,direction))continue;const later=cs.slice(i+1);const invalid=later.some(x=>direction==='LONG'?x.close<low:x.close>high);if(invalid)continue;return{symbol:SYMBOL,direction,score:null,poi_low:low,poi_high:high,poi_tf:tf,poi_source:kind,poi_created_at:c.start,minimum_rr:1.5};}return null;
}
function poiId(p){return `${p.direction}:${p.poi_tf}:${p.poi_low}:${p.poi_high}:${p.poi_created_at}`}
function closedThrough(cs,t,tf){const ms=TFMS[tf]; return cs.filter(c=>c.start+ms<=t)}
function touchesMid(c,mid){return c.low<=mid&&c.high>=mid}
function outcome(sig,all5,endIndex){
  const t=sig.trigger, dir=t.direction, entry=t.entry_mid, sl=t.structural_sl, tp1=t.tp1, tp2=t.tp2;
  let fill=null, fillIndex=null;
  for(let i=endIndex;i<all5.length;i++){
    const c=all5[i];
    const invalidBeforeFill=dir==='LONG'?c.low<=sl:c.high>=sl;
    if(touchesMid(c,entry)){fill=c.start;fillIndex=i;break}
    if(invalidBeforeFill) return {filled:false,reason:'invalid_before_mid'};
  }
  if(fillIndex===null)return{filled:false,reason:'never_mid'};
  let hit1=false,hit2=false,loss=false,exit1=null,exit2=null,slAt=null;
  for(let i=fillIndex;i<all5.length;i++){
    const c=all5[i]; const slHit=dir==='LONG'?c.low<=sl:c.high>=sl; const t1=dir==='LONG'?c.high>=tp1:c.low<=tp1; const t2=dir==='LONG'?c.high>=tp2:c.low<=tp2;
    if(slHit && (!hit1 || (!hit2&&t2))){ if(i===fillIndex || (!hit1)){loss=true;slAt=c.start;break;} }
    if(!hit1&&t1){hit1=true;exit1=c.start}
    if(!hit2&&t2){hit2=true;exit2=c.start;break}
    if(slHit){loss=true;slAt=c.start;break}
  }
  return{filled:true,fill,hit1,hit2,loss,exit1,exit2,slAt,open:!hit2&&!loss};
}

const now=Date.now(); const end=Math.floor((now-300000)/300000)*300000; const start=end-30*DAY; const warm=start-8*DAY;
console.log(JSON.stringify({phase:'fetch',start:new Date(start).toISOString(),end:new Date(end).toISOString()}));
const [m5,m15,h1]=await Promise.all([fetchKlines('5',warm,end),fetchKlines('15',warm,end),fetchKlines('60',warm,end)]);
console.log(JSON.stringify({phase:'fetched',m5:m5.length,m15:m15.length,h1:h1.length}));
const eval5=m5.filter(c=>c.start>=start&&c.start<=end); const consumed=new Set(), cycles=new Set(), signals=[];
const indexByStart=new Map(m5.map((c,i)=>[c.start,i]));
for(let k=0;k<eval5.length;k++){
  const c=eval5[k], t=c.start+TFMS['5'];
  const hist5=m5.filter(x=>x.start+TFMS['5']<=t).slice(-120);
  const c15=closedThrough(m15,t,'15').slice(-100), c60=closedThrough(h1,t,'60').slice(-100);
  for(const dir of ['LONG','SHORT']) for(const [cs,tf] of [[c15,'15m'],[c60,'1H']]){
    const p=latestDirectionalFvgClosed(cs,dir,tf); if(!p)continue; const pid=poiId(p); if(consumed.has(pid))continue;
    const e=evaluateEntryTrigger({candles5m:hist5,item:p}); if(!e.ready||!e.trigger)continue;
    const tr=e.trigger; if(tr.retrace_start!==hist5.at(-1)?.start)continue; if(cycles.has(tr.cycle_id))continue; cycles.add(tr.cycle_id); consumed.add(pid);
    const idx=indexByStart.get(c.start); const o=outcome({trigger:tr},m5,idx);
    signals.push({signal_start:c.start,signal_iso:new Date(c.start).toISOString(),direction:dir,poi_tf:tf,poi_low:p.poi_low,poi_high:p.poi_high,poi_created_at:p.poi_created_at,entry:tr.entry_mid,sl:tr.structural_sl,tp1:tr.tp1,tp2:tr.tp2,live_rr:tr.live_rr,...o});
  }
}
const filled=signals.filter(x=>x.filled), resolved=filled.filter(x=>!x.open), wins1=resolved.filter(x=>x.hit1).length, wins2=resolved.filter(x=>x.hit2).length, losses=resolved.filter(x=>x.loss).length;
const liveRR=signals.map(x=>x.live_rr).filter(Number.isFinite);
const summary={symbol:SYMBOL,model:'final_dynamic_1H_15m_POI__5m_entry__min_live_rr_1.5',period:{start:new Date(start).toISOString(),end:new Date(end).toISOString(),days:30},bars:{m5:m5.length,m15:m15.length,h1:h1.length},entry_signals:signals.length,mid_filled:filled.length,unfilled:signals.length-filled.length,resolved:resolved.length,open:filled.filter(x=>x.open).length,sl_losses:losses,tp1_hits:wins1,tp2_hits:wins2,tp1_win_rate_resolved:resolved.length?wins1/resolved.length:null,tp2_win_rate_resolved:resolved.length?wins2/resolved.length:null,avg_live_rr:liveRR.length?liveRR.reduce((a,b)=>a+b,0)/liveRR.length:null,median_live_rr:liveRR.length?[...liveRR].sort((a,b)=>a-b)[Math.floor(liveRR.length/2)]:null,expectancy_all_out_2R:resolved.length?(wins1*2-(resolved.length-wins1))/resolved.length:null,expectancy_all_out_3R:resolved.length?(wins2*3-(resolved.length-wins2))/resolved.length:null};
console.log('BACKTEST_RESULT_START'); console.log(JSON.stringify({summary,signals},null,2)); console.log('BACKTEST_RESULT_END');
