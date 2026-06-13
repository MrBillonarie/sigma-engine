#!/usr/bin/env python3
"""
SIGMA ENGINE - Agente 1: HRP Portfolio Constructor v2
Kelly per-slot escalado a [1.5%, 8.0%] por min-max normalizacion.
"""
import json, sys
from pathlib import Path
from collections import defaultdict
import numpy as np

BASE=Path('/opt/sigma')
OUT_FILE=BASE/'results/reports/kelly_weights.json'
KELLY_MIN=1.5
KELLY_MAX=8.0

def _pearson(a,b):
    n=min(len(a),len(b))
    if n<4: return 0.0
    a,b=np.array(a[-n:]),np.array(b[-n:])
    da,db=a.std(),b.std()
    if da<1e-9 or db<1e-9: return 0.0
    return float(np.clip(np.mean((a-a.mean())*(b-b.mean()))/(da*db),-1,1))

def _load_champs():
    snap={}
    try: snap=json.loads((BASE/'results/reports/port_snapshot.json').read_text())
    except: return {}
    champs={}
    for slot,strat_raw in snap.get('champions',{}).items():
        asset,tf=slot.split('|')
        strat=strat_raw.split('|')[0]
        tf_dir=BASE/f'models/{tf}'
        if not tf_dir.exists(): continue
        candidates=[tf_dir/f'{asset.lower()}_{strat.lower()}.json']+list(tf_dir.glob(f'{asset.lower()}_*.json'))
        for fp in candidates:
            if not fp.exists(): continue
            try:
                d=json.loads(fp.read_text()); moos=d.get('metrics_oos') or {}
                champs[slot]={'asset':asset,'tf':tf,'strategy':strat,
                    'wr':moos.get('win_rate',d.get('wr',50)) or 50,
                    'cagr':moos.get('cagr',d.get('cagr',0)) or 0,
                    'dd':abs(moos.get('max_dd',d.get('dd',20)) or 20),
                    'trades':moos.get('trades',d.get('n_trades',20)) or 20,
                    'score':d.get('robustness_final',d.get('canonical_score',0.5)) or 0.5}
                break
            except: pass
    return champs

def _load_returns():
    try: ts=json.loads((BASE/'results/trade_state.json').read_text())
    except: return {}
    by_slot=defaultdict(list)
    for t in ts.get('history',[]):
        slot=f"{t.get('sym','?')}|{t.get('tf','?')}"
        by_slot[slot].append(float(t.get('pnl_pct',0) or 0))
    return dict(by_slot)

def _build_corr(slots,champs,live_ret):
    n=len(slots); corr=np.eye(n)
    for i in range(n):
        for j in range(i+1,n):
            ri=live_ret.get(slots[i],[]); rj=live_ret.get(slots[j],[])
            c=_pearson(ri,rj) if len(ri)>=5 and len(rj)>=5 else (0.55 if champs[slots[i]]['asset']==champs[slots[j]]['asset'] else 0.12)
            corr[i,j]=corr[j,i]=c
    return corr*0.9+np.eye(n)*0.1

def _linkage_order(dist):
    n=dist.shape[0]; clusters=[[i] for i in range(n)]; dm=dist.copy(); np.fill_diagonal(dm,np.inf)
    while len(clusters)>1:
        bi,bj,bd=0,1,np.inf
        for a in range(len(clusters)):
            for b in range(a+1,len(clusters)):
                d=min(dm[x,y] for x in clusters[a] for y in clusters[b])
                if d<bd: bd=d; bi,bj=a,b
        clusters[bi]=clusters[bi]+clusters[bj]; clusters.pop(bj)
    return clusters[0]

def _hrp(corr,vols):
    n=len(vols); dist=np.sqrt(np.clip(0.5*(1.0-corr),0,1)); np.fill_diagonal(dist,0)
    order=_linkage_order(dist); weights=np.ones(n)
    def cv(idxs):
        w=1.0/(vols[idxs]+1e-9); w/=w.sum()
        return float(w@corr[np.ix_(idxs,idxs)]@w)+1e-9
    clusters=[order]
    while clusters:
        sub=clusters.pop(0)
        if len(sub)<=1: continue
        mid=len(sub)//2; L,R=np.array(sub[:mid]),np.array(sub[mid:])
        lv,rv=cv(L),cv(R); wL=rv/(lv+rv)
        weights[L]*=wL; weights[R]*=(1-wL)
        if len(L)>1: clusters.append(list(L))
        if len(R)>1: clusters.append(list(R))
    weights/=weights.sum(); return weights

def run():
    champs=_load_champs(); live_ret=_load_returns()
    if not champs:
        print('[HRP] Sin champions - flat 5%',flush=True); return {}
    slots=sorted(champs.keys()); n=len(slots)
    vols=np.array([np.std(live_ret[s]) if len(live_ret.get(s,[]))>=5 else (champs[s]['dd']/100)*(1+(100-champs[s]['wr'])/100) for s in slots])
    vols=np.clip(vols,1e-4,None)
    corr=_build_corr(slots,champs,live_ret)
    hrp=_hrp(corr,vols)
    # Apply score boost
    boost=np.array([0.8+champs[s].get('score',0.5)*0.4 for s in slots])
    raw=hrp*boost
    # Min-max normalize to [KELLY_MIN, KELLY_MAX]
    mn,mx=raw.min(),raw.max()
    if mx>mn:
        norm=(raw-mn)/(mx-mn)
    else:
        norm=np.ones(n)*0.5
    kelly=KELLY_MIN+norm*(KELLY_MAX-KELLY_MIN)
    result={s:round(float(kelly[i]),2) for i,s in enumerate(slots)}
    method='hrp_live' if any(len(live_ret.get(s,[]))>=5 for s in slots) else 'hrp_backtest'
    out={'computed_at':__import__('datetime').datetime.utcnow().isoformat(),'method':method,'n_champions':n,'kelly_min_pct':round(float(kelly.min()),2),'kelly_max_pct':round(float(kelly.max()),2),'kelly_avg_pct':round(float(kelly.mean()),2),'weights':result}
    OUT_FILE.parent.mkdir(parents=True,exist_ok=True)
    OUT_FILE.write_text(json.dumps(out,indent=2))
    print(f'[HRP] {n} champs | min={out["kelly_min_pct"]:.1f}% max={out["kelly_max_pct"]:.1f}% avg={out["kelly_avg_pct"]:.1f}% | {method}',flush=True)
    for s in sorted(result,key=lambda x:-result[x])[:6]:
        print(f'  {s}: {result[s]:.2f}%',flush=True)
    return result

if __name__=='__main__': run()
