// Strict ICT entry-trigger detector. Input candles must be CLOSED and oldest -> newest.
// POI is defined from 1H/15m context; entry confirmation is evaluated on CLOSED 5m candles only.
// Safety policy: one POI-arrival cycle -> one first FVG retracement candidate.
// POI arrival, sweep, MSS, or a reused/late retracement alone must never be treated as entry.

function finite(v){return Number.isFinite(Number(v));}
function n(v){return Number(v);}
function overlap(c,lo,hi){return c.high>=Math.min(lo,hi)&&c.low<=Math.max(lo,hi);}
function body(c){return Math.abs(c.close-c.open);}
function range(c){return Math.max(c.high-c.low,1e-12);}
function avg(xs){return xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:0;}

function pivots(candles,left=2,right=2){
  const highs=[],lows=[];
  for(let i=left;i<candles.length-right;i++){
    const c=candles[i]; let ph=true,pl=true;
    for(let j=i-left;j<=i+right;j++) if(j!==i){if(candles[j].high>=c.high)ph=false;if(candles[j].low<=c.low)pl=false;}
    if(ph)highs.push({i,price:c.high,start:c.start});
    if(pl)lows.push({i,price:c.low,start:c.start});
  }
  return {highs,lows};
}

function strictFvgs(candles,fromIndex,direction){
  const out=[];
  for(let i=Math.max(2,fromIndex);i<candles.length;i++){
    const a=candles[i-2],c=candles[i];
    if(direction==='LONG' && c.low>a.high) out.push({kind:'BULL_FVG',created_i:i,low:a.high,high:c.low,start:c.start,impulse_i:i-1});
    if(direction==='SHORT' && c.high<a.low) out.push({kind:'BEAR_FVG',created_i:i,low:c.high,high:a.low,start:c.start,impulse_i:i-1});
  }
  return out;
}

function displacement(candles,i,direction){
  if(i<5||i>=candles.length)return false;
  const c=candles[i],prior=candles.slice(i-5,i),r=range(c);
  const ar=avg(prior.map(range)),ab=avg(prior.map(body));
  const directional=direction==='LONG'?c.close>c.open:c.close<c.open;
  const closeLocation=direction==='LONG'?(c.close-c.low)/r:(c.high-c.close)/r;
  return directional &&
    r>=Math.max(ar*1.35,1e-12) &&
    body(c)>=Math.max(ab*1.5,r*0.55) &&
    closeLocation>=0.70;
}

function mostRecentPoiArrival(cs,lo,hi,lookback=80){
  const start=Math.max(1,cs.length-lookback);
  let arrival=null;
  for(let i=start;i<cs.length;i++){
    const now=overlap(cs[i],lo,hi),prev=overlap(cs[i-1],lo,hi);
    if(now&&!prev)arrival=i;
  }
  return arrival;
}

function findSetup(candles,item,tf){
  if(!Array.isArray(candles)||candles.length<30)return {ready:false,stage:'INSUFFICIENT_DATA',tf};
  const cs=candles.map(c=>({start:n(c.start),open:n(c.open),high:n(c.high),low:n(c.low),close:n(c.close)})).filter(c=>Object.values(c).every(finite));
  if(cs.length<30)return {ready:false,stage:'INSUFFICIENT_DATA',tf};

  const dir=item.direction,lo=n(item.poi_low),hi=n(item.poi_high);
  const poiI=mostRecentPoiArrival(cs,lo,hi,80);
  if(poiI===null)return {ready:false,stage:'WAIT_POI_ARRIVAL',tf};

  const pv=pivots(cs,2,2);
  let sweep=null;
  if(dir==='LONG'){
    for(const p of pv.lows.filter(x=>x.i<poiI).reverse()){
      for(let i=Math.max(p.i+1,poiI);i<cs.length;i++){
        if(cs[i].low<p.price && cs[i].close>p.price){sweep={i,level:p.price,extreme:cs[i].low,start:cs[i].start};break;}
      }
      if(sweep)break;
    }
  } else {
    for(const p of pv.highs.filter(x=>x.i<poiI).reverse()){
      for(let i=Math.max(p.i+1,poiI);i<cs.length;i++){
        if(cs[i].high>p.price && cs[i].close<p.price){sweep={i,level:p.price,extreme:cs[i].high,start:cs[i].start};break;}
      }
      if(sweep)break;
    }
  }
  if(!sweep)return {ready:false,stage:'WAIT_SWEEP',tf,poi_arrival_start:cs[poiI].start};

  const opposing=(dir==='LONG'?pv.highs:pv.lows).filter(x=>x.i<sweep.i).reverse()[0];
  if(!opposing)return {ready:false,stage:'WAIT_MSS',tf,poi_arrival_start:cs[poiI].start,sweep};

  let mss=null;
  for(let i=sweep.i+1;i<cs.length;i++){
    const broke=dir==='LONG'?cs[i].close>opposing.price:cs[i].close<opposing.price;
    if(broke&&displacement(cs,i,dir)){mss={i,level:opposing.price,start:cs[i].start,close:cs[i].close};break;}
  }
  if(!mss)return {ready:false,stage:'WAIT_MSS_DISPLACEMENT',tf,poi_arrival_start:cs[poiI].start,sweep,structure_level:opposing.price};

  // Conservative mode: FVG-only until OB validation is made structurally robust.
  const zones=strictFvgs(cs,mss.i,dir)
    .filter(x=>x.created_i>=mss.i && (displacement(cs,x.impulse_i,dir)||x.created_i===mss.i||x.created_i===mss.i+1))
    .sort((a,b)=>a.created_i-b.created_i);
  if(!zones.length)return {ready:false,stage:'WAIT_STRICT_FVG',tf,poi_arrival_start:cs[poiI].start,sweep,mss};

  const latestI=cs.length-1;
  for(const z of zones){
    // Entry is valid only on the FIRST post-creation touch, and only if that first touch is the latest closed 5m candle.
    let firstTouchI=null;
    for(let i=z.created_i+1;i<cs.length;i++){
      if(overlap(cs[i],z.low,z.high)){firstTouchI=i;break;}
    }
    if(firstTouchI===null)continue;
    if(firstTouchI!==latestI)continue;

    const c=cs[firstTouchI];
    const invalid=dir==='LONG'?c.low<=sweep.extreme:c.high>=sweep.extreme;
    if(invalid)continue;

    const entry=(z.low+z.high)/2,sl=sweep.extreme,risk=Math.abs(entry-sl);
    if(risk<=0)continue;
    const tp1=dir==='LONG'?entry+risk*2:entry-risk*2;
    const tp2=dir==='LONG'?entry+risk*3:entry-risk*3;
    const cycleId=`${item.symbol}:${dir}:${tf}:${cs[poiI].start}:${sweep.start}:${mss.start}`;

    return {
      ready:true,stage:'ENTRY_CANDIDATE',tf,direction:dir,score:item.score,
      poi:[lo,hi],poi_arrival_start:cs[poiI].start,sweep,mss,zone:z,
      retrace_start:c.start,entry_zone:[z.low,z.high],entry_mid:entry,
      structural_sl:sl,tp1,tp2,rr_tp1:2,rr_tp2:3,
      cycle_id:cycleId,event_id:cycleId
    };
  }

  return {ready:false,stage:'WAIT_FIRST_FVG_RETRACE',tf,poi_arrival_start:cs[poiI].start,sweep,mss,zones:zones.slice(-3)};
}

export function evaluateEntryTrigger({candles5m,item}){
  if(!item||!['LONG','SHORT'].includes(item.direction)||item.poi_low==null||item.poi_high==null)return {ready:false,stage:'INVALID_ITEM'};
  const r5=findSetup(candles5m,item,'5m');
  return {ready:r5.ready,stage:r5.ready?'ENTRY_CANDIDATE':`5m:${r5.stage}`,trigger:r5.ready?r5:null,diagnostics:{m5:r5}};
}
