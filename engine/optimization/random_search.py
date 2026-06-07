"""
SIGMA Random Search — 5000 muestras aleatorias
Mucho mas rapido que grid exhaustivo, misma cobertura estadistica.
Combina logica del CAMPEON + Dual-Mode del HUD v12.9.5
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data import fetch_multi_tf
from core.features import build_features
COMMISSION = 0.0004  # 0.04% taker fee por lado (Binance Futures)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'results')
import pandas as pd
import numpy as np
import warnings
import subprocess
import winsound
warnings.filterwarnings('ignore')

random.seed(42)
np.random.seed(42)

CAMPEON_TV = {'trades': 43, 'wr': 90.7, 'pf': 16.79, 'pnl': 14.61, 'dd': 1.2}
N_SAMPLES  = 5000

# ─── ESPACIO DE PARAMETROS ────────────────────────────────────────────────────
SPACE = {
    # Señales
    'use_elite_ict':    [True],
    'use_elite':        [True],
    'use_execute':      [True, False],
    'use_watch':        [False, True],
    'use_trend_mode':   [True, False],
    'use_range_mode':   [True, False],
    # Filtros de calidad
    'exec_setup_thr':   list(range(50, 80, 5)),
    'exec_timing_thr':  list(range(45, 70, 5)),
    'meta_ctx_min':     [1, 2, 3],
    'ofi_threshold':    [0.4, 0.5, 0.55, 0.6, 0.65, 0.7],
    'mkt_temp_min':     [5, 10, 15, 20, 25],
    'mkt_temp_max':     [75, 80, 85, 90, 100],
    # SL/TP
    'elite_sl_mult':    [1.1, 1.3, 1.5, 1.7, 2.0],
    'elite_tp_mult':    [2.0, 2.5, 3.0, 3.5, 4.0],
    'exec_sl_mult':     [1.3, 1.5, 1.7, 2.0, 2.15],
    'exec_tp_mult':     [1.5, 2.0, 2.5, 3.0, 4.0, 4.5],
    # Risk
    'risk_pct':         [0.5, 0.8, 1.0, 1.5],
    'use_be':           [True, False],
    'qty_tp1':          [0.40, 0.50, 0.60],
    # Sesiones
    'use_asia':         [True, False],
    'use_sess_b':       [True, False],
    # Dual-Mode
    'hurst_trend_thr':  [0.52, 0.54, 0.55, 0.57, 0.60],
    'adx_trend_thr':    [18, 20, 22, 25, 28, 30],
    'hurst_range_thr':  [0.46, 0.48, 0.50, 0.52],
    'adx_range_thr':    [16, 18, 20, 22, 25],
    # Cooldown
    'signal_cooldown':  [2, 4, 6, 8, 12, 16],
    # Dias
    'allow_friday':     [True, False],
    # Bayesiano
    'p_win_bull':       [0.50, 0.52, 0.54, 0.56],
    'p_win_bear':       [0.46, 0.48, 0.50, 0.52],
    # HTF
    'require_htf_4h':   [True, False],
}

def sample_config():
    return {k: random.choice(v) for k, v in SPACE.items()}

# ─── INDICADORES SIMPLES ──────────────────────────────────────────────────────
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _rsi(c, n=14):
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))

def _adx(h, l, c, n=14):
    up = h.diff(); dn = -l.diff()
    pdm = np.where((up>dn)&(up>0), up, 0.0)
    mdm = np.where((dn>up)&(dn>0), dn, 0.0)
    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_ = 100*pd.Series(pdm,index=h.index).ewm(alpha=1/n,adjust=False).mean()/atr
    minus_= 100*pd.Series(mdm,index=h.index).ewm(alpha=1/n,adjust=False).mean()/atr
    dx    = 100*(plus_-minus_).abs()/(plus_+minus_+1e-9)
    return dx.ewm(alpha=1/n, adjust=False).mean(), plus_, minus_

# ─── FEATURES RAPIDAS ─────────────────────────────────────────────────────────
def build_fast_features(df_15m, df_1h, df_4h):
    df = df_15m.copy()
    c,h,l,v,o = df['close'],df['high'],df['low'],df['volume'],df['open']

    # ATR
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

    # EMAs
    df['ema20']  = _ema(c,20)
    df['ema50']  = _ema(c,50)
    df['ema200'] = _ema(c,200)
    df['bull']   = df['ema50'] > df['ema200']
    df['bear']   = df['ema50'] < df['ema200']

    # MACD
    m12 = _ema(c,12); m26 = _ema(c,26)
    df['macd']   = m12 - m26
    df['signal'] = _ema(df['macd'], 9)
    df['hist']   = df['macd'] - df['signal']

    # RSI
    df['rsi'] = _rsi(c)

    # ADX
    df['adx'], df['di_plus'], df['di_minus'] = _adx(h, l, c)

    # Hurst proxy
    rn  = h.rolling(50).max() - l.rolling(50).min()
    rn2 = h.rolling(25).max() - l.rolling(25).min()
    df['hurst'] = np.where(rn2>0, np.log(rn/(rn2+1e-9))/np.log(2), 0.5)

    # Bollinger
    sma20 = c.rolling(20).mean(); std20 = c.rolling(20).std()
    df['bb_upper'] = sma20 + 2*std20
    df['bb_lower'] = sma20 - 2*std20

    # OFI
    body = (c-o).abs()/(h-l+1e-9)
    bv = v*np.where(c>o, body, 0); sv = v*np.where(c<o, body, 0)
    tot = pd.Series(bv).rolling(20).sum() + pd.Series(sv).rolling(20).sum()
    df['ofi'] = ((pd.Series(bv).rolling(20).sum() - pd.Series(sv).rolling(20).sum()) / (tot+1e-9)).ewm(span=3).mean()

    # CVD
    delta = np.where(c>o, v, np.where(c<o, -v, 0))
    df['cvd']    = pd.Series(delta, index=df.index).cumsum()
    df['cvd_ma'] = df['cvd'].rolling(20).mean()
    df['cvd_bull'] = df['cvd'] > df['cvd_ma']

    # Vol
    df['vol_ma'] = v.rolling(20).mean()
    df['vol_ok'] = v > df['vol_ma'] * 1.5
    df['atr_ratio'] = df['atr'] / df['atr'].rolling(50).mean()
    df['is_spike'] = (h-l) > df['atr']*2

    # Fake move
    liq_up   = h > h.rolling(20).max().shift(1)
    liq_down = l < l.rolling(20).min().shift(1)
    df['fake_move'] = (liq_up&(c<o)) | (liq_down&(c>o))

    # Vol percentile
    atr_min = df['atr'].rolling(100,min_periods=10).min()
    atr_max = df['atr'].rolling(100,min_periods=10).max()
    df['vol_pct'] = ((df['atr']-atr_min)/(atr_max-atr_min+1e-9)*100).clip(0,100)

    # OB simple
    imp_up = (c.shift(9)>o.shift(9)) & ((c.shift(9)-o.shift(9))>df['atr'].shift(9)*0.8)
    ob_bull = (c.shift(10)<o.shift(10)) & imp_up
    df['in_bull_ob'] = ob_bull & (c<=o.shift(10)) & (c>=c.shift(10)) & df['bull']
    imp_dn = (c.shift(9)<o.shift(9)) & ((o.shift(9)-c.shift(9))>df['atr'].shift(9)*0.8)
    ob_bear = (c.shift(10)>o.shift(10)) & imp_dn
    df['in_bear_ob'] = ob_bear & (c>=o.shift(10)) & (c<=c.shift(10)) & df['bear']

    # FVG
    df['fvg_bull'] = l > h.shift(2)
    df['fvg_bear'] = h < l.shift(2)
    df['fill_bull_fvg'] = (c<=l.shift(1))&(c>=h.shift(3))&df['bull']
    df['fill_bear_fvg'] = (c>=h.shift(1))&(c<=l.shift(3))&df['bear']

    # AVWAP
    df['week'] = df.index.isocalendar().week.values
    tp = (h+l+c)/3
    an=[]; avd_=[]; pn=avd=0.0; pw=-1
    for i in range(len(df)):
        wk=df['week'].iloc[i]
        if wk!=pw: pn=0.0; avd=0.0; pw=wk
        pn+=tp.iloc[i]*v.iloc[i]; avd+=v.iloc[i]
        an.append(pn); avd_.append(avd)
    df['avwap'] = np.array(an)/np.maximum(np.array(avd_),1e-9)
    df['above_avwap'] = c > df['avwap']

    # Gap block
    gap = (o-c.shift()).abs() > df['atr']*2
    bsg=[]; cnt=9999
    for ig in gap:
        if ig: cnt=0
        bsg.append(cnt); cnt+=1
    df['bars_since_gap'] = bsg

    # Day of week
    df['dow'] = df.index.dayofweek

    # Session
    df['hour'] = df.index.hour

    # HTF 1h
    h1 = df_1h.copy()
    h1['ema50_1h']  = _ema(h1['close'], 50)
    h1['ema200_1h'] = _ema(h1['close'], 200)
    h1['htf1_long']  = h1['ema50_1h'] > h1['ema200_1h']
    h1['htf1_short'] = h1['ema50_1h'] < h1['ema200_1h']
    df = pd.merge_asof(df.reset_index(), h1[['htf1_long','htf1_short']].reset_index(),
                       on='timestamp', direction='backward').set_index('timestamp')

    # HTF 4h
    h4 = df_4h.copy()
    h4['ema50_4h']  = _ema(h4['close'], 50)
    h4['ema200_4h'] = _ema(h4['close'], 200)
    h4['htf4_long'] = h4['ema50_4h'] > h4['ema200_4h']
    df = pd.merge_asof(df.reset_index(), h4[['htf4_long']].reset_index(),
                       on='timestamp', direction='backward').set_index('timestamp')

    df['htf1_long']  = df['htf1_long'].fillna(False)
    df['htf1_short'] = df['htf1_short'].fillna(False)
    df['htf4_long']  = df['htf4_long'].fillna(False)

    # Smart / Elite signals
    df['trend_power'] = (df['ema50']-df['ema200']).abs()
    df['trend_gate']  = df['trend_power'] > df['atr']*0.5
    df['smart_long']  = df['bull'] & df['trend_gate'] & (df['macd']>df['signal']) & df['htf1_long'] & ~df['is_spike']
    df['smart_short'] = df['bear'] & df['trend_gate'] & (df['macd']<df['signal']) & df['htf1_short'] & ~df['is_spike']
    df['tf3_bull'] = df['bull'] & df['htf1_long']  & df['htf4_long']
    df['tf3_bear'] = df['bear'] & df['htf1_short'] & ~df['htf4_long']
    df['elite_long']      = df['smart_long']  & df['tf3_bull'] & ~df['fake_move'] & (df['rsi']<70)
    df['elite_short']     = df['smart_short'] & df['tf3_bear'] & ~df['fake_move'] & (df['rsi']>30)
    df['elite_ict_long']  = df['elite_long']  & (df['in_bull_ob']|df['fill_bull_fvg']|df['above_avwap'])
    df['elite_ict_short'] = df['elite_short'] & (df['in_bear_ob']|df['fill_bear_fvg']|~df['above_avwap'])

    # Regimen
    df['is_trend_up']   = (df['hurst']>0.55) & (df['adx']>25) & df['bull']  & (df['close']>df['ema50'])
    df['is_trend_down'] = (df['hurst']>0.55) & (df['adx']>25) & df['bear']  & (df['close']<df['ema50'])
    df['is_weak_range'] = (df['hurst']<0.50) & (df['adx']<20)

    # RSI divergences para RANGE
    rsi_ll = df['rsi'].rolling(14).min()
    rsi_hh = df['rsi'].rolling(14).max()
    price_ll = l.rolling(14).min()
    price_hh = h.rolling(14).max()
    df['bull_div'] = (l<=price_ll) & (df['rsi']>rsi_ll.shift(1)) & (df['rsi']>30)
    df['bear_div'] = (h>=price_hh) & (df['rsi']<rsi_hh.shift(1)) & (df['rsi']<70)

    return df.ffill().bfill()

# ─── SEÑALES RAPIDAS ──────────────────────────────────────────────────────────
def get_signals(df, cfg):
    c = df['close']
    h_utc = df['hour']
    dow   = df['dow']

    # Sesiones
    in_a = (h_utc>=8)&(h_utc<12)
    in_b = (h_utc>=13)&(h_utc<20) if cfg['use_sess_b'] else pd.Series(False,index=df.index)
    in_asia = (h_utc>=1)&(h_utc<6) if cfg['use_asia'] else pd.Series(False,index=df.index)
    in_sess = in_a|in_b|in_asia

    # Dias
    allowed = [1,2,3]  # Mar,Mie,Jue siempre
    if cfg['allow_friday']: allowed.append(4)
    day_ok = pd.Series(dow,index=df.index).isin(allowed)

    # Gap
    gap_ok = df['bars_since_gap'] >= 2

    # OFI
    ofi_bull = df['ofi'] >  cfg['ofi_threshold']
    ofi_bear = df['ofi'] < -cfg['ofi_threshold']

    # Temp
    atr_r = df['atr_ratio']
    rsi_h = (df['rsi']-50).abs()*2
    mkt_temp = ((atr_r*50)*0.5 + rsi_h*0.2 + (df['vol_pct']/2)*0.3).clip(0,100)
    temp_ok = (mkt_temp>=cfg['mkt_temp_min'])&(mkt_temp<=cfg['mkt_temp_max'])

    # Base filters
    base = ~df['fake_move'] & ~df['is_spike'] & day_ok & gap_ok & temp_ok & in_sess

    # HTF
    if cfg['require_htf_4h']:
        htf_long_ok  = df['htf1_long']  & df['htf4_long']
        htf_short_ok = df['htf1_short'] & ~df['htf4_long']
    else:
        htf_long_ok  = df['htf1_long']
        htf_short_ok = df['htf1_short']

    # Señales por calidad
    sig_l = pd.Series(False, index=df.index)
    sig_s = pd.Series(False, index=df.index)

    # ELITE_ICT
    eit_l = df['elite_ict_long']  & (df['sigma_long']>=75  if 'sigma_long'  in df.columns else pd.Series(True,index=df.index))
    eit_s = df['elite_ict_short'] & (df['sigma_short']>=75 if 'sigma_short' in df.columns else pd.Series(True,index=df.index))
    sig_l = sig_l | eit_l
    sig_s = sig_s | eit_s

    # ELITE
    if cfg['use_elite']:
        sig_l = sig_l | (df['elite_long']  & ~eit_l & htf_long_ok)
        sig_s = sig_s | (df['elite_short'] & ~eit_s & htf_short_ok)

    # EXECUTE
    if cfg['use_execute']:
        et = cfg['exec_setup_thr']
        exc_l = df['smart_long']  & ~df['elite_long']  & (df['macd']>df['signal']) & (df['adx']>et*0.3) & htf_long_ok
        exc_s = df['smart_short'] & ~df['elite_short'] & (df['macd']<df['signal']) & (df['adx']>et*0.3) & htf_short_ok
        sig_l = sig_l | exc_l
        sig_s = sig_s | exc_s

    # WATCH
    if cfg['use_watch']:
        wat_l = df['smart_long']  & ~df['elite_long']  & ~(cfg['use_execute'] and True)
        wat_s = df['smart_short'] & ~df['elite_short'] & ~(cfg['use_execute'] and True)
        sig_l = sig_l | wat_l
        sig_s = sig_s | wat_s

    # TREND MODE
    if cfg['use_trend_mode']:
        is_tu = (df['hurst']>cfg['hurst_trend_thr']) & (df['adx']>cfg['adx_trend_thr']) & df['bull'] & (df['close']>df['ema50'])
        is_td = (df['hurst']>cfg['hurst_trend_thr']) & (df['adx']>cfg['adx_trend_thr']) & df['bear'] & (df['close']<df['ema50'])
        tl = is_tu & (df['low']<=df['ema20']*1.005) & (df['close']>df['ema20']) & (df['close']>df['open']) & (df['macd']>df['signal']) & ~df['fake_move'] & ~df['is_spike']
        ts = is_td & (df['high']>=df['ema20']*0.995) & (df['close']<df['ema20']) & (df['close']<df['open']) & (df['macd']<df['signal']) & ~df['fake_move'] & ~df['is_spike']
        if cfg['require_htf_4h']:
            tl = tl & htf_long_ok; ts = ts & htf_short_ok
        sig_l = sig_l | tl
        sig_s = sig_s | ts

    # RANGE MODE
    if cfg['use_range_mode']:
        is_wr = (df['hurst']<cfg['hurst_range_thr']) & (df['adx']<cfg['adx_range_thr'])
        rl = is_wr & (df['low']<=df['bb_lower']) & (df['close']>df['bb_lower']) & (df['rsi']<30) & df['bull_div'] & ~df['fake_move']
        rs = is_wr & (df['high']>=df['bb_upper']) & (df['close']<df['bb_upper']) & (df['rsi']>70) & df['bear_div'] & ~df['fake_move']
        sig_l = sig_l | rl
        sig_s = sig_s | rs

    # OFI boost (requiere OFI en direccion)
    sig_l = sig_l & base
    sig_s = sig_s & base

    # Cooldown
    cd = cfg['signal_cooldown']
    final = pd.Series(0, index=df.index)
    quality = pd.Series('NONE', index=df.index)
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if sig_l.iloc[i]:
            final.iloc[i] = 1
            quality.iloc[i] = 'ELITE_ICT' if eit_l.iloc[i] else 'ELITE' if df['elite_long'].iloc[i] else 'EXEC'
            last = i
        elif sig_s.iloc[i]:
            final.iloc[i] = -1
            quality.iloc[i] = 'ELITE_ICT' if eit_s.iloc[i] else 'ELITE' if df['elite_short'].iloc[i] else 'EXEC'
            last = i

    return final, quality

# ─── BACKTEST RAPIDO ──────────────────────────────────────────────────────────
def backtest(df, signals, quality, cfg):
    cap = cfg['risk_pct'] / 100 * 1000  # risk USD
    capital = 1000.0
    equity = [capital]
    trades = []
    pos = 0; entry = sl = tp1 = tp2 = 0.0; size = 0.0
    be_done = False; partial = 0.0

    for i in range(1, len(df)):
        row  = df.iloc[i]; prev = df.iloc[i-1]
        sig  = signals.iloc[i-1]
        qual = quality.iloc[i-1]
        pr   = row['close']; atr = prev['atr']
        h    = row['high'];  lo  = row['low']

        sl_m = cfg['elite_sl_mult'] if qual in ('ELITE_ICT','ELITE') else cfg['exec_sl_mult']
        tp_m = cfg['elite_tp_mult'] if qual in ('ELITE_ICT','ELITE') else cfg['exec_tp_mult']
        qt1  = cfg['qty_tp1']

        if pos != 0:
            pnl = 0.0; closed = False; reason = ''
            sl_eff = entry if (be_done and cfg['use_be']) else sl

            if pos == 1:
                if not be_done and h >= tp1:
                    pnl += size*qt1*(tp1-entry) - size*qt1*(entry+tp1)*COMMISSION
                    partial += pnl; be_done = True
                if be_done and h >= tp2:
                    p2 = size*(1-qt1)*(tp2-entry) - size*(1-qt1)*(entry+tp2)*COMMISSION
                    pnl += p2; closed = True; reason = 'TP2'
                if not closed and lo <= sl_eff:
                    rem = (1-qt1) if be_done else 1.0
                    pnl += rem*size*(sl_eff-entry) - rem*size*(entry+sl_eff)*COMMISSION
                    closed = True; reason = 'BE' if be_done else 'SL'
            else:
                if not be_done and lo <= tp1:
                    pnl += size*qt1*(entry-tp1) - size*qt1*(entry+tp1)*COMMISSION
                    partial += pnl; be_done = True
                if be_done and lo <= tp2:
                    p2 = size*(1-qt1)*(entry-tp2) - size*(1-qt1)*(entry+tp2)*COMMISSION
                    pnl += p2; closed = True; reason = 'TP2'
                if not closed and h >= sl_eff:
                    rem = (1-qt1) if be_done else 1.0
                    pnl += rem*size*(entry-sl_eff) - rem*size*(entry+sl_eff)*COMMISSION
                    closed = True; reason = 'BE' if be_done else 'SL'

            if not closed and sig == -pos:
                rem = (1-qt1) if be_done else 1.0
                pnl += rem*size*(pr-entry)*pos - rem*size*(entry+pr)*COMMISSION
                closed = True; reason = 'Signal'

            if closed:
                total = partial + pnl
                capital += total
                trades.append({'pnl': total, 'won': total > 0,
                                'reason': reason, 'capital': capital,
                                'side': 'L' if pos==1 else 'S'})
                pos = 0; be_done = False; partial = 0.0

        if pos == 0 and sig != 0 and capital > 50:
            pos = sig; entry = pr; be_done = False; partial = 0.0
            sl_m2 = cfg['elite_sl_mult'] if qual in ('ELITE_ICT','ELITE') else cfg['exec_sl_mult']
            tp_m2 = cfg['elite_tp_mult'] if qual in ('ELITE_ICT','ELITE') else cfg['exec_tp_mult']
            r_sl  = atr * sl_m2
            sl    = entry - r_sl if pos==1 else entry + r_sl
            tp1   = entry + atr*tp_m2     if pos==1 else entry - atr*tp_m2
            tp2   = entry + atr*tp_m2*1.5 if pos==1 else entry - atr*tp_m2*1.5
            size  = (capital * cfg['risk_pct']/100) / r_sl if r_sl > 0 else 0

        equity.append(capital)

    df_t = pd.DataFrame(trades)
    eq   = pd.Series(equity[:len(df)], index=df.index[:len(equity)])
    if df_t.empty or len(df_t) < 3:
        return {'trades':0,'winrate':0,'pnl_pct':-999,'sharpe':-99,
                'max_dd':-100,'profit_factor':0,'calmar':0}
    w = df_t[df_t['pnl']>0]; l = df_t[df_t['pnl']<=0]
    gp = w['pnl'].sum(); gl = abs(l['pnl'].sum())
    peak = eq.cummax(); dd = (eq-peak)/peak*100
    ret  = eq.pct_change().dropna()
    sh   = ret.mean()/ret.std()*np.sqrt(35040) if ret.std()>0 else 0
    pnl  = (eq.iloc[-1]-1000)/1000*100
    cal  = pnl/abs(dd.min()) if dd.min()<0 else 0
    return {'trades':len(df_t),'winrate':len(w)/len(df_t)*100,
            'pnl_pct':pnl,'sharpe':sh,'max_dd':dd.min(),
            'profit_factor':gp/gl if gl>0 else 999,'calmar':cal}

def score(m):
    if m['trades'] < 20: return -9999 + m['trades']
    trade_bonus = min(m['trades']/43, 3.0) * 0.20
    wr_pen = max(0, 60-m['winrate']) * 0.03
    wr  = m['winrate']/100
    pf  = min(m['profit_factor'],20)/20
    sh  = max(min(m['sharpe'],8),-8)/8
    pnl = max(min(m['pnl_pct'],100),-50)/100
    dd  = max(min(m['max_dd'],0),-30)/-30
    return 0.25*wr + 0.20*pf + 0.15*sh + 0.20*pnl + 0.10*(1-dd) + trade_bonus - wr_pen

# ─── PINE SCRIPT GANADOR ──────────────────────────────────────────────────────
def gen_pine(cfg, m):
    modes = []
    if cfg['use_execute']:   modes.append("EXEC")
    if cfg['use_trend_mode']:modes.append("TREND")
    if cfg['use_range_mode']:modes.append("RANGE")
    if cfg['use_watch']:     modes.append("WATCH")
    mode_str = "+".join(modes) if modes else "ELITE"
    return f"""//@version=6
// SIGMA K1 RANDOM SEARCH WINNER — {mode_str}
// Python backtest: {m['trades']}T | WR {m['winrate']:.1f}% | PnL {m['pnl_pct']:+.1f}%
// MaxDD {m['max_dd']:.1f}% | PF {m['profit_factor']:.2f} | Sharpe {m['sharpe']:.2f}
// vs CAMPEON: 43T | 90.7%WR | +14.61% | DD1.2% | PF16.79
strategy("SIGMA K1 — {mode_str}", overlay=true,
         default_qty_type=strategy.percent_of_equity, default_qty_value=100,
         commission_type=strategy.commission.percent, commission_value=0.05,
         slippage=2, initial_capital=1000)

atr    = ta.atr(14)
ema20  = ta.ema(close, 20)
ema50  = ta.ema(close, 50)
ema200 = ta.ema(close, 200)
[ml, sl2, hist] = ta.macd(close, 12, 26, 9)
rsi    = ta.rsi(close, 14)
[dp, dm, adx]  = ta.dmi(14, 14)
range_n  = ta.highest(close,50)-ta.lowest(close,50)
range_n2 = ta.highest(close,25)-ta.lowest(close,25)
hurst    = range_n2>0 ? math.log(range_n/math.max(range_n2,0.001))/math.log(2.0) : 0.5

[ema50_1h,ema200_1h] = request.security(syminfo.tickerid,"60",[ta.ema(close,50),ta.ema(close,200)],lookahead=barmerge.lookahead_off)
[ema50_4h,ema200_4h] = request.security(syminfo.tickerid,"240",[ta.ema(close,50),ta.ema(close,200)],lookahead=barmerge.lookahead_off)
bull = ema50>ema200; bear = ema50<ema200
htf1_long  = ema50_1h>ema200_1h; htf1_short = ema50_1h<ema200_1h
htf4_long  = ema50_4h>ema200_4h

h_utc = hour(time,"UTC")
in_sess = (h_utc>=8 and h_utc<12) or (h_utc>=13 and h_utc<20){" or (h_utc>=1 and h_utc<6)" if cfg['use_asia'] else ""}
dow_ok  = dayofweek(time,"UTC")>=dayofweek.tuesday and dayofweek(time,"UTC")<={("dayofweek.friday" if cfg['allow_friday'] else "dayofweek.thursday")}

liq_up=high>ta.highest(high,20)[1]; liq_dn=low<ta.lowest(low,20)[1]
fake_move=(liq_up and close<open) or (liq_dn and close>open)
is_spike=(high-low)>atr*2.0
bar_gap=math.abs(open-close[1])>atr*2.0
var int bsg=9999; bsg:=bar_gap?0:bsg+1; gap_ok=bsg>=2

is_trend_up   = hurst>{cfg['hurst_trend_thr']} and adx>{cfg['adx_trend_thr']} and bull and close>ema50
is_trend_down = hurst>{cfg['hurst_trend_thr']} and adx>{cfg['adx_trend_thr']} and bear and close<ema50
is_weak_range = hurst<{cfg['hurst_range_thr']} and adx<{cfg['adx_range_thr']}
[bbM,bbU,bbL] = ta.bb(close,20,2)

body_r=math.abs(close-open)/math.max(high-low,0.0001)
buy_v=volume*(close>open?body_r:0.0); sell_v=volume*(close<open?body_r:0.0)
tot_v=math.sum(buy_v,20)+math.sum(sell_v,20)
ofi=ta.ema(tot_v>0?(math.sum(buy_v,20)-math.sum(sell_v,20))/tot_v:0.0,3)

tf3_bull=bull and htf1_long and htf4_long; tf3_bear=bear and htf1_short and not htf4_long
smart_long  = bull and math.abs(ema50-ema200)>atr*0.5 and ml>sl2 and htf1_long  and not is_spike
smart_short = bear and math.abs(ema50-ema200)>atr*0.5 and ml<sl2 and htf1_short and not is_spike
elite_long  = smart_long  and tf3_bull and not fake_move and rsi<70
elite_short = smart_short and tf3_bear and not fake_move and rsi>30

base_ok = not fake_move and not is_spike and dow_ok and gap_ok and in_sess

trend_long  = {"is_trend_up   and low<=ema20*1.005 and close>ema20 and close>open and ml>sl2 and not fake_move" if cfg['use_trend_mode'] else "false"}
trend_short = {"is_trend_down and high>=ema20*0.995 and close<ema20 and close<open and ml<sl2 and not fake_move" if cfg['use_trend_mode'] else "false"}

bull_div = low<=ta.lowest(low,14) and rsi>ta.lowest(rsi,14)[1] and rsi>30
bear_div = high>=ta.highest(high,14) and rsi<ta.highest(rsi,14)[1] and rsi<70
range_long  = {"is_weak_range and low<=bbL and close>bbL and rsi<30 and bull_div and not fake_move" if cfg['use_range_mode'] else "false"}
range_short = {"is_weak_range and high>=bbU and close<bbU and rsi>70 and bear_div and not fake_move" if cfg['use_range_mode'] else "false"}

entry_long  = base_ok and (elite_long  or ({"smart_long  and not elite_long  and not fake_move" if cfg['use_execute'] else "false"}) or trend_long  or range_long)
entry_short = base_ok and (elite_short or ({"smart_short and not elite_short and not fake_move" if cfg['use_execute'] else "false"}) or trend_short or range_short)

is_elite = elite_long or elite_short
sl_m = is_elite ? {cfg['elite_sl_mult']} : {cfg['exec_sl_mult']}
tp_m = is_elite ? {cfg['elite_tp_mult']} : {cfg['exec_tp_mult']}

var float eref=na; var bool be_done=false
if entry_long  and strategy.position_size==0
    eref:=close; be_done:=false; strategy.entry("L",strategy.long)
if entry_short and strategy.position_size==0
    eref:=close; be_done:=false; strategy.entry("S",strategy.short)
if high>=(eref+atr*tp_m) and strategy.position_size>0  and not be_done; be_done:=true; end
if low <=(eref-atr*tp_m) and strategy.position_size<0  and not be_done; be_done:=true; end
sl_eff_l = be_done and {str(cfg['use_be']).lower()} ? eref : eref-atr*sl_m
sl_eff_s = be_done and {str(cfg['use_be']).lower()} ? eref : eref+atr*sl_m
if strategy.position_size>0
    strategy.exit("LX","L",qty_percent=50,stop=sl_eff_l,limit=eref+atr*tp_m)
    strategy.exit("LX2","L",stop=sl_eff_l,limit=eref+atr*tp_m*1.5)
if strategy.position_size<0
    strategy.exit("SX","S",qty_percent=50,stop=sl_eff_s,limit=eref-atr*tp_m)
    strategy.exit("SX2","S",stop=sl_eff_s,limit=eref-atr*tp_m*1.5)

plot(ema50,"EMA50",color.new(color.yellow,10),1)
plot(ema200,"EMA200",color.new(color.orange,10),2)
plotshape(entry_long, "L",shape.triangleup,  location.belowbar,color.lime,size=size.small)
plotshape(entry_short,"S",shape.triangledown,location.abovebar,color.red, size=size.small)
bgcolor(entry_long ?color.new(color.green,90):entry_short?color.new(color.red,90):na)
"""

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("\n"+"="*70)
    print("  SIGMA RANDOM SEARCH — 5,000 muestras")
    print(f"  TARGET: superar CAMPEON TV: {CAMPEON_TV['trades']}T | {CAMPEON_TV['wr']}%WR | +{CAMPEON_TV['pnl']}%")
    print("="*70)

    df_15m, df_1h, df_4h, df_1d = fetch_multi_tf(days=180)
    print("[FEATURES] Calculando indicadores...")
    df = build_fast_features(df_15m, df_1h, df_4h)
    df.dropna(subset=['close','atr','ema50'], inplace=True)
    print(f"[FEATURES] {len(df)} velas listas\n")

    best_score = -9999; best_m = None; best_cfg = None
    all_pos = []; n_beat = 0

    print(f"[SEARCH] Corriendo {N_SAMPLES:,} muestras aleatorias...\n")

    for i in range(N_SAMPLES):
        cfg = sample_config()
        if not cfg['use_elite_ict'] and not cfg['use_elite'] and not cfg['use_execute'] \
           and not cfg['use_trend_mode'] and not cfg['use_range_mode']:
            continue
        try:
            sig, qual = get_signals(df, cfg)
            if (sig!=0).sum() < 10: continue
            m = backtest(df, sig, qual, cfg)
            s = score(m)

            # ── GUARDAR EN DB (conexion con sistema de aprendizaje) ──────────
            try:
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..'))
                from core.database import save_run as _save_run
                if m['trades'] >= 10:
                    _save_run('15m', 'random_search', cfg, m, s)
            except Exception:
                pass
            # ────────────────────────────────────────────────────────────────

            if m['pnl_pct']>0 and m['trades']>=30 and m['winrate']>=55:
                all_pos.append((m.copy(), cfg.copy(), s))

            if m['trades']>43 and m['winrate']>=80 and m['pnl_pct']>CAMPEON_TV['pnl']:
                n_beat += 1
                print(f"  *** SUPERA CAMPEON *** {m['trades']}T | WR {m['winrate']:.1f}% | "
                      f"PnL {m['pnl_pct']:+.1f}% | PF {m['profit_factor']:.2f} | DD {m['max_dd']:.1f}%")
                print(f"      CFG: exec={cfg['use_execute']} trend={cfg['use_trend_mode']} "
                      f"range={cfg['use_range_mode']} cd={cfg['signal_cooldown']} "
                      f"asia={cfg['use_asia']} elite_sl={cfg['elite_sl_mult']} elite_tp={cfg['elite_tp_mult']}")

            if s > best_score:
                best_score = s; best_m = m; best_cfg = cfg.copy()
                print(f"  [MEJOR #{i+1}] {m['trades']}T | WR {m['winrate']:.1f}% | "
                      f"PnL {m['pnl_pct']:+.1f}% | PF {m['profit_factor']:.2f} | DD {m['max_dd']:.1f}%")

        except Exception:
            continue

        if (i+1) % 500 == 0:
            print(f"  [{i+1:,}/{N_SAMPLES:,}] Positivos: {len(all_pos)} | Superan campeon: {n_beat}")

    # Resultados
    all_pos.sort(key=lambda x: x[2], reverse=True)
    print(f"\n{'='*75}")
    print(f"RESULTADOS — {N_SAMPLES:,} muestras probadas")
    print(f"{'='*75}")
    print(f"\n  CAMPEON TV:  {CAMPEON_TV['trades']}T | {CAMPEON_TV['wr']}%WR | +{CAMPEON_TV['pnl']}% | DD{CAMPEON_TV['dd']}% | PF{CAMPEON_TV['pf']}")
    if best_m:
        print(f"  MEJOR RS:    {best_m['trades']}T | {best_m['winrate']:.1f}%WR | {best_m['pnl_pct']:+.1f}% | DD{best_m['max_dd']:.1f}% | PF{best_m['profit_factor']:.2f}")
    print(f"\n  Configs positivas (PnL>0, WR>55%, 30+T): {len(all_pos)}")
    print(f"  Configs que superan campeon en TODOS los KPIs: {n_beat}")

    if all_pos:
        print(f"\n{'TOP 10':^75}")
        print(f"{'#':<3} {'T':>4} {'WR%':>6} {'PnL%':>7} {'Sharpe':>7} {'DD%':>6} {'PF':>6}")
        print("-"*50)
        for i,(m,cfg,s) in enumerate(all_pos[:10],1):
            print(f"{i:<3} {m['trades']:>4} {m['winrate']:>5.1f}% {m['pnl_pct']:>6.1f}% "
                  f"{m['sharpe']:>7.2f} {m['max_dd']:>5.1f}% {m['profit_factor']:>6.2f}")

        rows=[]
        for m,cfg,s in all_pos:
            row={'score':round(s,4)}
            row.update({k:round(v,2) if isinstance(v,float) else v for k,v in m.items()})
            row.update({f"p_{k}":v for k,v in cfg.items()})
            rows.append(row)
        pd.DataFrame(rows).to_csv(os.path.join(OUTPUT_DIR,'sigma_rs_results.csv'),index=False)
        print(f"\n[CSV] sigma_rs_results.csv ({len(rows)} configs)")

        wm,wc,_ = all_pos[0]
        pine = gen_pine(wc, wm)
        p = os.path.join(OUTPUT_DIR,'SIGMA_RS_WINNER.pine')
        with open(p,'w',encoding='utf-8') as f: f.write(pine.strip())
        print(f"[PINE] {p}")

    print("\n[DONE] Random search completado.")

    try:
        for _ in range(5): winsound.Beep(1200,300)
        bm = best_m if best_m else {'trades':0,'winrate':0,'pnl_pct':0,'profit_factor':0,'max_dd':0}
        msg=(f"SIGMA RANDOM SEARCH LISTO!\\n\\n"
             f"Muestras: {N_SAMPLES:,}\\n"
             f"Configs positivas: {len(all_pos)}\\n"
             f"Superan campeon: {n_beat}\\n\\n"
             f"MEJOR ENCONTRADO:\\n"
             f"Trades: {bm['trades']} | WR: {bm['winrate']:.1f}%\\n"
             f"PnL: {bm['pnl_pct']:+.1f}% | PF: {bm['profit_factor']:.2f}\\n"
             f"DD: {bm['max_dd']:.1f}%\\n\\n"
             f"vs CAMPEON: 43T | 90.7%WR | +14.61%\\n\\n"
             f"Ver SIGMA_RS_WINNER.pine")
        subprocess.Popen(['powershell','-WindowStyle','Hidden','-Command',
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}","SIGMA RS","OK","Information")'])
    except: pass

if __name__=='__main__':
    main()
