"""
SIGMA ENGINE — Core Backtest Layer
Motor de backtest con: BE management, parciales, trailing, multi-calidad.
Metricas con CAGR y normalizacion por periodo.
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

COMMISSION   = CFG["execution"]["commission_pct"] / 100  # 0.04% taker por lado
SLIPPAGE     = CFG["execution"].get("slippage_bps", 5.0) / 10000  # 5 bps por lado
FUNDING_RATE = 0.0001   # 0.01% cada 8h = 0.0003/dia aprox (Binance Futuros promedio)
BARS_PER_8H  = {"1m": 480, "5m": 96, "15m": 32, "1h": 8, "4h": 2, "1d": 0.33}
CAPITAL      = CFG["capital"]["initial"]

# Costo total por lado (comision + slippage)
COST_PER_SIDE = COMMISSION + SLIPPAGE  # 0.04% + 0.05% = 0.09% por lado
# Round trip completo: entry + exit = 0.18%


def run_backtest(df, signals, quality, params=None):
    """
    Motor de backtest principal.

    df:       DataFrame con features (necesita columna 'atr')
    signals:  Series[int] — 1=long, -1=short, 0=flat
    quality:  Series[str] — calidad de cada señal
    params:   dict con overrides. Si None, usa settings.json.

    Retorna: (trades_df, equity_series)

    Mejoras v2:
      - Regime-adaptive SL/TP/risk (TREND_BULL: TP+20%, VOLATILE: riesgo x0.5, etc.)
      - Trailing stop ATR despues de TP1 (en vez de BE estatico)
      - Time stop: cierra al superar max_bars_in_trade barras
    """
    from core.regime import detect_regime, REGIME_PARAMS

    p = _merge_params(params)

    capital    = p["initial_capital"]
    risk_pct   = p["risk_pct"] / 100
    qty_tp1    = p["qty_tp1"]
    use_trail  = p.get("use_trail", True)
    trail_mult = p.get("trail_mult", 1.5)
    max_bars   = p.get("max_bars_in_trade", 0)   # 0 = sin limite

    equity  = [capital]
    trades  = []
    pos     = 0
    entry   = sl = tp1 = tp2 = 0.0
    size    = 0.0
    be_done = False
    partial = 0.0
    trail_sl      = 0.0
    bars_in_trade = 0
    entry_regime  = "TRANSITION"
    sl_m_used     = 1.5
    tp_m_used     = 2.5
    funding_acc   = 0.0   # acumula funding de TODAS las barras del trade (no solo la de cierre)
    mae           = 0.0   # Maximum Adverse Excursion  (peor punto contra nosotros, en precio)
    mfe           = 0.0   # Maximum Favorable Excursion (mejor punto a nuestro favor, en precio)
    entry_atr     = 1.0   # ATR en el momento de entrada (para normalizar MAE/MFE)

    avg_secs    = (df.index[-1] - df.index[0]).total_seconds() / max(len(df) - 1, 1)
    bars_per_8h = max(8 * 3600 / avg_secs, 0.1)

    # Usar funding rate real si esta disponible en el df (de data_futures)
    if "funding_rate" in df.columns:
        funding_arr = (df["funding_rate"].ffill().fillna(FUNDING_RATE) / bars_per_8h).values
    else:
        _fpb        = FUNDING_RATE / bars_per_8h
        funding_arr = np.full(len(df), _fpb)

    def trade_cost(sz, px_entry, px_exit):
        return sz * (px_entry + px_exit) * COST_PER_SIDE

    for i in range(1, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        sig  = signals.iloc[i - 1]
        qual = quality.iloc[i - 1]
        pr   = row["close"]
        atr_ = prev["atr"]
        h_   = row["high"]
        lo   = row["low"]

        if pos != 0:
            bars_in_trade += 1
            pnl          = 0.0
            closed       = False
            reason       = ""
            first_bar_be = False   # evita falso trail-exit en la misma barra del TP1

            funding_acc += size * entry * funding_arr[i - 1] * (1 if pos == 1 else -1)

            # MAE/MFE: distancia máxima en nuestra contra/favor durante el trade
            if pos == 1:
                mfe = max(mfe, h_ - entry)
                mae = min(mae, lo - entry)
            else:
                mfe = max(mfe, entry - lo)
                mae = min(mae, entry - h_)

            if pos == 1:
                # ── TP1 parcial ───────────────────────────────────────────────
                if not be_done and h_ >= tp1:
                    pnl         += size * qty_tp1 * (tp1 - entry) - trade_cost(size*qty_tp1, entry, tp1)
                    partial     += pnl
                    be_done      = True
                    trail_sl     = entry   # arranca el trail en breakeven
                    first_bar_be = True

                # ── Actualizar trailing stop ───────────────────────────────────
                if be_done and use_trail:
                    new_trail = h_ - atr_ * trail_mult
                    if new_trail > trail_sl:
                        trail_sl = new_trail

                # ── SL efectivo ───────────────────────────────────────────────
                if not be_done:
                    sl_eff = sl
                elif use_trail:
                    sl_eff = trail_sl
                else:
                    sl_eff = entry   # BE plano

                # ── TP2 techo fijo ────────────────────────────────────────────
                if be_done and not closed and h_ >= tp2:
                    rem   = 1 - qty_tp1
                    pnl  += rem * size * (tp2 - entry) - trade_cost(rem*size, entry, tp2)
                    closed = True; reason = "TP2"

                # ── SL / Trailing ─────────────────────────────────────────────
                if not closed and not first_bar_be and lo <= sl_eff:
                    rem   = (1 - qty_tp1) if be_done else 1.0
                    pnl  += rem * size * (sl_eff - entry) - trade_cost(rem*size, entry, sl_eff)
                    closed = True
                    reason = "TRAIL" if (be_done and use_trail) else ("BE" if be_done else "SL")

            else:  # SHORT
                # ── TP1 parcial ───────────────────────────────────────────────
                if not be_done and lo <= tp1:
                    pnl         += size * qty_tp1 * (entry - tp1) - trade_cost(size*qty_tp1, entry, tp1)
                    partial     += pnl
                    be_done      = True
                    trail_sl     = entry
                    first_bar_be = True

                # ── Actualizar trailing stop (baja con el precio) ─────────────
                if be_done and use_trail:
                    new_trail = lo + atr_ * trail_mult
                    if new_trail < trail_sl:
                        trail_sl = new_trail

                # ── SL efectivo ───────────────────────────────────────────────
                if not be_done:
                    sl_eff = sl
                elif use_trail:
                    sl_eff = trail_sl
                else:
                    sl_eff = entry

                # ── TP2 suelo fijo ────────────────────────────────────────────
                if be_done and not closed and lo <= tp2:
                    rem   = 1 - qty_tp1
                    pnl  += rem * size * (entry - tp2) - trade_cost(rem*size, entry, tp2)
                    closed = True; reason = "TP2"

                # ── SL / Trailing ─────────────────────────────────────────────
                if not closed and not first_bar_be and h_ >= sl_eff:
                    rem   = (1 - qty_tp1) if be_done else 1.0
                    pnl  += rem * size * (entry - sl_eff) - trade_cost(rem*size, entry, sl_eff)
                    closed = True
                    reason = "TRAIL" if (be_done and use_trail) else ("BE" if be_done else "SL")

            # ── Time stop ─────────────────────────────────────────────────────
            if not closed and max_bars > 0 and bars_in_trade >= max_bars:
                rem   = (1 - qty_tp1) if be_done else 1.0
                pnl  += rem * size * (pr - entry) * pos - trade_cost(rem*size, entry, pr)
                closed = True; reason = "TIME"

            # ── Señal contraria ───────────────────────────────────────────────
            if not closed and sig == -pos:
                rem   = (1 - qty_tp1) if be_done else 1.0
                pnl  += rem * size * (pr - entry) * pos - trade_cost(rem*size, entry, pr)
                closed = True; reason = "Signal"

            if closed:
                total    = partial + pnl - funding_acc   # descuenta funding de TODAS las barras
                capital += total
                trades.append({
                    "exit_time":  row.name,
                    "entry":      entry,
                    "exit":       pr,
                    "side":       "long" if pos == 1 else "short",
                    "pnl":        total,
                    "won":        total > 0,
                    "reason":     reason,
                    "quality":    qual,
                    "regime":     entry_regime,
                    "capital":    capital,
                    "sl_mult":    sl_m_used,
                    "tp_mult":    tp_m_used,
                    "bars_held":  bars_in_trade,
                    "mae_atr":    mae / max(entry_atr, 1e-9),   # normalizado por ATR de entrada
                    "mfe_atr":    mfe / max(entry_atr, 1e-9),
                })
                pos           = 0
                be_done       = False
                partial       = 0.0
                trail_sl      = 0.0
                bars_in_trade = 0
                funding_acc   = 0.0
                mae           = 0.0
                mfe           = 0.0

        # ── Nueva entrada ──────────────────────────────────────────────────────
        if pos == 0 and sig != 0 and capital > 50:
            regime = detect_regime(prev)
            r_cfg  = REGIME_PARAMS.get(regime, REGIME_PARAMS["TRANSITION"])

            sl_m2, tp_m2 = _get_sl_tp(qual, p)
            sl_m2 = round(sl_m2 * r_cfg["sl_mult"], 3)
            tp_m2 = round(tp_m2 * r_cfg["tp_mult"], 3)

            pos           = sig
            entry         = pr
            entry_regime  = regime
            be_done       = False
            partial       = 0.0
            trail_sl      = 0.0
            bars_in_trade = 0
            sl_m_used     = sl_m2
            tp_m_used     = tp_m2
            funding_acc   = 0.0
            mae           = 0.0
            mfe           = 0.0
            entry_atr     = atr_

            r_sl = atr_ * sl_m2
            sl   = entry - r_sl  if pos == 1 else entry + r_sl
            tp1  = entry + atr_ * tp_m2       if pos == 1 else entry - atr_ * tp_m2
            tp2  = entry + atr_ * tp_m2 * 1.5 if pos == 1 else entry - atr_ * tp_m2 * 1.5
            size = (capital * risk_pct * r_cfg["risk_mult"]) / r_sl if r_sl > 0 else 0

        equity.append(capital)

    df_t = pd.DataFrame(trades)
    eq   = pd.Series(equity[:len(df)], index=df.index[:len(equity)])
    return df_t, eq


def calc_metrics(trades_df, equity, name="", days_period=None):
    """
    Calcula metricas completas con CAGR normalizado por periodo.

    days_period: si None, calcula desde el index del equity.
    """
    empty = {
        "name": name, "trades": 0, "winrate": 0,
        "pnl_pct": -999, "cagr": -999, "trades_month": 0,
        "sharpe": -99, "max_dd": -100,
        "profit_factor": 0, "calmar": 0,
        "avg_win": 0, "avg_loss": 0,
        "expect_usd": 0,
    }

    if trades_df is None or trades_df.empty or len(trades_df) < 3:
        return empty
    if equity is None or equity.empty:
        return empty

    w  = trades_df[trades_df["pnl"] > 0]
    ls = trades_df[trades_df["pnl"] <= 0]
    gp = w["pnl"].sum()
    gl = abs(ls["pnl"].sum())

    # Drawdown
    peak   = equity.cummax()
    dd     = (equity - peak) / peak * 100
    max_dd = dd.min()

    # Sharpe (anualizado por barras reales)
    ret    = equity.pct_change().dropna()
    # Estimamos barras/año segun frecuencia del index
    if len(equity) > 1:
        avg_secs = (equity.index[-1] - equity.index[0]).total_seconds() / len(equity)
        bars_year = 365.25 * 24 * 3600 / avg_secs
    else:
        bars_year = 35040  # default 15m
    sharpe = ret.mean() / ret.std() * np.sqrt(bars_year) if ret.std() > 0 else 0

    # CAGR
    init_cap = equity.iloc[0]
    final_cap= equity.iloc[-1]
    if days_period is None and len(equity) > 1:
        days_period = (equity.index[-1] - equity.index[0]).days
    days_period = max(days_period or 1, 1)
    years  = days_period / 365.25
    pnl    = (final_cap - init_cap) / init_cap * 100
    cagr   = ((final_cap / init_cap) ** (1 / years) - 1) * 100

    # Trades por mes
    t_month = len(trades_df) / max(days_period / 30.44, 0.01)

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0

    # MAE/MFE medios (en ATR) — diagnóstico de calidad de SL/TP
    mae_avg = trades_df["mae_atr"].mean() if "mae_atr" in trades_df.columns else 0
    mfe_avg = trades_df["mfe_atr"].mean() if "mfe_atr" in trades_df.columns else 0

    return {
        "name":           name,
        "trades":         len(trades_df),
        "winrate":        len(w) / len(trades_df) * 100,
        "pnl_pct":        pnl,
        "cagr":           cagr,
        "trades_month":   round(t_month, 1),
        "sharpe":         sharpe,
        "max_dd":         max_dd,
        "profit_factor":  gp / gl if gl > 0 else 999,
        "calmar":         calmar,
        "avg_win":        w["pnl"].mean()  if not w.empty  else 0,
        "avg_loss":       ls["pnl"].mean() if not ls.empty else 0,
        "expect_usd":     trades_df["pnl"].mean(),
        "gross_profit":   gp,
        "gross_loss":     gl,
        "mae_atr_avg":    round(mae_avg, 3),   # cuánto ATR en contra antes de cerrar
        "mfe_atr_avg":    round(mfe_avg, 3),   # cuánto ATR a favor como máximo
    }


def score_config(m, min_trades=30):
    """
    Score normalizado para comparar configs durante la optimizacion.
    Premia: CAGR, Sharpe, PF, WR, frecuencia.
    Penaliza: DD severo, frecuencia muy baja, WR irreal (overfit con pocos trades).
    """
    if m["trades"] < min_trades:
        return -9999 + m["trades"]

    # Penalizar frecuencia muy baja: < 1 trade/mes es estadisticamente irrelevante
    t_month = m.get("trades_month", m["trades"] / 24)
    if t_month < 0.5:
        return -5000 + m["trades"]

    # Penalizar WR demasiado alta con pocos trades (probable overfit)
    wr_high_pen = max(0, m["winrate"] - 80) * 0.04 if m["trades"] < 60 else 0

    trade_bonus = min(m["trades"] / min_trades, 3.0) * 0.10
    freq_bonus  = min(t_month / 3.0, 1.0) * 0.05         # premia frecuencia razonable
    wr_pen      = max(0, 52 - m["winrate"]) * 0.03        # umbral bajado de 55 a 52

    wr   = m["winrate"] / 100
    pf   = min(m["profit_factor"], 20) / 20
    sh   = max(min(m["sharpe"],  8), -8) / 8
    cagr = max(min(m["cagr"], 200), -100) / 200
    # DD mas agresivo: -25% ya es inaceptable para una cuenta pequeña
    dd   = max(min(m["max_dd"],  0), -25) / -25
    cal  = max(min(m["calmar"],  5),  0) / 5

    return (0.25 * cagr + 0.18 * sh  + 0.15 * pf  +
            0.12 * wr   + 0.12 * (1-dd) + 0.08 * cal +
            trade_bonus + freq_bonus - wr_pen - wr_high_pen)


def print_metrics(m, label=""):
    """Print formateado de metricas."""
    pad = f"[{label}] " if label else ""
    print(f"  {pad}Trades: {m['trades']} ({m.get('trades_month',0):.1f}T/mes)")
    print(f"  {pad}WinRate: {m['winrate']:.1f}%")
    print(f"  {pad}CAGR: {m.get('cagr', m['pnl_pct']):+.1f}%/año")
    print(f"  {pad}PnL total: {m['pnl_pct']:+.1f}%")
    print(f"  {pad}Sharpe: {m['sharpe']:.2f}")
    print(f"  {pad}MaxDD: {m['max_dd']:.1f}%")
    print(f"  {pad}PF: {m['profit_factor']:.2f}")
    print(f"  {pad}Calmar: {m.get('calmar',0):.2f}")
    print(f"  {pad}Expectancy: ${m.get('expect_usd',0):.2f}/trade")


def compare_metrics(m_is, m_oos, label_is="IS", label_oos="OOS"):
    """Compara IS vs OOS y da veredicto."""
    print(f"\n  {'Metrica':<20} {label_is:>12} {label_oos:>12} {'Eficiencia':>12}")
    print(f"  {'-'*58}")
    for key, fmt in [("trades","%d"), ("winrate","%.1f%%"), ("cagr","%+.1f%%"),
                     ("profit_factor","%.2f"), ("max_dd","%.1f%%"), ("sharpe","%.2f")]:
        v_is  = m_is.get(key, 0)
        v_oos = m_oos.get(key, 0)
        eff   = v_oos / abs(v_is) if abs(v_is) > 0.01 else 0
        if key == "max_dd": eff = 1.0 - (v_oos / v_is) if v_is != 0 else 0
        print(f"  {key:<20} {v_is:>12{fmt[1:]}} {v_oos:>12{fmt[1:]}} {eff:>12.2f}")

    eff_pnl = m_oos.get("cagr", 0) / max(abs(m_is.get("cagr", 1)), 0.01)
    if eff_pnl >= 0.5:
        print(f"\n  VEREDICTO: EDGE SOLIDO (eficiencia {eff_pnl:.2f}) ✓")
    elif eff_pnl >= 0.25:
        print(f"\n  VEREDICTO: EDGE DEBIL (eficiencia {eff_pnl:.2f}) — paper trading")
    else:
        print(f"\n  VEREDICTO: OVERFIT (eficiencia {eff_pnl:.2f}) — no operar")
    return eff_pnl


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def _get_sl_tp(quality, params):
    """Retorna (sl_mult, tp_mult) segun calidad y params."""
    q_map = CFG["sl_tp_by_quality"]
    if quality in q_map:
        base_sl = q_map[quality]["sl_atr_mult"]
        base_tp = q_map[quality]["tp_atr_mult"]
    else:
        base_sl = q_map["EXECUTE"]["sl_atr_mult"]
        base_tp = q_map["EXECUTE"]["tp_atr_mult"]

    # Override por params si existen
    sl = params.get(f"sl_{quality.lower()}", params.get("sl_default", base_sl))
    tp = params.get(f"tp_{quality.lower()}", params.get("tp_default", base_tp))
    return sl, tp


def _merge_params(params=None):
    """Defaults del backtest desde config."""
    ex = CFG["execution"]
    defaults = {
        "initial_capital":    CFG["capital"]["initial"],
        "risk_pct":           CFG["risk"]["risk_per_trade_pct"],
        "qty_tp1":            ex["qty_tp1_pct"] / 100,
        "qty_tp2":            ex["qty_tp2_pct"] / 100,
        "sl_default":         1.5,
        "tp_default":         2.5,
        "use_trail":          ex.get("use_trail", True),
        "trail_mult":         ex.get("trail_atr_mult", 1.5),
        "max_bars_in_trade":  ex.get("max_bars_in_trade", 0),
    }
    if params:
        defaults.update(params)
    return defaults
