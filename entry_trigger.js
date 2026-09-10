// Strict ICT entry-trigger detector. Input candles must be CLOSED and oldest -> newest.
// POI is defined from 1H/15m context; entry confirmation is evaluated on CLOSED 5m candles only.
// ENTRY activation is anchored to the recorded realtime POI touch timestamp when available.
// ENTRY is blocked unless live reward:risk to a verified meaningful expansion-liquidity target is >= minimum_rr.

function finite(v){return Number.isFinite(Number(v));}
function n(v){return Number(v);}
function overlap(c,lo,hi){return c.high>=Math.min(lo,hi)&&c.low<=Math.max(lo,hi);}
function body(c){return Math.abs(c.close-c.open);}
function range(c){return Math.max(c.high-c.low,1e-12);}
function avg(xs){return xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:0;}

function pivots(candles,left=2,right=2){const highs=[],lows=[];for(let i=left;i<candles.length-right;i++){const c=candles[i];let ph=true,pl=true;for(let j=i-left;j<=i+right;j++)if(j!==i){if(candles[j].high>=c.high)ph=false;if(candles[j].low<=c.low)pl=false;}if(ph)highs.push({i,price:c.high,start:c.start});if(pl)lows.push({i,price:c.low,start:c.start});}return{highs,lows};}
function strictFvgs(candles,fromIndex,direction){const out=[];for(let i=Math.max(2,fromIndex);i<candles.length;i++){const a=candles[i-2],c=candles[i];if(direction==='LONG'&&c.low>a.high)out.push({kind:'BULL_FVG',created_i:i,low:a.high,high:c.low,start:c.start,impulse_i:i-1});if(direction==='SHORT'&&c.high<a.low)out.push({kind:'BEAR_FVG',created_i:i,low:c.high,high:a.low,start:c.start,impulse_i:i-1});}return out;}
function displacement(candles,i,direction){if(i<5||i>=candles.length)return false;const c=candles[i],prior=candles.slice(i-5,i),r=range(c),ar=avg(prior.map(range)),ab=avg(prior.map(body)),directional=direction==='LONG'?c.close>c.open:c.close<c.open,closeLocation=direction==='LONG'?(c.close-c.low)/r:(c.high-c.close)/r;return directional&&r>=Math.max(ar*1.35,1e-12)&&body(c)>=Math.max(ab*1.5,r*0.55)&&closeLocation>=0.70;}
function mostRecentPoiArrival(cs,lo,hi,lookback=80){const start=Math.max(1,cs.length-lookback);let arrival=null;for(let i=start;i<cs.length;i++){const now=overlap(cs[i],lo,hi),prev=overlap(cs[i-1],lo,hi);if(now&&!prev)arrival=i;}return arrival;}
function touchAnchorIndex(cs,item,lo,hi){const ts=n(item.poi_touch_start);if(Number.isFinite(ts)&&ts>0){for(let i=0;i<cs.length;i++){const end=cs[i].start+5*60*1000;if(end>ts)return i;}return null;}return mostRecentPoiArrival(cs,lo,hi,80);}
function nearestExpansionLiquidity(pv,entry,dir,fromI){const pool=(dir==='LONG'?pv.highs:pv.lows).filter(p=>p.i<fromI&&(dir==='LONG'?p.price>entry:p.price<entry));if(!pool.length)return null;pool.sort((a,b)=>Math.abs(a.price-entry)-Math.abs(b.price-entry));return pool[0];}

function findSetup(candles,item,tf){
  if(!Array.isArray(candles)||candles.length<30)return{ready:false,stage:'INSUFFICIENT_DATA',tf};
  const cs=candles.map(c=>({start:n(c.start),open:n(c.open),high:n(c.high),low:n(c.low),close:n(c.close)})).filter(c=>Object.values(c).every(finite));
  if(cs.length<30)return{ready:false,stage:'INSUFFICIENT_DATA',tf};
  const dir=item.direction,lo=n(item.poi_low),hi=n(item.poi_high),minimumRR=finite(item.minimum_rr)?Math.max(n(item.minimum_rr),0):1.5,poiI=touchAnchorIndex(cs,item,lo,hi);
  if(poiI===null)return{ready:false,stage:'WAIT_POI_TOUCH',tf};
  const poiTouchStart=finite(item.poi_touch_start)?n(item.poi_touch_start):cs[poiI].start,pv=pivots(cs,2,2);let sweep=null;
  if(dir==='LONG'){for(const p of pv.lows.filter(x=>x.i<poiI).reverse()){for(let i=Math.max(p.i+1,poiI);i<cs.length;i++)if(cs[i].low<p.price&&cs[i].close>p.price){sweep={i,level:p.price,extreme:cs[i].low,start:cs[i].start};break;}if(sweep)break;}}
  else{for(const p of pv.highs.filter(x=>x.i<poiI).reverse()){for(let i=Math.max(p.i+1,poiI);i<cs.length;i++)if(cs[i].high>p.price&&cs[i].close<p.price){sweep={i,level:p.price,extreme:cs[i].high,start:cs[i].start};break;}if(sweep)break;}}
  if(!sweep)return{ready:false,stage:'WAIT_SWEEP',tf,poi_touch_start:poiTouchStart};
  const opposing=(dir==='LONG'?pv.highs:pv.lows).filter(x=>x.i<sweep.i).reverse()[0];
  if(!opposing)return{ready:false,stage:'WAIT_MSS',tf,poi_touch_start:poiTouchStart,sweep};
  let mss=null;for(let i=sweep.i+1;i<cs.length;i++){const broke=dir==='LONG'?cs[i].close>opposing.price:cs[i].close<opposing.price;if(broke&&displacement(cs,i,dir)){mss={i,level:opposing.price,start:cs[i].start,close:cs[i].close};break;}}
  if(!mss)return{ready:false,stage:'WAIT_MSS_DISPLACEMENT',tf,poi_touch_start:poiTouchStart,sweep,structure_level:opposing.price};
  const postMss=cs.slice(mss.i+1,Math.min(cs.length,mss.i+3)),failed=postMss.some(c=>dir==='LONG'?c.close<mss.level:c.close>mss.level);
  if(failed)return{ready:false,stage:'MSS_FAILED_REACCEPTANCE',tf,poi_touch_start:poiTouchStart,sweep,mss};
  const zones=strictFvgs(cs,mss.i,dir).filter(x=>x.created_i>=mss.i&&(displacement(cs,x.impulse_i,dir)||x.created_i===mss.i||x.created_i===mss.i+1)).sort((a,b)=>a.created_i-b.created_i);
  if(!zones.length)return{ready:false,stage:'WAIT_STRICT_FVG',tf,poi_touch_start:poiTouchStart,sweep,mss};
  const latestI=cs.length-1;
  for(const z of zones){let firstTouchI=null;for(let i=z.created_i+1;i<cs.length;i++)if(overlap(cs[i],z.low,z.high)){firstTouchI=i;break;}if(firstTouchI===null||firstTouchI!==latestI)continue;const c=cs[firstTouchI],invalid=dir==='LONG'?c.low<=sweep.extreme:c.high>=sweep.extreme;if(invalid)continue;const entry=(z.low+z.high)/2,sl=sweep.extreme;if((dir==='LONG'&&!(sl<entry))||(dir==='SHORT'&&!(sl>entry)))return{ready:false,stage:'INVALID_SL_GEOMETRY',tf,poi_touch_start:poiTouchStart,sweep,mss,zone:z,entry_mid:entry,structural_sl:sl};const risk=Math.abs(entry-sl);if(risk<=0)continue;
    const target=nearestExpansionLiquidity(pv,entry,dir,firstTouchI);if(!target)return{ready:false,stage:'RR_BLOCKED_NO_TARGET',tf,poi_touch_start:poiTouchStart,sweep,mss,zone:z,entry_mid:entry,structural_sl:sl,nearest_liquidity:null,live_rr:null,minimum_rr:minimumRR};const roomR=Math.abs(target.price-entry)/risk;if(!Number.isFinite(roomR)||roomR<minimumRR)return{ready:false,stage:'RR_BLOCKED',tf,poi_touch_start:poiTouchStart,sweep,mss,zone:z,entry_mid:entry,structural_sl:sl,nearest_liquidity:target.price,live_rr:roomR,minimum_rr:minimumRR};
    const tp1R=Math.min(2,roomR),tp2R=Math.min(3,roomR),tp1=dir==='LONG'?entry+risk*tp1R:entry-risk*tp1R,tp2=dir==='LONG'?entry+risk*tp2R:entry-risk*tp2R,cycleId=`${item.symbol}:${dir}:${tf}:${poiTouchStart}:${sweep.start}:${mss.start}`;
    return{ready:true,stage:'ENTRY_CANDIDATE',tf,direction:dir,score:item.score,poi:[lo,hi],poi_touch_start:poiTouchStart,sweep,mss,zone:z,retrace_start:c.start,entry_zone:[z.low,z.high],entry_mid:entry,structural_sl:sl,tp1,tp2,rr_tp1:tp1R,rr_tp2:tp2R,nearest_liquidity:target.price,live_rr:roomR,expansion_room_r:roomR,minimum_rr:minimumRR,cycle_id:cycleId,event_id:cycleId};
  }
  return{ready:false,stage:'WAIT_FIRST_FVG_RETRACE',tf,poi_touch_start:poiTouchStart,sweep,mss,zones:zones.slice(-3)};
}

export function evaluateEntryTrigger({candles5m,item}){if(!item||!['LONG','SHORT'].includes(item.direction)||item.poi_low==null||item.poi_high==null)return{ready:false,stage:'INVALID_ITEM'};const r5=findSetup(candles5m,item,'5m');return{ready:r5.ready,stage:r5.ready?'ENTRY_CANDIDATE':`5m:${r5.stage}`,trigger:r5.ready?r5:null,diagnostics:{m5:r5}};}
