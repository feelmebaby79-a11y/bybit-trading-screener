#!/usr/bin/env python3
import json, sys
from pathlib import Path

WATCH = Path('latest/watchlist.json')
PREV = Path('/tmp/previous_watchlist.json')


def load(p):
    return json.loads(Path(p).read_text())

def key(x):
    return x.get('poi_id') or (x.get('symbol'), x.get('direction'), x.get('poi_tf'), x.get('poi_low'), x.get('poi_high'))

def touched(x):
    return bool(x.get('poi_touch_start'))

def terminal(x):
    return bool(x.get('consumed')) or x.get('status') in {'INVALIDATED','CONSUMED','CLOSED'}

def main():
    cur=load(WATCH)
    if cur.get('version',0) < 16: raise SystemExit('watchlist version must be >=16')
    pol=cur.get('policy',{})
    if not pol.get('persistent_after_poi_touch'): raise SystemExit('persistent_after_poi_touch must be true')
    items=cur.get('items',[])
    ids=[key(x) for x in items if key(x)]
    if len(ids)!=len(set(map(str,ids))): raise SystemExit('duplicate POI/setup identity detected')
    for x in items:
        if x.get('bucket')=='PERSISTENT_SETUP':
            if not x.get('persistent_setup'): raise SystemExit(f"{x.get('symbol')}: persistent bucket missing persistent_setup")
            if not touched(x): raise SystemExit(f"{x.get('symbol')}: persistent setup missing poi_touch_start")
            if terminal(x): raise SystemExit(f"{x.get('symbol')}: terminal setup must not remain persistent")
    if PREV.exists():
        prev=load(PREV)
        curkeys={str(key(x)) for x in items}
        missing=[]
        for x in prev.get('items',[]):
            # Any touched, non-terminal ranked/persistent setup must survive rebuild.
            if touched(x) and not terminal(x) and x.get('bucket') in {'EARLY_MOMENTUM','DAILY_RECOMMENDATION','PERSISTENT_SETUP'}:
                if str(key(x)) not in curkeys:
                    missing.append(f"{x.get('symbol')}:{x.get('direction')}:{x.get('poi_id')}")
        if missing: raise SystemExit('lifecycle regression: touched setups dropped: '+', '.join(missing))
    print(f"lifecycle validation PASS: {len(items)} items, persistent={sum(bool(x.get('persistent_setup')) for x in items)}")

if __name__=='__main__': main()
