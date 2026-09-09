import fs from 'node:fs';

const DAY=86400000;
const INITIAL=1000;
const LEVERAGE=10;
const TRADE_RISK_PCT=0.01;
const SYMBOLS=['BTCUSDT','ETHUSDT','BNBUSDT'];
const START=Date.parse('2026-08-10T00:00:00Z');
const END=Date.parse('2026-09-09T23:59:59Z');
const CAPS=[0.02,0.03];

function parseLog(s){
  const txt=fs.readFileSync(`result_${s}.log`,'utf8');
  const a=txt.indexOf('BACKTEST_RESULT_START'),b=txt.indexOf('BACKTEST_RESULT_END');
  if(a<0||b<0) throw new Error(`missing result ${s}`);
  return JSON.parse(txt.slice(a+'BACKTEST_RESULT_START'.length,b).trim());
}
function parseCsv(s){
  const out=[];
  for(const line of fs.readFileSync(`${s}_5m.csv`,'utf8').trim().split(/\r?\n/)){
    const x=line.split(','); if(!/^\d+$/.test(x[0]||'')) continue;
    const c={start:+x[0],open:+x[1],high:+x[2],low:+x[3],close:+x[4]};
    if(Object.values(c).every(Number.isFinite)) out.push(c);
  }
  return [...new Map(out.map(c=>[c.start,c])).values()].sort((a,b)=>a.start-b.start);
}
function recalcDay(t,bars){
  const dir=t.direction,e=t.entry,sl=t.sl,risk=Math.abs(e-sl),tp=dir==='LONG'?e+3*risk:e-3*risk;
  const fi=bars.findIndex(c=>c.start===t.fill);
  if(fi<0||!(risk>0)||!(e>0)) return null;
  if((dir==='LONG'&&!(sl<e))||(dir==='SHORT'&&!(sl>e))) return null;
  const done=(r,exit,reason,px)=>({...t,r,exit,exit_reason:reason,hold_days:(exit-t.fill)/DAY,exit_price:px,price_return:dir==='LONG'?(px-e)/e:(e-px)/e,stop_pct:risk/e});
  const endT=Math.min(t.fill+DAY,END);
  for(let i=fi+1;i<bars.length&&bars[i].start<=endT;i++){
    const c=bars[i],sh=dir==='LONG'?c.low<=sl:c.high>=sl,th=dir==='LONG'?c.high>=tp:c.low<=tp;
    if(sh&&th) return done(-1,c.start,'AMBIGUOUS_SL_FIRST',sl);
    if(th) return done(3,c.start,'TP3R',tp);
    if(sh) return done(-1,c.start,'SL',sl);
  }
  const end=bars.find(c=>c.start>=endT)||bars.filter(c=>c.start<=END).at(-1);
  if(!end) return null;
  const rr=(dir==='LONG'?(end.close-e):(e-end.close))/risk;
  return done(rr,end.start,'TIME_24H',end.close);
}
function dedupe(xs){
  const m=new Map();
  for(const x of xs){
    const k=`${x.symbol}:${x.type}:${x.signal}:${x.direction}:${x.fill}:${Number(x.entry).toFixed(8)}:${Number(x.sl).toFixed(8)}`;
    if(!m.has(k)) m.set(k,x);
  }
  return [...m.values()];
}

const candidates=[];
for(const s of SYMBOLS){
  const j=parseLog(s),bars=parseCsv(s);
  const day=dedupe((j.day_trades||[]).map(x=>({...x,symbol:s})))
    .filter(x=>x.fill>=START&&x.fill<=END)
    .map(x=>recalcDay(x,bars)).filter(Boolean);
  candidates.push(...day);
}
candidates.sort((a,b)=>a.fill-b.fill||a.symbol.localeCompare(b.symbol));

function simulate(capPct){
  let equity=INITIAL,peak=INITIAL,mdd=0,maxConcurrent=0,blockedRisk=0,blockedMargin=0;
  const open=[],accepted=[],equityEvents=[];
  const contrib=Object.fromEntries(SYMBOLS.map(s=>[s,{trades:0,wins:0,losses:0,pnl:0,r_sum:0}]));

  function settleUntil(ts){
    while(true){
      let idx=-1,min=Infinity;
      for(let i=0;i<open.length;i++) if(open[i].exit<=ts&&open[i].exit<min){min=open[i].exit;idx=i;}
      if(idx<0) break;
      const p=open.splice(idx,1)[0];
      equity+=p.pnl;
      peak=Math.max(peak,equity);
      mdd=Math.max(mdd,(peak-equity)/peak);
      equityEvents.push({time:p.exit,equity,symbol:p.symbol,pnl:p.pnl});
    }
  }

  for(const t of candidates){
    settleUntil(t.fill);
    if(equity<=0) break;
    const openRisk=open.reduce((s,p)=>s+p.risk_dollars,0);
    const usedMargin=open.reduce((s,p)=>s+p.margin,0);
    const intendedRisk=equity*TRADE_RISK_PCT;
    const desiredNotional=intendedRisk/t.stop_pct;
    const availableMargin=Math.max(0,equity-usedMargin);
    const notional=Math.min(desiredNotional,availableMargin*LEVERAGE);
    const margin=notional/LEVERAGE;
    const riskDollars=notional*t.stop_pct;
    if(!(notional>0)||riskDollars<=0){blockedMargin++;continue;}
    if(openRisk+riskDollars>equity*capPct+1e-9){blockedRisk++;continue;}
    const pnl=notional*t.price_return;
    const p={...t,notional,margin,risk_dollars:riskDollars,pnl};
    open.push(p);accepted.push(p);maxConcurrent=Math.max(maxConcurrent,open.length);
    const c=contrib[t.symbol];c.trades++;c.r_sum+=t.r;if(t.r>0)c.wins++;else c.losses++;c.pnl+=pnl;
  }
  settleUntil(Infinity);
  const wins=accepted.filter(x=>x.r>0).length;
  const activeDays=new Set(accepted.map(x=>new Date(x.fill).toISOString().slice(0,10)));
  const elapsedDays=Math.floor((END-START)/DAY)+1;
  return {
    cap_pct:capPct*100,
    trades:accepted.length,wins,losses:accepted.length-wins,
    win_rate:accepted.length?wins/accepted.length:null,
    avg_r:accepted.length?accepted.reduce((s,x)=>s+x.r,0)/accepted.length:null,
    final_equity:equity,return_pct:(equity/INITIAL-1)*100,
    realized_equity_max_drawdown_pct:mdd*100,
    max_concurrent_positions:maxConcurrent,
    blocked_by_risk_cap:blockedRisk,
    blocked_by_margin:blockedMargin,
    active_day_count:activeDays.size,
    elapsed_calendar_days:elapsedDays,
    day_signal_coverage_pct:activeDays.size/elapsedDays*100,
    avg_entries_per_calendar_day:accepted.length/elapsedDays,
    per_symbol:Object.fromEntries(Object.entries(contrib).map(([s,c])=>[s,{...c,win_rate:c.trades?c.wins/c.trades:null,avg_r:c.trades?c.r_sum/c.trades:null}]))
  };
}

console.log('TOP3_10X_RISKCAP_1M_START');
console.log(JSON.stringify({
  universe:SYMBOLS,
  period:{start:new Date(START).toISOString(),end:new Date(END).toISOString()},
  initial_equity:INITIAL,
  leverage:LEVERAGE,
  trade_risk_pct:TRADE_RISK_PCT*100,
  position_sizing:'1% equity structural-stop risk per trade, notional capped by 10x leverage and available margin',
  portfolio_risk_caps_pct:CAPS.map(x=>x*100),
  candidate_trades_before_portfolio_caps:candidates.length,
  notes:['MDD is based on realized-equity events, not intratrade mark-to-market drawdown','same-candle SL/TP ambiguity is treated conservatively as SL first'],
  results:Object.fromEntries(CAPS.map(c=>[`${Math.round(c*100)}pct`,simulate(c)]))
},null,2));
console.log('TOP3_10X_RISKCAP_1M_END');
