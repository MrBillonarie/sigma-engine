"""
SIGMA ENGINE — Mejora Continua 4H
Estrategia especifica para generar MAS trades en 4H manteniendo calidad.

Problema: 37 trades/año es insuficiente estadisticamente.
Solucion:
  1. Mas historia (1500 dias = 4 años)
  2. Mas tipos de señales (trend + range + reversal)
  3. Cooldown minimo en 4H (1-2 barras = 4-8h)
  4. Ensemble: combinar 2-3 señales con voto de mayoria
  5. Objetivo: 60-80 trades/año en 4H
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random, json, numpy as np, pandas as pd
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

random.seed(55); np.random.seed(55)

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
COMMISSION = 0.0004
SLIPPAGE   = 0.0005
COST       = COMMISSION + SLIPPAGE


def load_max_history():
    """Carga la historia maxima disponible para 4H."""
    from core.data import fetch_ohlcv
    from core.features import build_features

    paths = {
        '4h': OUTPUT_DIR / 'models' / 'data_4h_max.csv',
        '1d': OUTPUT_DIR / 'models' / 'data_1d_max.csv',
    }

    dfs = {}
    for tf, path in paths.items():
        if path.exists():
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.index.name = 'timestamp'
            dfs[tf] = df.astype(float)
            days = (df.index[-1]-df.index[0]).days
            print(f"  {tf}: {len(df):,} velas | {days} dias")
        else:
            print(f"  {tf}: descargando...")
            dfs[tf] = fetch_ohlcv(tf=tf, days=1500)

    df_base = build_features(dfs['4h'], {'1d': dfs['1d']})
    df_base.dropna(subset=['close','atr','ema50'], inplace=True)
    return df_base


# ─── SEÑALES ESPECIFICAS 4H ───────────────────────────────────────────────────

def sig_4h_trend_pullback(df, cfg):
    """Pullback a EMA en tendencia — la más confiable en 4H."""
    c,h,l,o = df['close'],df['high'],df['low'],df['open']
    atr = df['atr']
    ema_f = c.ewm(span=cfg['ema_fast'],adjust=False).mean()
    ema_s = c.ewm(span=cfg['ema_slow'],adjust=False).mean()
    bull  = ema_f > ema_s; bear = ema_f < ema_s
    adx   = df.get('adx', pd.Series(25,index=df.index))
    tol   = cfg.get('tol', 0.008)

    # Pullback toca la EMA rapida y rebota
    touch_l = (l <= ema_f*(1+tol)) & (l >= ema_f*(1-tol)) & (c > ema_f) & (c > o)
    touch_s = (h >= ema_f*(1-tol)) & (h <= ema_f*(1+tol)) & (c < ema_f) & (c < o)

    htf = df.get('htf_long_1d', pd.Series(True,index=df.index))
    base = ~df.get('fake_move', pd.Series(False,index=df.index))

    sig = pd.Series(0,index=df.index)
    sig[touch_l & bull & (adx>cfg.get('adx_min',18)) & htf & base] = 1
    sig[touch_s & bear & (adx>cfg.get('adx_min',18)) & ~htf & base] = -1
    return _cd(sig, cfg.get('cooldown',2))


def sig_4h_range_reversal(df, cfg):
    """Reversal en extremos de Bollinger — funciona en rangos 4H."""
    c,h,l = df['close'],df['high'],df['low']
    sma   = c.rolling(20).mean(); std = c.rolling(20).std()
    bb_u  = sma+2*std; bb_l = sma-2*std
    rsi   = df.get('rsi', pd.Series(50,index=df.index))
    adx   = df.get('adx', pd.Series(20,index=df.index))

    ranging = adx < cfg.get('adx_max', 25)
    base    = ~df.get('fake_move', pd.Series(False,index=df.index))

    sig = pd.Series(0,index=df.index)
    sig[(l<=bb_l) & (rsi<cfg.get('rsi_os',35)) & ranging & (c>c.shift(1)) & base] = 1
    sig[(h>=bb_u) & (rsi>cfg.get('rsi_ob',65)) & ranging & (c<c.shift(1)) & base] = -1
    return _cd(sig, cfg.get('cooldown',2))


def sig_4h_breakout(df, cfg):
    """Breakout de rango semanal — grandes movimientos 4H."""
    c,h,l = df['close'],df['high'],df['low']
    lb  = cfg.get('lookback', 10)  # 10 velas x 4h = 40h ≈ semana
    hh  = h.rolling(lb).max().shift(1)
    ll  = l.rolling(lb).min().shift(1)
    vol_ok = df['volume'] > df['volume'].rolling(lb).mean() * cfg.get('vol_mult',1.3)
    htf    = df.get('htf_long_1d', pd.Series(True,index=df.index))
    base   = ~df.get('fake_move', pd.Series(False,index=df.index))

    sig = pd.Series(0,index=df.index)
    sig[(c>hh) & vol_ok & htf & base]  = 1
    sig[(c<ll) & vol_ok & ~htf & base] = -1
    return _cd(sig, cfg.get('cooldown',2))


def sig_4h_ma_cross(df, cfg):
    """Cruce de medias moviles — señales limpias en 4H."""
    c = df['close']
    f = c.ewm(span=cfg['fast'],adjust=False).mean()
    s = c.ewm(span=cfg['slow'],adjust=False).mean()
    cu = (f>s)&(f.shift()<=s.shift())
    cd = (f<s)&(f.shift()>=s.shift())
    htf = df.get('htf_long_1d', pd.Series(True,index=df.index))
    base= ~df.get('fake_move', pd.Series(False,index=df.index))

    sig = pd.Series(0,index=df.index)
    sig[cu & htf & base]  = 1
    sig[cd & ~htf & base] = -1
    return _cd(sig, cfg.get('cooldown',1))


def sig_4h_ensemble(df, cfg):
    """
    Voto de mayoria entre 3 señales.
    Entra solo cuando 2 de 3 señales coinciden.
    Mas trades que cualquiera por separado, mejor calidad que 1 señal sola.
    """
    s1 = sig_4h_trend_pullback(df, cfg)
    s2 = sig_4h_range_reversal(df, {'adx_max':28,'rsi_os':38,'rsi_ob':62,'cooldown':1})
    s3 = sig_4h_ma_cross(df, {'fast':cfg.get('ema_fast',9),'slow':cfg.get('ema_slow',21),'cooldown':1})

    votes_l = (s1==1).astype(int) + (s2==1).astype(int) + (s3==1).astype(int)
    votes_s = (s1==-1).astype(int) + (s2==-1).astype(int) + (s3==-1).astype(int)

    sig = pd.Series(0,index=df.index)
    sig[votes_l >= 2] = 1
    sig[votes_s >= 2] = -1
    return _cd(sig, cfg.get('cooldown',1))


def _cd(sig, bars):
    final = pd.Series(0,index=sig.index); last=-bars-1
    for i in range(len(sig)):
        if (i-last)>=bars and sig.iloc[i]!=0:
            final.iloc[i]=sig.iloc[i]; last=i
    return final


# ─── BACKTEST ─────────────────────────────────────────────────────────────────
def backtest(df, sig, sl_m, tp_m, risk=1.0, trail=False, trail_m=2.0):
    cap=CAPITAL; eq=[cap]; pos=0; entry=sl=tp=trl=sz=0.0; trades=[]
    for i in range(1,len(df)):
        row=df.iloc[i]; prev=df.iloc[i-1]; s=sig.iloc[i-1]
        pr=row['close']; atr=prev['atr']; h_=row['high']; lo=row['low']
        if pos!=0:
            pnl=0.; closed=False
            if trail:
                if pos==1: trl=max(trl,h_-atr*trail_m)
                else:      trl=min(trl,lo+atr*trail_m)
                if (pos==1 and lo<=trl) or (pos==-1 and h_>=trl):
                    pnl=pos*sz*(trl-entry)-sz*(entry+trl)*COST; closed=True
            else:
                if pos==1:
                    if lo<=sl: pnl=sz*(sl-entry)-sz*(entry+sl)*COST; closed=True
                    elif h_>=tp: pnl=sz*(tp-entry)-sz*(entry+tp)*COST; closed=True
                else:
                    if h_>=sl: pnl=sz*(entry-sl)-sz*(entry+sl)*COST; closed=True
                    elif lo<=tp: pnl=sz*(entry-tp)-sz*(entry+tp)*COST; closed=True
            if not closed and s==-pos:
                pnl=pos*sz*(pr-entry)-sz*(entry+pr)*COST; closed=True
            if closed:
                cap+=pnl; trades.append({'pnl':pnl,'won':pnl>0}); pos=0
        if pos==0 and s!=0 and cap>50:
            pos=s; entry=pr; r_sl=atr*sl_m
            sl=entry-r_sl if pos==1 else entry+r_sl
            tp=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            trl=sl; sz=(cap*risk/100)/r_sl if r_sl>0 else 0
        eq.append(cap)
    df_t=pd.DataFrame(trades)
    eq_s=pd.Series(eq[:len(df)],index=df.index[:len(eq)])
    if df_t.empty or len(df_t)<5: return None
    w=df_t[df_t['pnl']>0]; l=df_t[df_t['pnl']<=0]
    gp=w['pnl'].sum(); gl=abs(l['pnl'].sum())
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    ret=eq_s.pct_change().dropna()
    sh=ret.mean()/ret.std()*np.sqrt(2190) if ret.std()>0 else 0  # 2190 barras/año en 4H
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    wr=len(w)/len(df_t)
    import scipy.stats as st
    se=np.sqrt(wr*(1-wr)/len(df_t)); ci=st.norm.ppf(0.975)*se
    return {
        'trades':len(df_t),'winrate':round(wr*100,1),
        'ci_low':round((wr-ci)*100,1),'ci_high':round((wr+ci)*100,1),
        'cagr':round(cagr,2),'sharpe':round(sh,3),
        'max_dd':round(dd.min(),2),'pf':round(gp/gl,3) if gl>0 else 999,
        'calmar':round(cagr/abs(dd.min()),3) if dd.min()<0 else 0,
    }


def score(m, min_t=40):
    # FIX 2026-06-24: faltaba descalificar CAGR negativo -- un modelo perdedor
    # podia rankear bien si wr/pf/sharpe eran altos, porque calmar (cagr/dd)
    # negativo con peso 0.30 no alcanzaba a anular el resto. Mismo criterio que
    # canonical_score()/asset_pipeline.score() (cagr<=0 -> descalificado).
    if m is None or m['trades']<min_t or m.get('cagr', 0)<=0: return -9999
    pen = max(0,(80-m['trades'])/80)  # penaliza menos de 80 trades
    cal = min(m['calmar'],5)/5
    wr  = (m['winrate']/100-0.45)/0.35
    pf  = min(m['pf'],4)/4
    sh  = max(min(m['sharpe'],3),-3)/3
    return 0.30*cal+0.30*wr+0.20*pf+0.20*sh-pen*0.5


# ─── SEARCH ESPECIFICO 4H ─────────────────────────────────────────────────────
STRATEGIES_4H = {
    'Trend Pullback':  (sig_4h_trend_pullback, {
        'ema_fast':[9,12,21],'ema_slow':[34,50,100],
        'adx_min':[15,18,22],'tol':[0.005,0.008,0.012],'cooldown':[1,2]}),
    'Range Reversal':  (sig_4h_range_reversal, {
        'adx_max':[22,25,30],'rsi_os':[30,35,40],'rsi_ob':[60,65,70],'cooldown':[1,2]}),
    'Breakout':        (sig_4h_breakout, {
        'lookback':[8,10,14],'vol_mult':[1.2,1.5,2.0],'cooldown':[1,2]}),
    'MA Cross':        (sig_4h_ma_cross, {
        'fast':[7,9,12],'slow':[21,34,50],'cooldown':[1,2]}),
    'Ensemble':        (sig_4h_ensemble, {
        'ema_fast':[9,12],'ema_slow':[21,34],'cooldown':[1,2]}),
}

SL_RANGE    = [1.5,2.0,2.5,3.0,3.5]
TP_RANGE    = [2.5,3.0,4.0,5.0,6.0]
N_SAMPLES   = 300  # por estrategia


def run_4h_improvement(n_per=N_SAMPLES):
    print(f"\n{'='*65}")
    print(f"  MEJORA CONTINUA 4H — objetivo: 60-80 trades/año")
    print(f"  {len(STRATEGIES_4H)} estrategias x {n_per} muestras")
    print(f"{'='*65}")

    print(f"\n[DATA] Cargando historia maxima 4H...")
    df = load_max_history()
    days_total = (df.index[-1]-df.index[0]).days
    print(f"  {len(df):,} velas | {days_total} dias de historia")

    # OOS split: ultimos 20%
    split  = int(len(df)*0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    d_is   = (df_is.index[-1]-df_is.index[0]).days
    d_oos  = (df_oos.index[-1]-df_oos.index[0]).days
    print(f"  IS: {d_is}d | OOS: {d_oos}d\n")

    all_valid  = []; best_g = None; best_s = -9999; best_cfg_g = {}; best_name_g = ''

    for strat_name,(fn,space) in STRATEGIES_4H.items():
        print(f"  [{strat_name}]")
        best_strat = None; best_strat_s = -9999

        for _ in range(n_per):
            cfg = {k:random.choice(v) for k,v in space.items()}
            sl  = random.choice(SL_RANGE)
            tp  = random.choice(TP_RANGE)
            if tp<=sl: continue
            trail    = random.choice([True,False])
            trail_m  = random.choice([2.0,2.5,3.0]) if trail else 2.0
            risk     = random.choice([0.5,0.8,1.0,1.5,2.0])

            try:
                sig = fn(df_is, cfg)
                if (sig!=0).sum()<20: continue
                m = backtest(df_is,sig,sl,tp,risk,trail,trail_m)
                if m is None: continue
                s = score(m, min_t=40)

                if s > best_strat_s:
                    best_strat_s=s; best_strat=m.copy()

                if s > best_s:
                    best_s=s; best_g=m.copy(); best_name_g=strat_name
                    best_cfg_g={**cfg,'sl':sl,'tp':tp,'trail':trail,'trail_m':trail_m,'risk':risk}
                    if m['trades']>=60:
                        print(f"  *** NUEVO MEJOR ({strat_name}) ***")
                        print(f"  {m['trades']}T | WR {m['winrate']:.1f}% [{m['ci_low']:.1f}-{m['ci_high']:.1f}%] | "
                              f"CAGR {m['cagr']:+.1f}%/año | Calmar {m['calmar']:.2f} | PF {m['pf']:.2f}")

                if m['trades']>=50 and m['winrate']>=50 and m['cagr']>0 and m['calmar']>=0.5:
                    all_valid.append({**m,'strategy':strat_name,'cfg':cfg,'sl':sl,'tp':tp})
            except: continue

        if best_strat:
            print(f"  Mejor: {best_strat['trades']}T | WR {best_strat['winrate']:.1f}% | "
                  f"CAGR {best_strat['cagr']:+.1f}%/año | Calmar {best_strat['calmar']:.2f}")

    # OOS del mejor
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION — {best_name_g}")
    all_valid.sort(key=lambda x:x.get('calmar',0),reverse=True)

    if best_cfg_g:
        fn,_ = STRATEGIES_4H[best_name_g]
        inner = {k:v for k,v in best_cfg_g.items() if k not in ('sl','tp','trail','trail_m','risk')}
        try:
            sig_oos = fn(df_oos, inner)
            m_oos = backtest(df_oos,sig_oos,best_cfg_g['sl'],best_cfg_g['tp'],
                             best_cfg_g['risk'],best_cfg_g['trail'],best_cfg_g['trail_m'])
            if m_oos:
                print(f"  OOS 4H: {m_oos['trades']}T | WR {m_oos['winrate']:.1f}% "
                      f"[{m_oos['ci_low']:.1f}-{m_oos['ci_high']:.1f}%] | "
                      f"CAGR {m_oos['cagr']:+.1f}%/año | Calmar {m_oos['calmar']:.2f}")

                # Comparar con modelo anterior
                prev_path = OUTPUT_DIR/'models'/'4h'/'best_validated.json'
                if prev_path.exists():
                    with open(prev_path) as f:
                        prev = json.load(f)
                    prev_m = prev.get('metrics_oos', prev.get('metrics_is', {}))
                    prev_cagr = prev_m.get('cagr', 0)
                    if m_oos['cagr'] > prev_cagr:
                        print(f"  MEJORA: {prev_cagr:+.1f}% → {m_oos['cagr']:+.1f}% CAGR")
                    else:
                        print(f"  Sin mejora vs modelo anterior ({prev_cagr:+.1f}%). Manteniendo anterior.")

                # Guardar SOLO si supera el CAGR OOS actual
                (OUTPUT_DIR/'models'/'4h').mkdir(parents=True,exist_ok=True)
                new_score = score(m_oos, min_t=20)
                save = True
                if prev_path.exists():
                    with open(prev_path) as f:
                        prev = json.load(f)
                    prev_m_check = prev.get('metrics_oos', prev.get('metrics_is', {}))
                    if m_oos['cagr'] <= prev_m_check.get('cagr', 0):
                        save = False

                if save and m_oos['cagr']>0:
                    with open(OUTPUT_DIR/'models'/'4h'/'best_validated.json','w') as f:
                        json.dump({'tf':'4h','strategy':best_name_g,'params':best_cfg_g,
                                   'metrics_is':{k:round(v,4) if isinstance(v,float) else v
                                                 for k,v in (best_g or {}).items()},
                                   'metrics_oos':{k:round(v,4) if isinstance(v,float) else v
                                                  for k,v in m_oos.items()},
                                   'score':new_score,'valid_configs':len(all_valid)},f,indent=2)
                    print(f"  [SAVED] Modelo 4H actualizado")
        except Exception as e:
            print(f"  OOS error: {e}")

    print(f"\n  Configs validas (50+T, WR>=50%, Calmar>=0.5): {len(all_valid)}")
    if all_valid:
        print(f"\n  TOP 5:")
        for i,m in enumerate(all_valid[:5],1):
            print(f"  {i}. {m['strategy']}: {m['trades']}T | WR {m['winrate']:.1f}% | "
                  f"CAGR {m['cagr']:+.1f}%/año | Calmar {m['calmar']:.2f}")

        pd.DataFrame(all_valid).to_csv(
            OUTPUT_DIR/'results'/'reports'/'4h_improvement_results.csv',index=False)

    # Notificar
    try:
        import winsound, subprocess
        for _ in range(3): winsound.Beep(1000,300)
        m = all_valid[0] if all_valid else {}
        msg=(f"4H MEJORA COMPLETADA!\\n\\n"
             f"Configs validas: {len(all_valid)}\\n\\n"
             f"MEJOR: {m.get('strategy','?')}\\n"
             f"Trades: {m.get('trades',0)} | WR: {m.get('winrate',0):.1f}%\\n"
             f"CAGR: {m.get('cagr',0):+.1f}%/año | Calmar: {m.get('calmar',0):.2f}")
        subprocess.Popen(['powershell','-WindowStyle','Hidden','-Command',
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}","4H Mejora","OK","Information")'])
    except: pass

    print(f"\n[DONE] 4H improvement completado.")
    return all_valid


if __name__=='__main__':
    run_4h_improvement()
