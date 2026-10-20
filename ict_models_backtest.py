import json,urllib.request,time,datetime,collections,os,csv
from zoneinfo import ZoneInfo
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT']; MS=300000; END=int(time.time()*1000)//MS*MS; START=END-90*86400000
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
  if len(b)<18000:raise ValueError('insufficient proxy history '+str(len(b)))
  return b
 end=END
 while end>=START:
  u=f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=5&limit=1000&end={end}'
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
 if len(b)<18000:raise ValueError('insufficient history '+str(len(b)))
 return b
def run(sym,b):
 out=[]; events=[]; used=set(); high=None;low=None; htf_ema=None
 for i in range(110,len(b)-290):
  t,o,h,l,c=b[i]; side=1 if c>o else -1
  if i%12==11:
   htf_ema=c if htf_ema is None else htf_ema+(2/51)*(c-htf_ema)
  if htf_ema is None:continue
  if i>=100:
   k=i-50;window=b[k-50:i+1]
   if b[k][2]==max(x[2] for x in window):high=b[k][2]
   if b[k][3]==min(x[3] for x in window):low=b[k][3]
  if low is not None and l<low<c:events.append((i,1,l))
  if high is not None and h>high>c:events.append((i,-1,h))
  events=[e for e in events if i-e[0]<=24]
  avg=sum(abs(x[4]-x[1]) for x in b[i-20:i])/20
  if abs(c-o)<avg*1.5 or abs(c-o)/max(h-l,1e-9)<.6:continue
  if (c-htf_ema)*side<=0:continue
  recent=b[i-99:i+1]; rh=max(x[2] for x in recent);rl=min(x[3] for x in recent)
  if side==1 and b[i-1][4]>(rh+rl)/2:continue
  if side==-1 and b[i-1][4]<(rh+rl)/2:continue
  for sw,d,ext in events:
   if sw>=i or d!=side:continue
   if d==1 and c<=max(x[2] for x in b[sw:i]):continue
   if d==-1 and c>=min(x[3] for x in b[sw:i]):continue
   fvg=(b[i-2][2],l) if d==1 and l>b[i-2][2] else ((h,b[i-2][3]) if d==-1 and h<b[i-2][3] else None)
   if not fvg:continue
   opp=[x for x in b[max(0,sw-8):sw+1] if (x[4]<x[1] if d==1 else x[4]>x[1])]
   models=['ICT_2022_PROXY']
   if opp and min(fvg[1],opp[-1][2])>max(fvg[0],opp[-1][3]):models.append('UNICORN_PROXY')
   if opp and (c>opp[-1][2] if d==1 else c<opp[-1][3]):models.append('BREAKER_PROXY')
   mid=sum(fvg)/2;imp=abs(c-ext)
   if imp>0 and .62<=(c-mid)*d/imp<=.79:models.append('OTE_CONFLUENCE')
   ny=datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).astimezone(ZoneInfo('America/New_York'))
   if ny.hour in (10,14):models.append('SILVER_BULLET_PROXY')
   entry=mid;sl=ext;risk=(entry-sl)*d
   if risk<=0:continue
   for model in models:
    key=(model,sw,d)
    if key in used:continue
    used.add(key);fill=None
    for k in range(i+1,i+13):
     x=b[k]
     if (x[3]<=sl if d==1 else x[2]>=sl):break
     if x[3]<=entry<=x[2]:fill=k;break
    if fill is None:continue
    tp=entry+d*2*risk;res=None
    for k in range(fill+1,fill+289):
     x=b[k];stop=x[3]<=sl if d==1 else x[2]>=sl;target=x[2]>=tp if d==1 else x[3]<=tp
     if stop:res=-1;break
     if target:res=2;break
    if res is None:continue
    out.append(dict(symbol=sym,model=model,signal_utc=datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).isoformat(),side='LONG' if d==1 else 'SHORT',entry=entry,sl=sl,tp=tp,gross_r=res,net_r=res-.0012*entry/risk))
 return out
def main():
 trades=[];errors={}
 for sym in SYMS:
  try:
   b=load(sym);print(sym,'candles',len(b),flush=True);trades+=run(sym,b)
  except Exception as e:errors[sym]=str(e);print('ERROR',sym,e,flush=True)
 groups=collections.defaultdict(list)
 for t in trades:groups[(t['model'],t['symbol'])].append(t)
 stats=[]
 for (model,sym),a in sorted(groups.items()):
  stats.append(dict(model=model,symbol=sym,trades=len(a),wins=sum(x['net_r']>0 for x in a),win_rate_pct=round(100*sum(x['net_r']>0 for x in a)/len(a),2),avg_net_r=round(sum(x['net_r'] for x in a)/len(a),4)))
 os.makedirs('backtest_results',exist_ok=True)
 with open('backtest_results/trades.csv','w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['symbol','model','signal_utc','side','entry','sl','tp','gross_r','net_r']);w.writeheader();w.writerows(trades)
 report=dict(created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),period_days=90,source='Binance Vision USD-M futures 5m proxy, not Bybit',swing_pivot=50,roundtrip_fee=.0012,filters=['1H EMA50 direction proxy','100-bar dealing-range half discount/premium','first FVG retest within 12 bars'],limitations=['ICT-inspired proxy, not exact LuxAlgo source','No lookahead; 50-bar pivots confirmed after 50 bars','Stop-first intrabar ambiguity','Overlapping trades counted independently','No funding or slippage'],errors=errors,total_trades=len(trades),stats=stats)
 with open('backtest_results/report.json','w') as f:json.dump(report,f,indent=2)
 print('RESULT',json.dumps(report),flush=True)
 if errors or not trades:raise SystemExit(1)
if __name__=='__main__':main()
