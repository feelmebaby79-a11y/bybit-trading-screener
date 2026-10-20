import json,urllib.request,time,datetime,collections,os,csv
from zoneinfo import ZoneInfo
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT']; MS=900000; END=int(time.time()*1000)//MS*MS; START=END-90*86400000
def load(sym):
 d={}
 path=f'backtest_data/{sym}.csv'
 if os.path.exists(path):
  with open(path) as fh:
   for line in fh:
    x=line.strip().split(',')
    if len(x)<5 or not x[0].isdigit():continue
    t=int(x[0]);
    if START<=t<END:d[t]=(t,float(x[1]),float(x[2]),float(x[3]),float(x[4]))
  b=[d[k] for k in sorted(d)]
  if len(b)<8000:raise ValueError('insufficient proxy history '+str(len(b)))
  return b
 end=END
 while end>=START:
  u=f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=1000&end={end}'
  with urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'ICT-backtest'}),timeout=30) as r:j=json.load(r)
  if j.get('retCode')!=0:raise ValueError(j)
  a=j['result']['list']
  if not a:break
  for x in a:
   t=int(x[0])
   if START<=t<END:d[t]=(t,*map(float,x[1:5]))
  oldest=min(int(x[0]) for x in a)
  if oldest<=START:break
  end=oldest-1;time.sleep(.08)
 b=[d[k] for k in sorted(d)]
 if len(b)<8000:raise ValueError('insufficient history '+str(len(b)))
 return b
def run(sym,b):
 # Seven independently triggered ICT concept models. 15m candles; no arbitrary holding-time exit. Not verified verbatim ICT rules.
 out=[]; n=len(b); used=set()
 def pivot(i,kind,span=3):
  if i<span*2:return False
  k=i-span
  v=b[k][2 if kind=='H' else 3]
  return (v== (max(x[2] for x in b[k-span:i+1]) if kind=='H' else min(x[3] for x in b[k-span:i+1])))
 def submit(model,i,d,entry,sl,tp,reason):
  if not (0<i<n-1 and (entry-sl)*d>0 and (tp-entry)*d>0):return
  key=(model,i,d)
  if key in used:return
  used.add(key)
  fill=None
  for j in range(i+1,min(i+13,n)):
   if (b[j][3]<=sl if d==1 else b[j][2]>=sl):break
   if b[j][3]<=entry<=b[j][2]:fill=j;break
  if fill is None:return
  result=None
  for j in range(fill+1,n):
   stop=b[j][3]<=sl if d==1 else b[j][2]>=sl
   target=b[j][2]>=tp if d==1 else b[j][3]<=tp
   if stop:result=-1;break
   if target:result=(tp-entry)*d/((entry-sl)*d);break
  if result is None:
   result=(b[-1][4]-entry)*d/abs(entry-sl)
   status='OPEN_MARK_TO_MARKET'
  else:status='CLOSED'
  out.append(dict(symbol=sym,model=model,signal_utc=datetime.datetime.fromtimestamp(b[i][0]/1000,datetime.timezone.utc).isoformat(),side='LONG' if d==1 else 'SHORT',entry=round(entry,8),sl=round(sl,8),tp=round(tp,8),status=status,gross_r=round(result,5),net_r=round(result-.0012*entry/abs(entry-sl),5),reason=reason))
 highs=[];lows=[]; fvgs=[]; obs=[]; breakers=[]; sweeps=[]
 for i in range(60,n-1):
  t,o,h,l,c=b[i];prev=b[i-1]; d=1 if c>o else -1
  if pivot(i,'H'):highs.append((i-3,b[i-3][2]))
  if pivot(i,'L'):lows.append((i-3,b[i-3][3]))
  highs=highs[-30:];lows=lows[-30:]
  # Price delivery: 3-candle FVG; no model-wide filter.
  if l>b[i-2][2]:fvgs.append((i,1,b[i-2][2],l))
  if h<b[i-2][3]:fvgs.append((i,-1,h,b[i-2][3]))
  fvgs=[x for x in fvgs if i-x[0]<=48]
  # Confirmed liquidity raid, independently used only by models requiring it.
  if lows and l<lows[-1][1]<c:sweeps.append((i,1,l))
  if highs and h>highs[-1][1]>c:sweeps.append((i,-1,h))
  sweeps=[x for x in sweeps if i-x[0]<=24]
  ny=datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).astimezone(ZoneInfo('America/New_York'))
  avg=sum(abs(x[4]-x[1]) for x in b[i-20:i])/20
  displacement=abs(c-o)>1.5*avg and abs(c-o)/max(h-l,1e-9)>.6
  # Order blocks: final opposing candle before displacement through a recent confirmed swing.
  if displacement:
   swing=(highs[-1][1] if highs else None) if d==1 else (lows[-1][1] if lows else None)
   if swing is not None and ((c>swing) if d==1 else (c<swing)):
    opp=next((b[j] for j in range(i-1,max(0,i-9),-1) if (b[j][4]<b[j][1] if d==1 else b[j][4]>b[j][1])),None)
    if opp:obs.append((i,d,opp[3],opp[2],False))
  obs=obs[-100:]
  # A broken opposing order block may become a breaker on retest.
  newobs=[]
  for formed,od,bot,top,broken in obs:
   if not broken and ((c<bot) if od==1 else (c>top)):
    breakers.append((i,-od,bot,top))
    broken=True
   newobs.append((formed,od,bot,top,broken))
  obs=newobs;breakers=[x for x in breakers if i-x[0]<=48]
  # ICT 2022: liquidity raid -> displacement through short-term structure -> fresh FVG.
  for si,sd,ext in sweeps:
   if sd!=d or not displacement or i<=si or i-si>12:continue
   structure=max(x[2] for x in b[si:i]) if d==1 else min(x[3] for x in b[si:i])
   if (c<=structure if d==1 else c>=structure):continue
   fresh=next((x for x in fvgs[::-1] if x[0]==i and x[1]==d),None)
   if not fresh:continue
   entry=(fresh[2]+fresh[3])/2
   pool=next((v for _,v in reversed(highs if d==1 else lows) if (v>entry if d==1 else v<entry)),None)
   if pool is not None:submit('ICT_2022',i,d,entry,ext,pool,'raid_MSS_FVG_to_opposing_liquidity')
  # Breaker: failed opposing OB, retest of its price zone; structural invalidation and swing target.
  for bi,bd,bot,top in breakers:
   if i<=bi or i-bi>24:continue
   if not (l<=top and h>=bot):continue
   entry=(bot+top)/2;sl=bot if bd==1 else top
   target=next((v for _,v in reversed(highs if bd==1 else lows) if (v>entry if bd==1 else v<entry)),None)
   if target is not None:submit('BREAKER',i,bd,entry,sl,target,'invalidated_OB_retest_to_liquidity')
  # Unicorn: overlap of confirmed breaker zone and same-direction FVG.
  for bi,bd,bot,top in breakers:
   if i-bi>24:continue
   for fi,fd,lo,hi in fvgs:
    if fd!=bd or fi<bi or i-fi>12:continue
    a=max(bot,lo);z=min(top,hi)
    if a>=z:continue
    entry=(a+z)/2;sl=bot if bd==1 else top
    target=next((v for _,v in reversed(highs if bd==1 else lows) if (v>entry if bd==1 else v<entry)),None)
    if target is not None:submit('UNICORN',i,bd,entry,sl,target,'breaker_FVG_overlap_to_liquidity')
  # OTE: retracement to 62-79% of an independently identified completed swing.
  for direction in (1,-1):
   pts=sorted([(k,v,'H') for k,v in highs]+[(k,v,'L') for k,v in lows])
   if len(pts)<2:continue
   p2=pts[-1]
   p1=next((x for x in reversed(pts[:-1]) if x[2]!=p2[2]),None)
   if p1 is None or i-p2[0]>36:continue
   if direction==1 and not(p1[2]=='L' and p2[2]=='H'):continue
   if direction==-1 and not(p1[2]=='H' and p2[2]=='L'):continue
   length=abs(p2[1]-p1[1])
   if length<=0:continue
   entry=p2[1]-direction*.705*length;sl=p1[1]
   target=p2[1]
   if (l<=entry<=h):submit('OTE',i,direction,entry,sl,target,'confirmed_swing_70.5pct_retracement')
  # Silver Bullet: NY 10-11 or 14-15, session displacement FVG, nearest opposing liquidity.
  if ny.hour in (10,14) and displacement:
   fresh=next((x for x in fvgs[::-1] if x[0]==i and x[1]==d),None)
   if fresh:
    entry=(fresh[2]+fresh[3])/2
    sl=min(x[3] for x in b[i-8:i+1]) if d==1 else max(x[2] for x in b[i-8:i+1])
    target=next((v for _,v in reversed(highs if d==1 else lows) if (v>entry if d==1 else v<entry)),None)
    if target is not None:submit('SILVER_BULLET',i,d,entry,sl,target,'NY_window_displacement_FVG_to_liquidity')
  # Mitigation block: return to unbroken OB after displacement, structural stop.
  for formed,od,bot,top,broken in obs[-20:]:
   if broken or i<=formed or i-formed>36 or not(l<=top and h>=bot):continue
   entry=(bot+top)/2;sl=bot if od==1 else top
   target=next((v for _,v in reversed(highs if od==1 else lows) if (v>entry if od==1 else v<entry)),None)
   if target is not None:submit('MITIGATION_BLOCK',i,od,entry,sl,target,'return_to_unbroken_OB_to_liquidity')
  # OB continuation: pullback to intact OB in same swing-break direction.
  for formed,od,bot,top,broken in obs[-20:]:
   if broken or i<=formed or i-formed>18:continue
   if not (l<=top and h>=bot):continue
   entry=(bot+top)/2;sl=bot if od==1 else top
   target=next((v for _,v in reversed(highs if od==1 else lows) if (v>entry if od==1 else v<entry)),None)
   if target is not None:submit('OB_CONTINUATION',i,od,entry,sl,target,'swing_break_OB_retest_to_liquidity')
 return out
def main():
 trades=[];errors={}
 for sym in SYMS:
  try:
   b=load(sym);print(sym,'candles',len(b),flush=True);trades+=run(sym,b)
  except Exception as e:errors[sym]=str(e);print('ERROR',sym,e,flush=True)
 groups=collections.defaultdict(list)
 for t in trades:
  if t['status']=='CLOSED':groups[(t['model'],t['symbol'])].append(t)
 stats=[]
 for (model,sym),a in sorted(groups.items()):
  stats.append(dict(model=model,symbol=sym,trades=len(a),wins=sum(x['net_r']>0 for x in a),win_rate_pct=round(100*sum(x['net_r']>0 for x in a)/len(a),2),avg_net_r=round(sum(x['net_r'] for x in a)/len(a),4)))
 os.makedirs('backtest_results',exist_ok=True)
 with open('backtest_results/trades.csv','w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['symbol','model','signal_utc','side','entry','sl','tp','gross_r','net_r','reason','status']);w.writeheader();w.writerows(trades)
 report=dict(timeframe='15m',open_positions=sum(t['status']=='OPEN_MARK_TO_MARKET' for t in trades),created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),period_days=90,source='Binance Vision USD-M futures 15m proxy, not Bybit',swing_pivot=50,roundtrip_fee=.0012,filters=['Independent model triggers; structural stops; opposing swing liquidity targets'],limitations=['ICT concept interpretation, NOT verified verbatim against all original Inner Circle Trader lessons','No lookahead; 50-bar pivots confirmed after 50 bars','Stop-first intrabar ambiguity','Overlapping trades counted independently','No funding or slippage'],errors=errors,total_trades=len(trades),closed_trades=sum(t['status']=='CLOSED' for t in trades),stats=stats)
 with open('backtest_results/report.json','w') as f:json.dump(report,f,indent=2)
 print('RESULT',json.dumps(report),flush=True)
 if errors or not trades:raise SystemExit(1)
if __name__=='__main__':main()
