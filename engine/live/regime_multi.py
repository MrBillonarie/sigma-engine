#!/usr/bin/env python3
"""
SIGMA ENGINE - Agente 2: Regimen Multi-Dimensional v2
Crypto: usa regimen global del API (no model flags)
Commodities: usa CSVs per-asset
"""
import json, math, sys, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/opt/sigma')
BASE      = Path('/opt/sigma')
OUT_FILE  = BASE / 'results/reports/regime_matrix.json'
CHILE     = timezone(timedelta(hours=-4))
ASSETS_M1 = ['BTC','ETH','SOL','BNB','LTC']
ASSETS_M2 = ['XAU','XAG','WTI','HG','NG','PL']
GLOBAL_MOM = {'BEAR':20.0,'NEUTRAL':50.0,'BULL':80.0}

def _load(path):
    try: return json.loads(Path(path).read_text())
    except: return {}

def _fetch(url, timeout=4):
    try: return json.loads(urllib.request.urlopen(url,timeout=timeout).read())
    except: return {}

def _rsi(closes,period=14):
    if len(closes)<period+1: return 50.0
    gains,losses=[],[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains[-period:])/period; al=sum(losses[-period:])/period
    if al<1e-9: return 100.0
    return round(100-100/(1+ag/al),1)

def _ema(prices,p):
    if not prices: return []
    k=2/(p+1); r=[prices[0]]
    for v in prices[1:]: r.append(v*k+r[-1]*(1-k))
    return r

def _read_csv_tail(path,n=250):
    rows=[]
    try:
        lines=Path(path).read_text().splitlines()
        h={c.lower().strip():i for i,c in enumerate(lines[0].split(','))}
        ci=h.get('close',h.get('c',4)); hi=h.get('high',h.get('h',2)); li=h.get('low',h.get('l',3))
        for line in lines[-(n+1):-1]:
            p=line.split(',')
            try: rows.append({'close':float(p[ci]),'high':float(p[hi]),'low':float(p[li])})
            except: pass
    except: pass
    return rows[-n:]

def _atr_pct(rows,period=14):
    if len(rows)<25: return 50.0
    def atr_at(r):
        trs=[max(r[i]['high']-r[i]['low'],abs(r[i]['high']-r[i-1]['close']),abs(r[i]['low']-r[i-1]['close'])) for i in range(1,len(r))]
        return sum(trs[-period:])/min(period,len(trs))/r[-1]['close']*100
    atr_now=atr_at(rows[-20:])
    hist=[atr_at(rows[max(0,i-20):i]) for i in range(30,len(rows),5)]
    if not hist: return 50.0
    return round(sum(1 for a in hist if a<atr_now)/len(hist)*100,1)

def _macro(asset,btcd,lsr_data,fg_data):
    score=50.0
    fr=_load(BASE/'results/derivatives/funding_rate.json')
    if isinstance(fr,dict):
        v=fr.get(asset,fr.get('BTC',{}))
        v=v.get('value',v.get('rate',0)) if isinstance(v,dict) else (v if isinstance(v,(int,float)) else 0)
        score+=min(max(float(v)*1000,-15),15)
    if isinstance(lsr_data,dict):
        v=lsr_data.get(asset,lsr_data.get('BTC',{}))
        v=v.get('longShortRatio',v.get('value',1.0)) if isinstance(v,dict) else (v if isinstance(v,(int,float)) else 1.0)
        score+=min(max((float(v)-1.0)*20,-10),10)
    if isinstance(fg_data,dict):
        fg=fg_data.get('value',fg_data.get('fear_greed',50)) or 50
        try: fg=float(fg)
        except: fg=50
        score+=(fg-50)*0.3
    if asset!='BTC' and btcd>0: score-=(btcd-0.5)*30
    return max(0,min(100,round(score,1)))

def _label_mult(mom,vol,corr,macro):
    trend='BULL' if mom>=65 else ('BEAR' if mom<=35 else 'NEUTRAL')
    vol_tag='_VOLATILE' if vol>=70 else ('_QUIET' if vol<=30 else '')
    label=trend+vol_tag
    if trend=='BULL' and vol<=50: mult=1.25
    elif trend=='BULL': mult=1.05
    elif trend=='NEUTRAL': mult=0.90
    elif trend=='BEAR' and vol<=50: mult=0.70
    else: mult=0.55
    if corr>=80: mult*=0.80
    return label,round(mom*0.35+(100-vol)*0.20+(100-corr)*0.15+macro*0.30,1),round(mult,2)

def _crypto_scores(regime,asset,btcd,lsr,fg,champs):
    base_mom=GLOBAL_MOM.get(regime,50.0)
    slots=[k for k in champs if k.startswith(asset+'|')]
    n_short=sum(1 for s in slots if 'short' in str(champs.get(s,'')).lower())
    n_total=max(len(slots),1)
    mom=round(base_mom-(n_short/n_total-0.5)*20,1)
    mom=max(0,min(100,mom))
    volatility=45.0
    return mom,volatility,_macro(asset,btcd,lsr,fg)

def _commodity_scores(asset,btcd,lsr,fg):
    csv=BASE/f'models/data_{asset}_1h_max.csv'
    if not csv.exists(): return 50.0,50.0,_macro(asset,btcd,lsr,fg)
    rows=_read_csv_tail(str(csv),250)
    if len(rows)<20: return 50.0,50.0,_macro(asset,btcd,lsr,fg)
    closes=[r['close'] for r in rows]
    e21=_ema(closes,21); e50=_ema(closes,50); e200=_ema(closes,min(200,len(closes)//2))
    last=closes[-1]; score=50.0
    score+=12 if last>e21[-1] else -12
    score+=10 if last>e50[-1] else -10
    score+=8  if last>e200[-1] else -8
    score+=8  if e21[-1]>e21[-5] else -8
    score+=(_rsi(closes)-50)*0.24
    return max(0,min(100,round(score,1))),_atr_pct(rows),_macro(asset,btcd,lsr,fg)

def run():
    api=_fetch('http://127.0.0.1:8080/api/signals')
    regime=api.get('regime','NEUTRAL') if api else _load(BASE/'results/reports/port_snapshot.json').get('regime','NEUTRAL')
    snap=_load(BASE/'results/reports/port_snapshot.json')
    champs=snap.get('champions',{})
    lsr=_load(BASE/'results/derivatives/lsr_latest.json')
    fg=_load(BASE/'results/derivatives/fear_greed.json')
    btcd=_load(BASE/'results/reports/btc_dominance.json')
    btcd_val=btcd.get('value',0.5) if btcd else 0.5
    ts=_load(BASE/'results/trade_state.json')
    hist=ts.get('history',[])
    dirs=[t.get('direction','') for t in hist]
    ns,nl=dirs.count('short'),dirs.count('long')
    corr=round(abs(ns-nl)/max(len(dirs),1)*100*0.85+20,1); corr=max(20,min(95,corr))
    result={'computed_at':datetime.now(CHILE).isoformat(),'global_regime_m1':regime,'correlation_global':corr,'assets':{}}
    for asset in ASSETS_M1:
        mom,vol,mac=_crypto_scores(regime,asset,btcd_val,lsr,fg,champs)
        label,comp,mult=_label_mult(mom,vol,corr,mac)
        result['assets'][asset]={'momentum':mom,'volatility':vol,'correlation':corr,'macro':mac,'composite':comp,'label':label,'kelly_mult':mult,'source':'global_regime'}
    for asset in ASSETS_M2:
        mom,vol,mac=_commodity_scores(asset,btcd_val,lsr,fg)
        label,comp,mult=_label_mult(mom,vol,30.0,mac)
        result['assets'][asset]={'momentum':mom,'volatility':vol,'correlation':30.0,'macro':mac,'composite':comp,'label':label,'kelly_mult':mult,'source':'csv_based'}
    OUT_FILE.parent.mkdir(parents=True,exist_ok=True)
    OUT_FILE.write_text(json.dumps(result,indent=2))
    print(f'[REGIME_MULTI] global={regime} corr={corr} {len(result["assets"])} assets',flush=True)
    for a,d in result['assets'].items():
        print(f'  {a}: {d["label"]:16s} mom={d["momentum"]:5.1f} vol={d["volatility"]:5.1f} mac={d["macro"]:5.1f} x{d["kelly_mult"]}',flush=True)
    return result

if __name__=='__main__': run()
