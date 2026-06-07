"""
SIGMA ENGINE — Adaptive Walk-Forward Retraining

En vez de UN modelo estatico optimizado en todos los datos historicos,
este sistema:
  1. Divide el historico en ventanas de 30 dias
  2. Para cada ventana de test, entrena en los 180 dias anteriores
  3. Guarda los parametros optimos de cada sub-periodo
  4. En produccion: reentrenar cada 30 dias automaticamente

Ventaja critica: el modelo se adapta al regimen actual del mercado.
Cuando BTC cambia de RANGE a TREND, el modelo recalibra en 30 dias.

Output:
  - models/1h/adaptive_params.json: parametros por periodo
  - models/1h/current_params.json: params para usar HOY (mas recientes)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
from datetime import timedelta

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
COMMISSION  = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
CAPITAL     = 1000.0
TRAIN_DAYS  = 180   # 6 meses de entrenamiento
TEST_DAYS   = 30    # 1 mes de test
N_TRIALS    = 300   # trials Bayesian por ventana


def load_data():
    from core.data import fetch_ohlcv
    from core.features import build_features
    max_p = OUTPUT_DIR / "models" / "data_1h_max.csv"
    df_b  = pd.read_csv(max_p, index_col=0, parse_dates=True)
    df_b.index.name = "timestamp"
    df_4h = fetch_ohlcv(tf="4h", days=1500)
    df_1d = fetch_ohlcv(tf="1d", days=1500)
    df = build_features(df_b, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)
    return df


def backtest_fast(df, signals, quality, cfg):
    """Backtest rapido para trials Bayesian."""
    closes=df["close"].to_numpy(); highs=df["high"].to_numpy()
    lows=df["low"].to_numpy();     atrs=df["atr"].to_numpy()
    sigs=signals.to_numpy()
    quals=quality.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).to_numpy()

    e_sl=cfg.get("elite_sl_mult",2.4); e_tp=cfg.get("elite_tp_mult",2.0)
    x_sl=cfg.get("exec_sl_mult",1.9);  x_tp=cfg.get("exec_tp_mult",3.5)
    risk=cfg.get("risk_pct",1.5);      q65=cfg.get("qty_tp1",0.65)

    cap=CAPITAL; eq=[cap]; pos=0
    entry=sl=tp1=tp2=sz=sz2=0.0; tp1_done=False; trades=[]

    for i in range(1, len(closes)):
        pr=closes[i]; atr=atrs[i-1]; h_=highs[i]; lo=lows[i]
        s=sigs[i-1]; q=quals[i-1]
        if pos!=0:
            closed=False; pnl=0.0
            if pos==1:
                if lo<=sl: pnl=(sz+sz2)*(sl-entry)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif h_>=tp1 and not tp1_done:
                    p1=sz*(tp1-entry)-sz*(entry+tp1)*COST; cap+=p1
                    trades.append(p1>0); sz=0; tp1_done=True
                elif h_>=tp2: pnl=sz2*(tp2-entry)-sz2*(entry+tp2)*COST; closed=True
            else:
                if h_>=sl: pnl=(sz+sz2)*(entry-sl)-(sz+sz2)*(entry+sl)*COST; closed=True
                elif lo<=tp1 and not tp1_done:
                    p1=sz*(entry-tp1)-sz*(entry+tp1)*COST; cap+=p1
                    trades.append(p1>0); sz=0; tp1_done=True
                elif lo<=tp2: pnl=sz2*(entry-tp2)-sz2*(entry+tp2)*COST; closed=True
            if not closed and s==-pos:
                rem=sz+sz2; pnl=pos*rem*(pr-entry)-rem*(entry+pr)*COST; closed=True
            if closed: cap+=pnl; trades.append(pnl>0); pos=0; tp1_done=False
        if pos==0 and s!=0 and cap>50:
            is_el=q>=2; sl_m=e_sl if is_el else x_sl; tp_m=e_tp if is_el else x_tp
            pos=s; entry=pr; rsl=atr*sl_m
            sl=entry-rsl if pos==1 else entry+rsl
            tp1=entry+atr*tp_m if pos==1 else entry-atr*tp_m
            tp2=entry+atr*tp_m*1.5 if pos==1 else entry-atr*tp_m*1.5
            tsz=(cap*risk/100)/rsl if rsl>0 else 0; sz=tsz*q65; sz2=tsz*(1-q65); tp1_done=False
        eq.append(cap)

    if len(trades)<3: return -9999
    wins=sum(trades); n=len(trades); wr=wins/n
    eq_s=pd.Series(eq[:len(df)],index=df.index[:len(eq)])
    peak=eq_s.cummax(); dd=(eq_s-peak)/peak*100
    days=(eq_s.index[-1]-eq_s.index[0]).days
    cagr=((eq_s.iloc[-1]/CAPITAL)**(365.25/max(days,1))-1)*100
    if cagr<=0: return -abs(cagr)*0.1
    cal=cagr/abs(dd.min()) if dd.min()<0 else 0
    return 0.4*(wr-0.45)/0.3 + 0.3*min(cal,5)/5 + 0.3*min(cagr,60)/60


def optimize_window(df_train, n_trials=N_TRIALS):
    """Bayesian Optuna rapido para una ventana de entrenamiento."""
    from core.signals import get_signals

    def objective(trial):
        cfg = {
            "use_execute":     trial.suggest_categorical("use_execute",   [True, False]),
            "use_trend":       trial.suggest_categorical("use_trend",     [True, False]),
            "use_range":       trial.suggest_categorical("use_range",     [True, False]),
            "use_watch":       trial.suggest_categorical("use_watch",     [True, False]),
            "use_sess_b":      trial.suggest_categorical("use_sess_b",    [True, False]),
            "use_asia":        trial.suggest_categorical("use_asia",      [True, False]),
            "allow_friday":    trial.suggest_categorical("allow_friday",  [True, False]),
            "req_htf2":        trial.suggest_categorical("req_htf2",      [True, False]),
            "use_be":          trial.suggest_categorical("use_be",        [True, False]),
            "adx_min":         trial.suggest_int("adx_min",        12, 30),
            "hurst_t":         trial.suggest_float("hurst_t",      0.50, 0.62, step=0.01),
            "adx_t":           trial.suggest_int("adx_t",          18, 32),
            "hurst_r":         trial.suggest_float("hurst_r",      0.44, 0.52, step=0.01),
            "adx_r":           trial.suggest_int("adx_r",          14, 24),
            "temp_min":        trial.suggest_int("temp_min",        5, 25),
            "temp_max":        trial.suggest_int("temp_max",        75, 100),
            "ofi_threshold":   trial.suggest_float("ofi_threshold", 0.35, 0.75, step=0.05),
            "elite_sl_mult":   trial.suggest_float("elite_sl_mult", 1.0, 2.5, step=0.1),
            "elite_tp_mult":   trial.suggest_float("elite_tp_mult", 1.5, 4.0, step=0.25),
            "exec_sl_mult":    trial.suggest_float("exec_sl_mult",  1.2, 2.5, step=0.1),
            "exec_tp_mult":    trial.suggest_float("exec_tp_mult",  1.5, 4.0, step=0.25),
            "risk_pct":        trial.suggest_float("risk_pct",      0.3, 1.5, step=0.1),
            "qty_tp1":         trial.suggest_float("qty_tp1",       0.35, 0.65, step=0.05),
            "signal_cooldown": trial.suggest_int("signal_cooldown", 2, 20),
        }
        try:
            sigs, qual = get_signals(df_train, cfg)
            if (sigs!=0).sum() < 5: return -9999
            return backtest_fast(df_train, sigs, qual, cfg)
        except: return -9999

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_params, study.best_value


def run():
    print(f"\n{'='*65}")
    print(f"  SIGMA — ADAPTIVE WALK-FORWARD RETRAINING 1H")
    print(f"  Train: {TRAIN_DAYS}d | Test: {TEST_DAYS}d | {N_TRIALS} trials/ventana")
    print(f"{'='*65}")

    print("\n[DATA] Cargando datos...")
    df = load_data()
    days_total = (df.index[-1]-df.index[0]).days
    print(f"  {len(df):,} velas | {days_total} dias")

    # Construir ventanas de entrenamiento
    start = df.index[0] + timedelta(days=TRAIN_DAYS)
    end   = df.index[-1] - timedelta(days=TEST_DAYS)

    windows = []
    cur = start
    while cur <= end:
        train_start = cur - timedelta(days=TRAIN_DAYS)
        train_end   = cur
        test_end    = cur + timedelta(days=TEST_DAYS)
        windows.append((train_start, train_end, test_end))
        cur += timedelta(days=TEST_DAYS)

    print(f"  {len(windows)} ventanas de reentrenamiento\n")

    results = []; period_params = []
    from core.signals import get_signals

    print(f"  {'Periodo':<12} {'Trials':>6} {'Train':>7} {'Test':>7} {'T':>4} {'WR':>6}")
    print("  " + "-"*50)

    for i, (tr_s, tr_e, te_e) in enumerate(windows):
        df_train = df[(df.index>=tr_s)&(df.index<tr_e)]
        df_test  = df[(df.index>=tr_e)&(df.index<te_e)]
        if len(df_train)<500 or len(df_test)<50: continue

        # Optimizar en ventana de train
        best_cfg, best_score = optimize_window(df_train, N_TRIALS)

        # Evaluar en test
        sigs_t, qual_t = get_signals(df_test, best_cfg)
        score_test = backtest_fast(df_test, sigs_t, qual_t, best_cfg)

        # Calcular metricas test
        sigs_te, qual_te = get_signals(df_test, best_cfg)

        from analysis.walk_forward_real import backtest_window
        m_test = backtest_window(df_test, sigs_te, qual_te, best_cfg)

        period_label = tr_e.strftime("%Y-%m")
        tr_score_str = f"{best_score:+.2f}"
        te_score_str = f"{score_test:+.2f}"
        t_str = str(m_test["trades"]) if m_test else "0"
        wr_str = f"{m_test['wr']:.1f}%" if m_test else "N/A"

        print(f"  {period_label:<12} {best_score:>+6.2f} {score_test:>+6.2f}  "
              f"{'OK' if score_test>0 else 'XX'} {t_str:>4} {wr_str:>6}")

        period_params.append({
            "period": period_label,
            "train_start": str(tr_s.date()),
            "train_end": str(tr_e.date()),
            "test_end": str(te_e.date()),
            "train_score": round(best_score,4),
            "test_score": round(score_test,4),
            "params": best_cfg,
            "test_metrics": m_test
        })
        if m_test: results.append(m_test)

    positive = [r for r in results if r["cagr"]>0]
    print(f"\n{'='*65}")
    print(f"  Ventanas positivas: {len(positive)}/{len(results)} ({len(positive)/max(len(results),1)*100:.0f}%)")
    if results:
        avg_cagr = sum(r["cagr"] for r in results)/len(results)
        avg_wr   = sum(r["wr"] for r in results)/len(results)
        print(f"  CAGR promedio test: {avg_cagr:+.1f}%")
        print(f"  WR promedio test:   {avg_wr:.1f}%")

    # Params mas recientes = los que usar HOY
    if period_params:
        current = period_params[-1]
        out_dir = OUTPUT_DIR/"models"/"1h"

        # Convertir tipos numpy a Python para JSON
        def make_serializable(obj):
            if isinstance(obj, dict):
                return {k: make_serializable(v) for k,v in obj.items()}
            if isinstance(obj, list):
                return [make_serializable(v) for v in obj]
            if isinstance(obj, (np.bool_, bool)):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return obj

        period_params_clean = make_serializable(period_params)
        with open(out_dir/"adaptive_params.json","w") as f:
            json.dump(period_params_clean, f, indent=2)
        with open(out_dir/"current_params.json","w") as f:
            json.dump(current["params"], f, indent=2)
        print(f"\n  [SAVED] models/1h/adaptive_params.json")
        print(f"  [SAVED] models/1h/current_params.json  (usar para trading)")
        print(f"  Periodo actual: {current['period']} | Train score: {current['train_score']:+.4f}")

    print(f"{'='*65}")
    return period_params


if __name__ == "__main__":
    run()
