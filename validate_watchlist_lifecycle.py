#!/usr/bin/env python3
import csv, json
from pathlib import Path

WATCH=Path('latest/watchlist.json')
SCAN=Path('latest/scan_results.csv')

def load(p): return json.loads(Path(p).read_text())
def expected_from_builder_output(cur):
    # The builder is authoritative: validate that the current ranked buckets exist and are unique.
    return {(x.get('symbol'),x.get('direction'),x.get('bucket')) for x in cur.get('items',[]) if x.get('bucket') in {'DAILY_RECOMMENDATION','EARLY_MOMENTUM'}}

def main():
    cur=load(WATCH); items=cur.get('items',[]); pol=cur.get('policy',{})
    if cur.get('version',0)<16: raise SystemExit('watchlist version must be >=16')
    daily=[x for x in items if x.get('bucket')=='DAILY_RECOMMENDATION']
    early=[x for x in items if x.get('bucket')=='EARLY_MOMENTUM']
    if len(daily)!=pol.get('daily_recommendation_count'): raise SystemExit('daily recommendation count mismatch')
    if len(early)!=pol.get('early_momentum_count'): raise SystemExit('early momentum count mismatch')
    if len(daily)>pol.get('daily_recommendation_max',3): raise SystemExit('too many daily recommendations')
    ranked=[(x.get('symbol'),x.get('direction')) for x in daily+early]
    if len(ranked)!=len(set(ranked)): raise SystemExit('duplicate current recommendation in watchlist')
    # Current recommendations are dynamic. Old recommendations MUST be allowed to leave the ranked buckets.
    # ENTRY is therefore always driven by this newly built watchlist, never by yesterday/previous ranking.
    for x in daily+early:
        if x.get('status')!='ACTIVE': raise SystemExit(f"current recommendation inactive: {x.get('symbol')}")
    print('watchlist recommendation validation PASS:', {'daily':ranked[:len(daily)], 'early':ranked[len(daily):]})

if __name__=='__main__': main()
