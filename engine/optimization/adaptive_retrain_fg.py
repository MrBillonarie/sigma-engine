"""
Adaptive Retrain con F&G<60 Pre-Filtrado.
Optimiza ICT params solo en dias donde F&G < umbral.
Objetivo: aumentar ventanas positivas de 44% a 50%+
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, numpy as np, pandas as pd, optuna
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
from core.data import fetch_ohlcv
from core.features import build_features
from core.signals import get_signals
from core.backtest import run_backtest, calc_metrics

optuna.logging.set_verbosity(optuna.logging.WARNING)

OUTPUT_DIR = Path(__file__).parent.parent.parent
CAPITAL    = 1000.0
COMMISSION = 0.0004; SLIPPAGE = 0.0001; COST = COMMISSION + SLIPPAGE
N_TRIALS   = 200
TRAIN_DAYS = 180
TEST_DAYS  = 30
STEP_DAYS  = 30
FG_THRESH  = 60  # no operar cuando F&G > este valor


def fast_backtest(df_w, sigs, qual, cfg):
    closes = df_w["close"].values; highs = df_w["high"].values
    lows   = df_w["low"].values;   atrs  = df_w["atr"].values
    s = sigs.values
    q = qual.map({"ELITE_ICT":3,"ELITE":2,"EXECUTE":1}).fillna(0).astype(int).values
    e_sl = cfg.get("elite_sl_mult", 2.4); e_tp = cfg.get("elite_tp_mult", 2.0)
    x_sl = cfg.get("exec_sl_mult",  1.9); x_tp = cfg.get("exec_tp_mult",  3.5)
    risk = cfg.get("risk_pct", 1.5);      q65  = cfg.get("qty_tp1", 0.65)

    cap = CAPITAL; pos = 0
    entry = sl = tp1 = tp2 = sz = sz2 = 0.0
    tp1_done = False; trades = []

    for i in range(1, len(closes)):
        pr = closes[i]; atr = atrs[i-1]; h_ = highs[i]; lo = lows[i]
        si = s[i-1]; qi = q[i-1]
        if pos != 0:
            closed = False; pnl = 0.0
            if pos == 1:
                if lo <= sl:
                    pnl = (sz+sz2)*(sl-entry) - (sz+sz2)*(entry+sl)*COST; closed = True
                elif h_ >= tp1 and not tp1_done:
                    p1 = sz*(tp1-entry) - sz*(entry+tp1)*COST; cap += p1
                    trades.append(p1 > 0); sz = 0; tp1_done = True
                elif h_ >= tp2:
                    pnl = sz2*(tp2-entry) - sz2*(entry+tp2)*COST; closed = True
            else:
                if h_ >= sl:
                    pnl = (sz+sz2)*(entry-sl) - (sz+sz2)*(entry+sl)*COST; closed = True
                elif lo <= tp1 and not tp1_done:
                    p1 = sz*(entry-tp1) - sz*(entry+tp1)*COST; cap += p1
                    trades.append(p1 > 0); sz = 0; tp1_done = True
                elif lo <= tp2:
                    pnl = sz2*(entry-tp2) - sz2*(entry+tp2)*COST; closed = True
            if not closed and si == -pos:
                rem = sz+sz2
                pnl = pos*rem*(pr-entry) - rem*(entry+pr)*COST; closed = True
            if closed:
                cap += pnl; trades.append(pnl > 0); pos = 0; tp1_done = False

        if pos == 0 and si != 0 and cap > 50:
            is_el = qi >= 2; sl_m = e_sl if is_el else x_sl; tp_m = e_tp if is_el else x_tp
            rsl = atr * sl_m
            if rsl <= 0: continue
            tsz = (cap * risk/100) / rsl; sz = tsz * q65; sz2 = tsz * (1-q65)
            pos = si; entry = pr
            sl  = entry - rsl if pos == 1 else entry + rsl
            tp1 = entry + atr*tp_m if pos == 1 else entry - atr*tp_m
            tp2 = entry + atr*tp_m*1.5 if pos == 1 else entry - atr*tp_m*1.5
            tp1_done = False

    if not trades or len(trades) < 5:
        return -9999
    wins = sum(1 for t in trades if isinstance(t, bool) and t or
                                    isinstance(t, float) and t > 0)
    wr = wins / len(trades)
    days = (df_w.index[-1] - df_w.index[0]).days
    cagr = ((cap/CAPITAL)**(365.25/max(days,1)) - 1)*100
    if cagr <= 0:
        return cagr * 0.1
    return 0.4*(wr-0.45)/0.4 + 0.6*min(cagr,60)/60


def optimize_window_fg(df_train, fg_mask_train):
    """Optimiza en ventana de entrenamiento con F&G mask aplicada."""
    def objective(trial):
        cfg = {
            "use_execute":    trial.suggest_categorical("use_execute",  [True, False]),
            "use_trend":      trial.suggest_categorical("use_trend",    [True, False]),
            "use_range":      trial.suggest_categorical("use_range",    [True, False]),
            "use_sess_b":     trial.suggest_categorical("use_sess_b",   [True, False]),
            "use_asia":       trial.suggest_categorical("use_asia",     [True, False]),
            "allow_friday":   trial.suggest_categorical("allow_friday", [True, False]),
            "req_htf2":       trial.suggest_categorical("req_htf2",     [True, True, False]),
            "adx_min":        trial.suggest_int("adx_min",        12, 28),
            "hurst_t":        trial.suggest_float("hurst_t",     0.50, 0.62, step=0.01),
            "adx_t":          trial.suggest_int("adx_t",          18, 35),
            "elite_sl_mult":  trial.suggest_float("elite_sl_mult",1.0, 2.5, step=0.1),
            "elite_tp_mult":  trial.suggest_float("elite_tp_mult",1.5, 5.0, step=0.25),
            "exec_sl_mult":   trial.suggest_float("exec_sl_mult", 1.2, 2.5, step=0.1),
            "exec_tp_mult":   trial.suggest_float("exec_tp_mult", 1.5, 4.5, step=0.25),
            "risk_pct":       trial.suggest_float("risk_pct",     0.5, 2.0, step=0.1),
            "qty_tp1":        trial.suggest_float("qty_tp1",      0.35, 0.65, step=0.05),
            "signal_cooldown":trial.suggest_int("signal_cooldown", 4, 22),
            "temp_min":       trial.suggest_int("temp_min",        5, 22),
            "temp_max":       trial.suggest_int("temp_max",       72, 98),
            "ofi_threshold":  trial.suggest_float("ofi_threshold",0.35, 0.75, step=0.05),
        }
        try:
            sigs, qual = get_signals(df_train, cfg)
            sigs = sigs.copy(); sigs[~fg_mask_train] = 0  # F&G mask
            if (sigs != 0).sum() < 5:
                return -9999
            return fast_backtest(df_train, sigs, qual, cfg)
        except:
            return -9999

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=40)
    )
    study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=False)
    return study.best_params, study.best_value


def run():
    print(f"\n{'='*60}")
    print(f"  ADAPTIVE RETRAIN con F&G<{FG_THRESH} PRE-FILTRADO")
    print(f"  Train: {TRAIN_DAYS}d | Test: {TEST_DAYS}d | {N_TRIALS} trials/ventana")
    print(f"{'='*60}")

    print("\n[DATA] Cargando 8.7 anos...")
    df_1h = fetch_ohlcv(tf="1h", days=3200)
    df_4h = fetch_ohlcv(tf="4h", days=3200)
    df_1d = fetch_ohlcv(tf="1d", days=3200)
    df = build_features(df_1h, {"4h": df_4h, "1d": df_1d})
    df.dropna(subset=["close","atr","ema50"], inplace=True)

    fg_ok = df["fg_value"].fillna(50) <= FG_THRESH if "fg_value" in df.columns \
            else pd.Series(True, index=df.index)
    pct = fg_ok.mean()*100
    print(f"  {len(df):,} velas | F&G<{FG_THRESH}: {pct:.0f}% del tiempo\n")

    # Ventanas de reentrenamiento
    total = len(df)
    train_b = int(TRAIN_DAYS*24); test_b = int(TEST_DAYS*24); step_b = int(STEP_DAYS*24)
    min_start = int(365*24*2)  # esperar 2 anos para tener datos de F&G
    windows = []
    i = min_start
    while i + train_b + test_b <= total:
        windows.append((i, i+train_b, i+train_b+test_b))
        i += step_b

    print(f"  {len(windows)} ventanas | primeras 60 para test rapido\n")
    print(f"  {'Periodo':<12} {'Train':>7} {'Test':>8}  {'St':>2}  {'T':>4}  {'WR':>6}")
    print("  " + "-"*48)

    pos_count = 0; total_count = 0; cagr_list = []; wr_list = []

    for ws, wm, we in windows[:60]:
        df_tr = df.iloc[ws:wm]
        df_te = df.iloc[wm:we]
        fg_tr = fg_ok.iloc[ws:wm]
        fg_te = fg_ok.iloc[wm:we]

        best_cfg, best_score = optimize_window_fg(df_tr, fg_tr)

        # Evaluar en test con F&G mask
        sigs_t, qual_t = get_signals(df_te, best_cfg)
        sigs_t = sigs_t.copy(); sigs_t[~fg_te] = 0
        tr_t, eq_t = run_backtest(df_te, sigs_t, qual_t, best_cfg)
        m_t = calc_metrics(tr_t, eq_t, days_period=TEST_DAYS)
        cagr_t = m_t.get("cagr", m_t.get("pnl_pct", 0))
        wr_t = m_t["winrate"]
        trades_t = m_t["trades"]

        # Score del test (positivo = OOS > 0)
        positive = cagr_t > 0
        if positive: pos_count += 1
        total_count += 1
        cagr_list.append(cagr_t); wr_list.append(wr_t)

        period = df_te.index[0].strftime("%Y-%m")
        st = "OK" if positive else "XX"
        print(f"  {period:<12} {best_score:>+7.2f} {cagr_t:>+7.2f}  {st}  {trades_t:>4}  {wr_t:>5.1f}%")

    print(f"\n{'='*60}")
    pct_pos = pos_count/total_count*100 if total_count > 0 else 0
    cagr_avg = np.mean(cagr_list) if cagr_list else 0
    wr_avg   = np.mean([w for w in wr_list if w > 0]) if wr_list else 0
    print(f"  Ventanas positivas: {pos_count}/{total_count} ({pct_pos:.0f}%)")
    print(f"  CAGR promedio test: {cagr_avg:+.1f}%")
    print(f"  WR promedio test (pos):  {wr_avg:.1f}%")

    improvement = pct_pos - 44.0
    print(f"  Mejora vs sin F&G: {improvement:+.1f}% ({44}% -> {pct_pos:.0f}%)")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
