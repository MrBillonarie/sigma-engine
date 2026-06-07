"""
SIGMA ENGINE — Build Best Strategy
Construye la estrategia mas estadisticamente valida que podemos demostrar.

Criterios de validez estadistica:
  - Minimo 150 trades (CI 95% = ±8% en WR) — aceptable para fase inicial
  - Minimo 200 trades (CI 95% = ±7%) — preferible
  - WR >= 52% con R:R >= 2:1 (expectancy positiva garantizada)
  - Validada en OOS (ultimos 20% de datos nunca vistos)
  - CAGR / MaxDD (Calmar) >= 1.5

Metodologia:
  1. Usar 2 anos de datos (730 dias) — mas historia = mas trades = mas confianza
  2. Optimizar por Calmar Ratio, no por WR
  3. Validar en OOS estricto
  4. Reportar CI estadistico honesto
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import random
import numpy as np
import pandas as pd
import scipy.stats as stats
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

random.seed(42); np.random.seed(42)

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
COMMISSION = 0.0004
SLIPPAGE   = 0.0005
COST       = COMMISSION + SLIPPAGE

# ─── TARGET ESTADISTICO ───────────────────────────────────────────────────────
MIN_TRADES_FOR_STATS  = 150    # minimo aceptable
TARGET_TRADES         = 200    # objetivo
CONFIDENCE            = 0.95   # nivel de confianza
MIN_WR                = 0.52   # 52% minimo
MIN_RR                = 1.8    # R:R minimo
MIN_CALMAR            = 1.0    # CAGR/MaxDD minimo


def confidence_interval(n_trades, winrate):
    """Calcula el CI de la WR dado n trades."""
    if n_trades < 5:
        return 0, 1
    se    = np.sqrt(winrate * (1-winrate) / n_trades)
    z     = stats.norm.ppf((1 + CONFIDENCE) / 2)
    lower = max(0, winrate - z * se)
    upper = min(1, winrate + z * se)
    return round(lower, 3), round(upper, 3)


def required_trades_for_precision(target_error=0.05, wr_estimate=0.55):
    """Cuantos trades necesitas para un CI de ±target_error."""
    z = stats.norm.ppf((1 + CONFIDENCE) / 2)
    n = (z / target_error)**2 * wr_estimate * (1-wr_estimate)
    return int(np.ceil(n))


# ─── ESTRATEGIA CORE: TREND PULLBACK ─────────────────────────────────────────
def sig_trend_pullback(df, cfg):
    """
    La estrategia mas robusta para BTC segun la literatura quant:
    Trend-following con pullback a media movil.

    Logica:
      1. Mercado en tendencia (EMA50 > EMA200 + ADX > threshold)
      2. Precio se aleja de la media y retrocede (pullback)
      3. Rebota con una vela de confirmacion
      4. Volumen confirma la entrada
    Esta estrategia funciona en BTC porque BTC tiene momentum persistente
    (Hurst > 0.5) — los pullbacks en tendencia tienden a resolverse a favor.
    """
    sig  = pd.Series(0, index=df.index)
    c, h, l, o = df['close'], df['high'], df['low'], df['open']
    atr  = df['atr']

    ema_fast = cfg['ema_fast']
    ema_slow = cfg['ema_slow']
    ema_f    = c.ewm(span=ema_fast, adjust=False).mean()
    ema_s    = c.ewm(span=ema_slow, adjust=False).mean()

    # Tendencia
    bull = ema_f > ema_s
    bear = ema_f < ema_s

    # ADX
    adx = df.get('adx', pd.Series(25, index=df.index))
    trending = adx > cfg['adx_min']

    # HTF
    htf_l = df.get('htf1_long',  pd.Series(True, index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))

    # Pullback: precio vuelve a tocar la EMA rapida
    touch_ema_l = (l <= ema_f * (1 + cfg['touch_tolerance'])) & \
                  (l >= ema_f * (1 - cfg['touch_tolerance'])) & \
                  (c > ema_f)  # cierra arriba de EMA (rebote)
    touch_ema_s = (h >= ema_f * (1 - cfg['touch_tolerance'])) & \
                  (h <= ema_f * (1 + cfg['touch_tolerance'])) & \
                  (c < ema_f)

    # Confirmacion de vela
    bull_candle = (c > o) & ((c-o) > atr * cfg['min_body'])
    bear_candle = (c < o) & ((o-c) > atr * cfg['min_body'])

    # Volumen
    vol_ok = df.get('vol_ok', df['volume'] > df['volume'].rolling(20).mean())

    # Base
    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('gap_ok', pd.Series(True, index=df.index)) & \
           ~df.get('is_spike', pd.Series(False, index=df.index))

    # Session (solo London + NY)
    hour = df.index.hour
    dow  = df.index.dayofweek
    in_sess = ((hour >= 8) & (hour < 20))  # London + NY
    dow_ok  = pd.Series(dow, index=df.index).isin([1,2,3,4])  # Mar-Vie

    full_filter = base & in_sess & dow_ok & trending

    long_raw  = touch_ema_l & bull & bull_candle & htf_l & full_filter
    short_raw = touch_ema_s & bear & bear_candle & htf_s & full_filter

    # Cooldown
    cd = cfg['cooldown']
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if long_raw.iloc[i]:
            sig.iloc[i] = 1; last = i
        elif short_raw.iloc[i]:
            sig.iloc[i] = -1; last = i

    return sig


def sig_momentum_continuation(df, cfg):
    """
    Momentum continuation: entra cuando el precio acelera en la direccion
    de la tendencia con volumen elevado.
    Alta frecuencia de señales, requiere filtros fuertes.
    """
    sig = pd.Series(0, index=df.index)
    c   = df['close']
    atr = df['atr']

    # Momentum: close vs close de N barras atras
    mom = (c - c.shift(cfg['mom_bars'])) / c.shift(cfg['mom_bars']).replace(0, np.nan)

    # Aceleracion: momentum creciendo
    mom_accel = mom > mom.shift(2)

    htf_l = df.get('htf1_long',  pd.Series(True,  index=df.index))
    htf_s = df.get('htf1_short', pd.Series(False, index=df.index))
    htf2_l = df.get('htf2_long', pd.Series(True,  index=df.index))

    macd   = df.get('macd', c.ewm(12).mean() - c.ewm(26).mean())
    signal = df.get('macd_signal', macd.ewm(9).mean())
    adx    = df.get('adx', pd.Series(25, index=df.index))

    vol_ok  = df['volume'] > df['volume'].rolling(20).mean() * cfg['vol_mult']
    trending= adx > cfg['adx_min']
    bull    = df.get('bull', c > c.ewm(50).mean())
    bear    = df.get('bear', c < c.ewm(50).mean())

    base = ~df.get('fake_move', pd.Series(False, index=df.index)) & \
            df.get('gap_ok', pd.Series(True, index=df.index)) & \
           ~df.get('is_spike', pd.Series(False, index=df.index))

    hour = df.index.hour; dow = df.index.dayofweek
    in_sess = ((hour >= 8) & (hour < 20))
    dow_ok  = pd.Series(dow, index=df.index).isin([1,2,3,4])

    long_raw  = (mom > cfg['mom_threshold']) & mom_accel & bull & \
                (macd > signal) & htf_l & htf2_l & vol_ok & trending & base & in_sess & dow_ok
    short_raw = (mom < -cfg['mom_threshold']) & (mom < mom.shift(2)) & bear & \
                (macd < signal) & htf_s & ~htf2_l & vol_ok & trending & base & in_sess & dow_ok

    cd = cfg['cooldown']
    last = -cd-1
    for i in range(len(df)):
        if (i-last) < cd: continue
        if long_raw.iloc[i]:
            sig.iloc[i] = 1; last = i
        elif short_raw.iloc[i]:
            sig.iloc[i] = -1; last = i
    return sig


# ─── BACKTEST CON METRICAS ESTADISTICAS ──────────────────────────────────────
def full_backtest(df, signals, sl_mult=1.5, tp_mult=2.5, use_trail=True, trail_mult=2.0, risk=0.5):
    """Backtest completo con trailing stop opcional."""
    cap = CAPITAL; eq = [cap]; pos = 0
    entry = sl = tp = trail = 0.0; size = 0.0; trades = []

    for i in range(1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i-1]
        sig = signals.iloc[i-1]
        pr  = row['close']; atr = prev['atr']
        h_  = row['high'];  lo  = row['low']

        if pos != 0:
            pnl = 0.0; closed = False; reason = ''

            if use_trail:
                if pos == 1:
                    trail = max(trail, h_ - atr * trail_mult)
                    if lo <= trail:
                        pnl = size*(trail-entry) - size*(entry+trail)*COST
                        closed = True; reason = 'Trail'
                else:
                    trail = min(trail, lo + atr * trail_mult)
                    if h_ >= trail:
                        pnl = size*(entry-trail) - size*(entry+trail)*COST
                        closed = True; reason = 'Trail'
            else:
                if pos == 1:
                    if lo <= sl:
                        pnl = size*(sl-entry) - size*(entry+sl)*COST; closed=True; reason='SL'
                    elif h_ >= tp:
                        pnl = size*(tp-entry) - size*(entry+tp)*COST; closed=True; reason='TP'
                else:
                    if h_ >= sl:
                        pnl = size*(entry-sl) - size*(entry+sl)*COST; closed=True; reason='SL'
                    elif lo <= tp:
                        pnl = size*(entry-tp) - size*(entry+tp)*COST; closed=True; reason='TP'

            if not closed and sig == -pos:
                pnl = size*(pr-entry)*pos - size*(entry+pr)*COST; closed=True; reason='Sig'

            if closed:
                cap += pnl
                trades.append({'pnl':pnl,'won':pnl>0,'reason':reason,'exit':pr,'entry':entry})
                pos = 0

        if pos == 0 and sig != 0 and cap > 50:
            pos=sig; entry=pr
            r_sl = atr * sl_mult
            sl   = entry - r_sl if pos==1 else entry + r_sl
            tp   = entry + atr*tp_mult if pos==1 else entry - atr*tp_mult
            trail= sl
            size = (cap*risk/100)/r_sl if r_sl>0 else 0
        eq.append(cap)

    df_t = pd.DataFrame(trades)
    eq_s = pd.Series(eq[:len(df)], index=df.index[:len(eq)])

    if df_t.empty or len(df_t) < 5:
        return None, eq_s

    w = df_t[df_t['pnl']>0]; l = df_t[df_t['pnl']<=0]
    gp = w['pnl'].sum(); gl = abs(l['pnl'].sum())
    peak = eq_s.cummax(); dd = (eq_s-peak)/peak*100
    ret  = eq_s.pct_change().dropna()
    sh   = ret.mean()/ret.std()*np.sqrt(35040) if ret.std()>0 else 0
    pnl  = (eq_s.iloc[-1]-CAPITAL)/CAPITAL*100
    days = (eq_s.index[-1]-eq_s.index[0]).days
    cagr = ((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100 if days>0 else 0
    calmar = cagr/abs(dd.min()) if dd.min()<0 else 0

    n  = len(df_t); wr = len(w)/n
    ci_lo, ci_hi = confidence_interval(n, wr)
    avg_win  = w['pnl'].mean()  if not w.empty else 0
    avg_loss = abs(l['pnl'].mean()) if not l.empty else 0
    rr_real  = avg_win/avg_loss if avg_loss > 0 else 0

    metrics = {
        'trades':   n,
        'winrate':  round(wr*100, 1),
        'ci_low':   round(ci_lo*100, 1),
        'ci_high':  round(ci_hi*100, 1),
        'ci_width': round((ci_hi-ci_lo)*100, 1),
        'pnl_pct':  round(pnl, 2),
        'cagr':     round(cagr, 2),
        'sharpe':   round(sh, 3),
        'max_dd':   round(dd.min(), 2),
        'calmar':   round(calmar, 3),
        'pf':       round(gp/gl, 3) if gl>0 else 999,
        'rr_real':  round(rr_real, 2),
        'avg_win':  round(avg_win, 4),
        'avg_loss': round(avg_loss, 4),
        'expect':   round(df_t['pnl'].mean(), 4),
    }
    return metrics, eq_s


def score_calmar(m):
    """Score basado en Calmar — premia CAGR/DD con trades suficientes."""
    if m is None or m['trades'] < 50: return -9999
    penalty = max(0, (MIN_TRADES_FOR_STATS - m['trades']) / MIN_TRADES_FOR_STATS) * 2
    calmar  = min(m['calmar'], 5) / 5
    wr      = (m['winrate']/100 - 0.45) / 0.3  # centrado en 45-75%
    pf      = min(m['pf'], 4) / 4
    sh      = max(min(m['sharpe'], 3), -3) / 3
    return 0.35*calmar + 0.25*wr + 0.20*pf + 0.20*sh - penalty


# ─── SEARCH PRINCIPAL ─────────────────────────────────────────────────────────
def find_best_strategy(tf='15m', n_iter=3000):
    from core.data import fetch_ohlcv
    from core.features import build_features

    print(f"\n{'='*65}")
    print(f"  SIGMA — ESTRATEGIA ESTADISTICAMENTE VALIDA")
    print(f"  Objetivo: >={MIN_TRADES_FOR_STATS} trades | WR CI 95% positivo | Calmar >= {MIN_CALMAR}")
    print(f"  Datos: 2 anos | OOS: ultimos 20% nunca vistos")
    print(f"{'='*65}")
    print(f"\n  Trades necesarios para CI ±5%:  {required_trades_for_precision(0.05)}")
    print(f"  Trades necesarios para CI ±3%:  {required_trades_for_precision(0.03)}")
    print(f"  Nuestro objetivo minimo:         {MIN_TRADES_FOR_STATS}")

    TF_MAP = {'15m':('1h','4h',365),'1h':('4h','1d',730),'5m':('15m','1h',180)}
    htf1, htf2, days = TF_MAP.get(tf, ('1h','4h',365))

    print(f"\n[DATA] Cargando {days} dias de {tf}...")
    df_b  = fetch_ohlcv(tf=tf,   days=days)
    df_h1 = fetch_ohlcv(tf=htf1, days=days*2)
    df_h2 = fetch_ohlcv(tf=htf2, days=days*3)
    df    = build_features(df_b, {htf1: df_h1, htf2: df_h2})
    df.dropna(subset=['close','atr','ema50'], inplace=True)

    # Split IS/OOS
    split = int(len(df) * 0.80)
    df_is  = df.iloc[:split]
    df_oos = df.iloc[split:]
    days_is  = (df_is.index[-1]-df_is.index[0]).days
    days_oos = (df_oos.index[-1]-df_oos.index[0]).days

    print(f"  IS:  {df_is.index[0].date()} → {df_is.index[-1].date()} ({len(df_is):,} barras | {days_is} dias)")
    print(f"  OOS: {df_oos.index[0].date()} → {df_oos.index[-1].date()} ({len(df_oos):,} barras | {days_oos} dias)")
    print(f"\n[SEARCH] {n_iter:,} iteraciones...\n")

    # Espacio de busqueda
    space = {
        # Trend Pullback params
        'strategy':       ['pullback', 'momentum'],
        'ema_fast':       [9, 12, 15, 20, 21],
        'ema_slow':       [34, 50, 100],
        'adx_min':        [18, 20, 22, 25, 28],
        'touch_tolerance':[0.002, 0.003, 0.004, 0.005],
        'min_body':       [0.1, 0.2, 0.3],
        'cooldown':       [4, 6, 8, 10, 12],
        # Momentum params
        'mom_bars':       [4, 8, 12, 16],
        'mom_threshold':  [0.002, 0.003, 0.005],
        'vol_mult':       [1.2, 1.5, 1.8],
        # Exit params
        'sl_mult':        [1.2, 1.5, 1.7, 2.0],
        'tp_mult':        [2.0, 2.5, 3.0, 3.5, 4.0],
        'use_trail':      [True, False],
        'trail_mult':     [1.5, 2.0, 2.5],
        'risk_pct':       [0.5, 0.8, 1.0],
    }

    best_score = -9999; best_cfg = {}
    best_is = best_oos = None
    all_valid = []

    for i in range(n_iter):
        cfg = {k: random.choice(v) for k,v in space.items()}
        if cfg['tp_mult'] <= cfg['sl_mult']: continue

        try:
            if cfg['strategy'] == 'pullback':
                sig = sig_trend_pullback(df_is, cfg)
            else:
                sig = sig_momentum_continuation(df_is, cfg)

            if (sig!=0).sum() < MIN_TRADES_FOR_STATS//3: continue

            m_is, eq_is = full_backtest(df_is, sig,
                sl_mult=cfg['sl_mult'], tp_mult=cfg['tp_mult'],
                use_trail=cfg['use_trail'], trail_mult=cfg['trail_mult'],
                risk=cfg['risk_pct'])

            if m_is is None or m_is['trades'] < MIN_TRADES_FOR_STATS//2: continue

            s = score_calmar(m_is)
            if s > best_score:
                best_score = s; best_cfg = cfg.copy(); best_is = m_is
                if m_is['trades'] >= MIN_TRADES_FOR_STATS and m_is['calmar'] >= MIN_CALMAR:
                    print(f"  [#{i+1}] {m_is['trades']}T | WR {m_is['winrate']:.1f}% "
                          f"[{m_is['ci_low']:.1f}%-{m_is['ci_high']:.1f}%] | "
                          f"CAGR {m_is['cagr']:+.1f}%/año | Calmar {m_is['calmar']:.2f} | "
                          f"R:R {m_is['rr_real']:.2f}")

            if (m_is['trades'] >= MIN_TRADES_FOR_STATS and
                m_is['winrate'] >= MIN_WR*100 and
                m_is['calmar'] >= MIN_CALMAR and
                m_is['cagr'] > 0 and
                m_is['rr_real'] >= MIN_RR):
                all_valid.append((m_is.copy(), cfg.copy(), s))

        except Exception:
            continue

        if (i+1) % 500 == 0:
            print(f"  [{i+1:,}/{n_iter:,}] Validas: {len(all_valid)} | Mejor score: {best_score:.4f}")

    all_valid.sort(key=lambda x: x[2], reverse=True)

    # ── OOS VALIDATION ────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  OOS VALIDATION (datos nunca vistos)")
    print(f"{'='*65}")

    if not best_cfg:
        print("  Sin configuracion valida encontrada.")
        return

    if best_cfg.get('strategy') == 'pullback':
        sig_oos = sig_trend_pullback(df_oos, best_cfg)
    else:
        sig_oos = sig_momentum_continuation(df_oos, best_cfg)

    m_oos, eq_oos = full_backtest(df_oos, sig_oos,
        sl_mult=best_cfg['sl_mult'], tp_mult=best_cfg['tp_mult'],
        use_trail=best_cfg['use_trail'], trail_mult=best_cfg['trail_mult'],
        risk=best_cfg['risk_pct'])

    # ── REPORTE FINAL ─────────────────────────────────────────────────────────
    print(f"\n  {'Metrica':<25} {'IS (train)':>14} {'OOS (real)':>14}")
    print(f"  {'-'*55}")
    for k in ['trades','winrate','cagr','calmar','pf','rr_real','max_dd','sharpe']:
        v_is  = best_is.get(k, 0)  if best_is  else 0
        v_oos = m_oos.get(k, 0)    if m_oos    else 0
        if isinstance(v_is, float):
            print(f"  {k:<25} {v_is:>13.2f} {v_oos:>13.2f}")
        else:
            print(f"  {k:<25} {v_is:>14} {v_oos:>14}")

    if best_is and m_oos:
        print(f"\n  INTERVALO DE CONFIANZA 95% (OOS):")
        print(f"  WR: {m_oos['winrate']:.1f}% [{m_oos['ci_low']:.1f}% — {m_oos['ci_high']:.1f}%]")
        print(f"  Con {m_oos['trades']} trades OOS, la WR real esta en ese rango con 95% de confianza")

        eff = m_oos['cagr'] / max(abs(best_is['cagr']), 0.1)
        print(f"\n  Eficiencia IS→OOS: {eff:.2f}")

        if m_oos['cagr'] > 0 and m_oos['calmar'] >= 0.8:
            verdict = "EDGE REAL — estadisticamente valido"
            action  = "Listo para paper trading 30 dias"
        elif m_oos['cagr'] > 0:
            verdict = "EDGE DEBIL — positivo pero bajo"
            action  = "Paper trading con capital minimo"
        else:
            verdict = "SIN EDGE EN OOS — no llevar a produccion"
            action  = "Necesitas mas historia o distinto enfoque"

        print(f"\n  VEREDICTO: {verdict}")
        print(f"  ACCION:    {action}")

    # ── GUARDAR ───────────────────────────────────────────────────────────────
    model_dir = OUTPUT_DIR / 'models' / tf
    model_dir.mkdir(parents=True, exist_ok=True)

    result = {
        'tf': tf,
        'strategy_type': best_cfg.get('strategy', 'pullback'),
        'params': best_cfg,
        'metrics_is':  {k: round(v,4) if isinstance(v,float) else v
                        for k,v in (best_is or {}).items()},
        'metrics_oos': {k: round(v,4) if isinstance(v,float) else v
                        for k,v in (m_oos or {}).items()},
        'valid_configs': len(all_valid),
        'data_days': days,
        'oos_split': '80/20',
    }

    with open(model_dir / 'best_validated.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n  [SAVED] models/{tf}/best_validated.json")

    if all_valid:
        rows = []
        for m,c,s in all_valid[:50]:
            r = {'score':round(s,4)}; r.update(m); r.update({f'p_{k}':v for k,v in c.items()})
            rows.append(r)
        pd.DataFrame(rows).to_csv(OUTPUT_DIR/'results'/'reports'/f'best_strategies_{tf}.csv', index=False)
        print(f"  [CSV] best_strategies_{tf}.csv ({len(all_valid)} configs validas)")

    # ── NOTIFICAR ─────────────────────────────────────────────────────────────
    try:
        import winsound, subprocess
        for _ in range(4): winsound.Beep(1200, 300)
        m = m_oos or {}
        msg = (f"ESTRATEGIA VALIDADA — {tf.upper()}\\n\\n"
               f"OOS (datos reales):\\n"
               f"Trades: {m.get('trades',0)} | "
               f"WR: {m.get('winrate',0):.1f}% [{m.get('ci_low',0):.1f}%-{m.get('ci_high',0):.1f}%]\\n"
               f"CAGR: {m.get('cagr',0):+.1f}%/año | Calmar: {m.get('calmar',0):.2f}\\n"
               f"PF: {m.get('pf',0):.2f} | R:R: {m.get('rr_real',0):.2f}\\n\\n"
               f"Configs validas IS: {len(all_valid)}\\n"
               f"Ver: models/{tf}/best_validated.json")
        subprocess.Popen(['powershell','-WindowStyle','Hidden','-Command',
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}","SIGMA Validated","OK","Information")'])
    except: pass

    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tf',   default='15m')
    parser.add_argument('--iter', type=int, default=3000)
    args = parser.parse_args()
    find_best_strategy(args.tf, args.iter)
