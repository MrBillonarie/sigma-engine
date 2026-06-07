"""
SIGMA ENGINE — Walk-Forward Validator v2
=========================================
Reescrito sobre el engine modular (core.*).
Ya no depende del código del archive.

Pipeline completo antes de arriesgar capital:
  1. Walk-Forward Rolling    — IS/OOS con embargo entre ventanas
  2. IS/OOS Split            — top N configs del search en held-out 20%
  3. False Discovery Rate    — cuantos ganadores son ruido estadístico
  4. Monte Carlo             — distribución de DD y probabilidad de ruina

Uso:
  python engine/optimization/walk_forward.py --tf 1h
  python engine/optimization/walk_forward.py --tf 15m --samples 300
"""

import sys, os, random, argparse, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
random.seed(42)

import pandas as pd
import numpy  as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path

from core.data     import fetch_ohlcv
from core.features import build_features
from core.signals  import get_signals
from core.backtest import run_backtest, calc_metrics, score_config

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    _CFG = json.load(f)

CAPITAL    = _CFG["capital"]["initial"]
RESULTS    = Path(__file__).parent.parent / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

# ─── ESPACIO DE BÚSQUEDA ──────────────────────────────────────────────────────
# Parámetros optimizables del engine actual.
SEARCH_SPACE = {
    # Tipos de señal activos
    "use_elite_ict":  [True, False],
    "use_elite":      [True, False],
    "use_execute":    [True, False],
    "use_trend":      [True, False],
    "use_range":      [True, False],
    # Sesiones
    "use_sess_b":     [True, False],
    "use_asia":       [True, False],
    "allow_friday":   [True, False],
    "allow_monday":   [True, False],
    # HTF
    "req_htf2":       [True, False],
    # Filtros cuantitativos
    "adx_min":        list(range(10, 32, 2)),
    "hurst_t":        [0.50, 0.52, 0.54, 0.55, 0.57, 0.60, 0.62, 0.65],
    "adx_t":          list(range(18, 42, 3)),
    "hurst_r":        [0.40, 0.43, 0.46, 0.48, 0.50, 0.52],
    "adx_r":          list(range(12, 27, 2)),
    "temp_min":       [5, 8, 10, 12, 15, 18, 20],
    "temp_max":       [70, 75, 80, 85, 90, 95],
    "signal_cooldown":[2, 4, 6, 8, 10, 12, 16, 20],
    # Risk / sizing
    "risk_pct":       [0.3, 0.4, 0.5, 0.6, 0.75, 1.0],
    # Gestión de posición
    "use_trail":      [True, False],
    "trail_mult":     [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5],
    "max_bars_in_trade": [0, 10, 15, 20, 30, 40, 50],
    # Filtros de futuros (si hay datos)
    "use_funding_filter": [True, False],
    "use_oi_filter":      [True, False],
}

TF_CFG = {
    "1m":  {"days": 30,   "min_trades": 120, "min_trades_month": 5.0},
    "5m":  {"days": 180,  "min_trades": 50,  "min_trades_month": 3.0},
    "15m": {"days": 730,  "min_trades": 35,  "min_trades_month": 2.0},
    "1h":  {"days": 1095, "min_trades": 18,  "min_trades_month": 1.0},
    "4h":  {"days": 1500, "min_trades": 12,  "min_trades_month": 0.5},
    "1d":  {"days": 1500, "min_trades": 8,   "min_trades_month": 0.3},
}

HTF_MAP = {
    "1m":  ("5m",  "15m"),
    "5m":  ("15m", "1h"),
    "15m": ("1h",  "4h"),
    "1h":  ("4h",  "1d"),
    "4h":  ("1d",  "1d"),
    "1d":  ("1d",  "1d"),
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def sample_config():
    """Muestrea una configuración aleatoria del espacio de búsqueda."""
    return {k: random.choice(v) for k, v in SEARCH_SPACE.items()}


def bars_in_months(df, months):
    days = (df.index[-1] - df.index[0]).days
    bpd  = len(df) / max(days, 1)
    return int(bpd * 30.44 * months)


def split_df(df, is_bars, oos_bars, offset=0, embargo_bars=0):
    """
    Divide df en IS y OOS con buffer de embargo entre ellos.
    Embargo evita contaminación de look-ahead en el borde IS/OOS.
    """
    end_is  = offset + is_bars
    end_oos = end_is + embargo_bars + oos_bars
    if end_oos > len(df):
        return None, None
    return df.iloc[offset:end_is], df.iloc[end_is + embargo_bars:end_oos]


def test_config(df, cfg):
    """Ejecuta un backtest con el engine modular. Retorna métricas o None."""
    try:
        sig, qual = get_signals(df, cfg)
        if (sig != 0).sum() < 2:
            return None
        trades, equity = run_backtest(df, sig, qual, cfg)
        days = (df.index[-1] - df.index[0]).days
        return calc_metrics(trades, equity, days_period=days)
    except Exception:
        return None


def quick_optimize(df, n_samples=300, min_trades=15, min_trades_month=1.0):
    """Random search rápido en el engine actual. Retorna lista ordenada (m, cfg, score)."""
    results = []
    for _ in range(n_samples):
        cfg = sample_config()
        m   = test_config(df, cfg)
        if m is None or m["trades"] < min_trades:
            continue
        if m.get("trades_month", 0) < min_trades_month:
            continue
        s = score_config(m, min_trades=min_trades)
        if s > -9000:
            results.append((m, cfg, s))
    results.sort(key=lambda x: x[2], reverse=True)
    return results


# ─── 1. WALK-FORWARD ROLLING ──────────────────────────────────────────────────
def run_walk_forward(df, tf="15m", is_months=3, oos_months=1,
                     n_samples=300, embargo_months=0.25):
    tf_c      = TF_CFG.get(tf, TF_CFG["15m"])
    min_tr    = tf_c["min_trades"]
    min_tm    = tf_c["min_trades_month"]
    is_bars   = bars_in_months(df, is_months)
    oos_bars  = bars_in_months(df, oos_months)
    emb_bars  = bars_in_months(df, embargo_months)
    step      = oos_bars

    print(f"\n{'='*65}")
    print(f"  WALK-FORWARD ROLLING [{tf.upper()}]")
    print(f"  IS: {is_months}m | OOS: {oos_months}m | Embargo: {emb_bars} barras | RS: {n_samples}")
    print(f"{'='*65}")

    windows, offset = [], 0
    while True:
        df_is, df_oos = split_df(df, is_bars, oos_bars, offset, emb_bars)
        if df_is is None:
            break
        windows.append((offset, df_is, df_oos))
        offset += step

    print(f"  {len(windows)} ventanas IS/OOS\n")
    if not windows:
        print("  No hay suficientes datos. Necesitas más historia.")
        return []

    summary = []
    for i, (_, df_is, df_oos) in enumerate(windows):
        d_is  = f"{df_is.index[0].date()} → {df_is.index[-1].date()}"
        d_oos = f"{df_oos.index[0].date()} → {df_oos.index[-1].date()}"
        print(f"  [Ventana {i+1}/{len(windows)}]")
        print(f"    IS:  {d_is} ({len(df_is):,} barras)")
        print(f"    OOS: {d_oos} ({len(df_oos):,} barras)")

        top = quick_optimize(df_is, n_samples, min_tr, min_tm)
        if not top:
            print("    IS: sin configs válidas\n")
            summary.append({"ventana": i+1, "is_start": str(df_is.index[0].date()),
                             "oos_start": str(df_oos.index[0].date()),
                             "oos_pnl": 0, "oos_wr": 0, "efficiency": 0,
                             "oos_trades": 0, "is_pnl": 0, "is_trades": 0})
            continue

        best_m, best_cfg, _ = top[0]
        print(f"    IS mejor: {best_m['trades']}T | WR {best_m['winrate']:.1f}% | "
              f"CAGR {best_m.get('cagr', 0):+.1f}%")

        m_oos = test_config(df_oos, best_cfg)
        eff   = 0.0
        if m_oos and m_oos["trades"] >= 2:
            eff = m_oos.get("cagr", 0) / max(abs(best_m.get("cagr", 0.01)), 0.01)
            print(f"    OOS:     {m_oos['trades']}T | WR {m_oos['winrate']:.1f}% | "
                  f"CAGR {m_oos.get('cagr', 0):+.1f}% | Efic. {eff:.2f}")
        else:
            print("    OOS: sin trades suficientes")

        summary.append({
            "ventana":   i + 1,
            "is_start":  str(df_is.index[0].date()),
            "is_end":    str(df_is.index[-1].date()),
            "oos_start": str(df_oos.index[0].date()),
            "oos_end":   str(df_oos.index[-1].date()),
            "is_trades": best_m["trades"],
            "is_wr":     best_m["winrate"],
            "is_pnl":    best_m.get("cagr", 0),
            "oos_trades":m_oos["trades"] if m_oos else 0,
            "oos_wr":    m_oos["winrate"] if m_oos else 0,
            "oos_pnl":   m_oos.get("cagr", 0) if m_oos else 0,
            "efficiency":eff,
        })
        print()

    if summary:
        df_s = pd.DataFrame(summary)
        pos  = (df_s["oos_pnl"] > 0).sum()
        eff_avg = df_s["efficiency"].mean()
        print(f"  RESUMEN WALK-FORWARD:")
        print(f"  OOS positivos:          {pos}/{len(summary)} ({pos/len(summary)*100:.0f}%)")
        print(f"  Eficiencia IS→OOS avg:  {eff_avg:.2f}")
        if eff_avg >= 0.5 and pos / len(summary) >= 0.6:
            print("  VEREDICTO: EDGE VALIDADO ✓")
        elif eff_avg >= 0.3:
            print("  VEREDICTO: EDGE DÉBIL — paper trading primero")
        else:
            print("  VEREDICTO: OVERFIT — no operar")
        df_s.to_csv(RESULTS / "reports" / "wf_results.csv", index=False)

    return summary


# ─── 2. IS/OOS SPLIT SOBRE TOP CONFIGS ────────────────────────────────────────
def run_is_oos_split(df, tf="15m", n_samples=500, top_n=5):
    tf_c   = TF_CFG.get(tf, TF_CFG["15m"])
    min_tr = tf_c["min_trades"]
    min_tm = tf_c["min_trades_month"]

    print(f"\n{'='*65}")
    print(f"  IS/OOS SPLIT — 80% IS / 20% OOS [{tf.upper()}]")
    print(f"{'='*65}")

    # Embargo de max_bars_in_trade entre IS y OOS
    emb       = max(SEARCH_SPACE["max_bars_in_trade"]) if SEARCH_SPACE.get("max_bars_in_trade") else 20
    split_idx = int(len(df) * 0.80)
    df_is     = df.iloc[:split_idx - emb]
    df_oos    = df.iloc[split_idx:]

    print(f"  IS:  {df_is.index[0].date()} → {df_is.index[-1].date()} ({len(df_is):,} barras)")
    print(f"  OOS: {df_oos.index[0].date()} → {df_oos.index[-1].date()} ({len(df_oos):,} barras)\n")

    print(f"  Optimizando {n_samples} configs en IS...")
    top_configs = quick_optimize(df_is, n_samples, min_tr, min_tm)

    if not top_configs:
        print("  Sin configs válidas en IS.")
        return [], None

    results, best_idx = [], None
    for i, (m_is, cfg, _) in enumerate(top_configs[:top_n]):
        m_oos = test_config(df_oos, cfg)
        if m_oos is None:
            print(f"  Config #{i+1}: sin trades en OOS")
            continue
        eff      = m_oos.get("cagr", 0) / max(abs(m_is.get("cagr", 0.01)), 0.01)
        survived = m_oos.get("cagr", -999) > 0 and m_oos.get("profit_factor", 0) > 1.0
        tag      = "SOBREVIVE ✓" if survived else "FALLA ✗"
        print(f"  Config #{i+1}: {tag}")
        print(f"    IS:  {m_is['trades']}T | WR {m_is['winrate']:.1f}% | "
              f"CAGR {m_is.get('cagr',0):+.1f}% | PF {m_is['profit_factor']:.2f}")
        print(f"    OOS: {m_oos['trades']}T | WR {m_oos['winrate']:.1f}% | "
              f"CAGR {m_oos.get('cagr',0):+.1f}% | PF {m_oos['profit_factor']:.2f}")
        print(f"    Eficiencia: {eff:.2f} | MAE_ATR: {m_oos.get('mae_atr_avg',0):.2f} | "
              f"MFE_ATR: {m_oos.get('mfe_atr_avg',0):.2f}\n")
        results.append({"rank": i+1, "is_cagr": m_is.get("cagr",0),
                         "oos_cagr": m_oos.get("cagr",0),
                         "oos_wr": m_oos["winrate"], "oos_pf": m_oos["profit_factor"],
                         "efficiency": eff, "survived": survived, "cfg": cfg})
        if survived and best_idx is None:
            best_idx = i

    n_surv = sum(1 for r in results if r["survived"])
    print(f"  Configs que sobreviven OOS: {n_surv}/{len(results)}")
    return results, best_idx, top_configs


# ─── 3. FALSE DISCOVERY RATE ──────────────────────────────────────────────────
def run_fdr(n_tests, n_positives, alpha=0.05):
    print(f"\n{'='*65}")
    print("  FALSE DISCOVERY RATE")
    print(f"{'='*65}")
    expected_false = n_tests * alpha
    fdr = expected_false / max(n_positives, 1) * 100
    print(f"  Tests: {n_tests:,} | Positivos: {n_positives:,}")
    print(f"  Falsos esperados por azar: {expected_false:.0f}")
    print(f"  FDR: {fdr:.1f}%  {'← OK' if fdr < 30 else '← ALTO'}")
    return fdr


# ─── 4. MONTE CARLO ───────────────────────────────────────────────────────────
def run_monte_carlo(trades_df, n_sim=10000):
    print(f"\n{'='*65}")
    print(f"  MONTE CARLO — {n_sim:,} simulaciones")
    print(f"{'='*65}")

    if trades_df is None or len(trades_df) < 5:
        print("  Sin suficientes trades.")
        return {}

    pnl = trades_df["pnl"].values
    print(f"  Trades reales: {len(pnl)} | PnL total: ${pnl.sum():.2f}")

    max_dds, finals, max_cl_arr = [], [], []
    for _ in range(n_sim):
        perm = np.random.permutation(pnl)
        eq   = CAPITAL + np.cumsum(perm)
        eq   = np.insert(eq, 0, CAPITAL)
        peak = np.maximum.accumulate(eq)
        max_dds.append(((eq - peak) / peak * 100).min())
        finals.append(eq[-1])
        cl = mcl = 0
        for p in perm:
            cl = cl + 1 if p < 0 else 0
            mcl = max(mcl, cl)
        max_cl_arr.append(mcl)

    dds = np.array(max_dds); fins = np.array(finals); cls = np.array(max_cl_arr)
    dd50, dd95, dd99 = np.percentile(dds, 50), np.percentile(dds, 5), np.percentile(dds, 1)
    f50, f10 = np.percentile(fins, 50), np.percentile(fins, 10)
    ruin  = (fins < CAPITAL * 0.50).mean() * 100
    gains = (fins > CAPITAL).mean() * 100
    cl95  = np.percentile(cls, 95)

    print(f"  DD máximo mediano:        {dd50:.1f}%")
    print(f"  DD máximo peor 5%:        {dd95:.1f}%")
    print(f"  DD máximo peor 1%:        {dd99:.1f}%")
    print(f"  Equity final mediana:     ${f50:.0f}")
    print(f"  Prob. ganancia:           {gains:.1f}%")
    print(f"  Prob. ruina (perder 50%): {ruin:.1f}%")
    print(f"  Max losses consecutivos p95: {cl95:.0f}")
    if ruin < 1:
        print("  SIZING: OK — prob. ruina < 1%")
    else:
        print(f"  SIZING: Reducir riesgo — prob. ruina {ruin:.1f}%")

    return {"dd_p50": dd50, "dd_p95": dd95, "dd_p99": dd99,
            "profit_pct": gains, "ruin_pct": ruin,
            "eq_p50": f50, "eq_p10": f10, "max_consec_p95": cl95,
            "max_dds": dds, "finals": fins}


# ─── VISUALIZACIÓN ────────────────────────────────────────────────────────────
def plot_report(df_price, wf_summary, mc_results, best_trades, tf):
    fig = plt.figure(figsize=(18, 12))
    fig.patch.set_facecolor("#0f0f23")
    gs  = gridspec.GridSpec(3, 3, figure=fig, hspace=0.42, wspace=0.35)

    def ax_style(ax, title):
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor("#333355")
        ax.set_title(title, color="white", fontsize=9, pad=4)

    # Precio + ventanas OOS
    ax1 = fig.add_subplot(gs[0, :2])
    ax1.plot(df_price.index, df_price["close"], color="#cccccc", lw=0.5)
    if "ema50" in df_price.columns:
        ax1.plot(df_price.index, df_price["ema50"],  color="#f39c12", lw=1, label="EMA50")
        ax1.plot(df_price.index, df_price["ema200"], color="#e74c3c", lw=1, label="EMA200")
        ax1.legend(fontsize=7, loc="upper left")
    for r in (wf_summary or []):
        try:
            col = "#2ecc71" if r.get("oos_pnl", 0) > 0 else "#e74c3c"
            ax1.axvspan(pd.Timestamp(r["oos_start"]), pd.Timestamp(r["oos_end"]),
                        alpha=0.15, color=col)
        except Exception:
            pass
    ax_style(ax1, f"BTC/USDT {tf.upper()} — Verde=OOS+ | Rojo=OOS-")

    # OOS PnL por ventana
    ax2 = fig.add_subplot(gs[0, 2])
    if wf_summary:
        v  = [r["ventana"] for r in wf_summary]
        pn = [r.get("oos_pnl", 0) for r in wf_summary]
        ax2.bar(v, pn, color=["#2ecc71" if p > 0 else "#e74c3c" for p in pn], alpha=0.8)
        ax2.axhline(0, color="white", lw=0.8, ls="--")
        ax2.set_xlabel("Ventana"); ax2.set_ylabel("OOS CAGR %")
    ax_style(ax2, "Walk-Forward OOS por ventana")

    # IS vs OOS scatter
    ax3 = fig.add_subplot(gs[1, 0])
    if wf_summary:
        ix = [r.get("is_pnl",  0) for r in wf_summary]
        ox = [r.get("oos_pnl", 0) for r in wf_summary]
        ax3.scatter(ix, ox, color="#3498db", s=60, zorder=5)
        lim = max(abs(max(ix + ox, default=1)), 1)
        ax3.plot([-lim, lim], [-lim, lim], "w--", lw=0.8, alpha=0.5)
        ax3.axhline(0, color="#e74c3c", lw=0.5)
        ax3.axvline(0, color="#e74c3c", lw=0.5)
        ax3.set_xlabel("IS CAGR %"); ax3.set_ylabel("OOS CAGR %")
    ax_style(ax3, "IS vs OOS — ideal: sobre la diagonal")

    # Distribución DD Monte Carlo
    ax4 = fig.add_subplot(gs[1, 1])
    if mc_results and "max_dds" in mc_results:
        ax4.hist(mc_results["max_dds"], bins=50, color="#e74c3c", alpha=0.7, edgecolor="none")
        ax4.axvline(mc_results["dd_p95"], color="yellow", lw=1.5, ls="--",
                    label=f"p5: {mc_results['dd_p95']:.1f}%")
        ax4.axvline(mc_results["dd_p99"], color="orange", lw=1.5, ls="--",
                    label=f"p1: {mc_results['dd_p99']:.1f}%")
        ax4.legend(fontsize=7)
        ax4.set_xlabel("Max Drawdown %")
    ax_style(ax4, "Monte Carlo — Distribución DD")

    # Distribución equity final
    ax5 = fig.add_subplot(gs[1, 2])
    if mc_results and "finals" in mc_results:
        ax5.hist(mc_results["finals"], bins=50, color="#2ecc71", alpha=0.7, edgecolor="none")
        ax5.axvline(CAPITAL,                color="white",  lw=1.5, ls="--", label="Capital")
        ax5.axvline(mc_results["eq_p50"],   color="yellow", lw=1.5, ls="--",
                    label=f"Med: ${mc_results['eq_p50']:.0f}")
        ax5.axvline(mc_results["eq_p10"],   color="orange", lw=1.5, ls="--",
                    label=f"p10: ${mc_results['eq_p10']:.0f}")
        ax5.legend(fontsize=7); ax5.set_xlabel("Equity final ($)")
    ax_style(ax5, "Monte Carlo — Equity final")

    # Equity curve del mejor
    ax6 = fig.add_subplot(gs[2, :2])
    if best_trades is not None and not best_trades.empty and "capital" in best_trades.columns:
        eq_v = [CAPITAL] + list(best_trades["capital"].values)
        ax6.plot(range(len(eq_v)), eq_v, color="#3498db", lw=1.5)
        ax6.fill_between(range(len(eq_v)), CAPITAL, eq_v,
                         where=[e > CAPITAL for e in eq_v], alpha=0.2, color="#2ecc71")
        ax6.fill_between(range(len(eq_v)), CAPITAL, eq_v,
                         where=[e <= CAPITAL for e in eq_v], alpha=0.2, color="#e74c3c")
        ax6.axhline(CAPITAL, color="white", lw=0.8, ls="--")
        ax6.set_xlabel("# Trade"); ax6.set_ylabel("Capital ($)")
    ax_style(ax6, "Curva de equity — Mejor config OOS")

    # Texto resumen
    ax7 = fig.add_subplot(gs[2, 2])
    ax7.axis("off")
    txt = "RESUMEN QUANT\n\n"
    if wf_summary:
        pos = sum(1 for r in wf_summary if r.get("oos_pnl", 0) > 0)
        ae  = np.mean([r.get("efficiency", 0) for r in wf_summary])
        txt += f"Walk-Forward:\n  OOS+: {pos}/{len(wf_summary)}\n  Efic: {ae:.2f}\n\n"
    if mc_results:
        txt += (f"Monte Carlo:\n  Prob. ganancia: {mc_results.get('profit_pct',0):.1f}%\n"
                f"  Prob. ruina 50%: {mc_results.get('ruin_pct',0):.1f}%\n"
                f"  DD p95: {mc_results.get('dd_p95',0):.1f}%\n"
                f"  Max consec p95: {mc_results.get('max_consec_p95',0):.0f}\n")
    ax7.text(0.05, 0.95, txt, transform=ax7.transAxes,
             color="white", fontsize=8, va="top", fontfamily="monospace")
    ax_style(ax7, "Summary")

    fig.suptitle(f"SIGMA — Walk-Forward + Monte Carlo [{tf.upper()}]",
                 color="white", fontsize=13)
    out = RESULTS / "charts" / f"wf_montecarlo_{tf}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="#0f0f23")
    plt.close()
    print(f"\n  [CHART] {out}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="SIGMA Walk-Forward Validator v2")
    parser.add_argument("--tf",         default="15m", choices=list(TF_CFG))
    parser.add_argument("--is-months",  type=float, default=3)
    parser.add_argument("--oos-months", type=float, default=1)
    parser.add_argument("--samples",    type=int,   default=300)
    parser.add_argument("--top-n",      type=int,   default=5)
    args = parser.parse_args()

    tf      = args.tf
    tf_c    = TF_CFG.get(tf, TF_CFG["15m"])
    h1, h2  = HTF_MAP.get(tf, ("1h", "4h"))

    print(f"\n{'='*65}")
    print(f"  SIGMA — Walk-Forward + Monte Carlo v2 [{tf.upper()}]")
    print(f"  Engine: modular | Embargo: activo | Muestras: {args.samples}")
    print(f"{'='*65}")

    # ── Datos ─────────────────────────────────────────────────────────────────
    print("\n[DATA] Descargando datos...")
    df_base = fetch_ohlcv(tf=tf, days=tf_c["days"])
    df_htf1 = fetch_ohlcv(tf=h1, days=tf_c["days"] * 2)
    df_htf2 = fetch_ohlcv(tf=h2, days=tf_c["days"] * 3)

    # Intentar cargar datos de futuros (graceful degradation si no hay conexión)
    futures_dict = None
    try:
        from core.data_futures import fetch_all_futures_data
        futures_dict = fetch_all_futures_data(period="1h", days=min(tf_c["days"], 180))
        all_none = all(v is None for v in futures_dict.values())
        if all_none:
            futures_dict = None
    except Exception:
        pass

    print("[FEATURES] Calculando...")
    df = build_features(df_base, htf_dict={h1: df_htf1, h2: df_htf2},
                        futures_dict=futures_dict)
    df.dropna(subset=["close", "atr", "ema50"], inplace=True)
    print(f"  {len(df)} velas listas ({df.index[0].date()} → {df.index[-1].date()})\n")

    # ── 1. Walk-Forward Rolling ───────────────────────────────────────────────
    wf_summary = run_walk_forward(
        df, tf=tf,
        is_months=args.is_months, oos_months=args.oos_months,
        n_samples=args.samples
    )

    # ── 2. IS/OOS Split ───────────────────────────────────────────────────────
    split_out = run_is_oos_split(df, tf=tf, n_samples=args.samples, top_n=args.top_n)
    split_results, best_idx, top_configs = split_out if len(split_out) == 3 else ([], None, [])

    # ── 3. FDR ────────────────────────────────────────────────────────────────
    n_pos = len([r for r in split_results if r.get("oos_cagr", 0) > 0]) if split_results else 1
    run_fdr(n_tests=args.samples, n_positives=max(n_pos, 1))

    # ── 4. Monte Carlo sobre el mejor OOS ────────────────────────────────────
    best_trades = None
    mc_results  = {}

    if best_idx is not None and split_results and best_idx < len(split_results):
        best_cfg = split_results[best_idx]["cfg"]
        split_i  = int(len(df) * 0.80)
        df_oos   = df.iloc[split_i:]
        print(f"\n[MC] Corriendo Monte Carlo sobre mejor config OOS...")
        try:
            sig, qual = get_signals(df_oos, best_cfg)
            if (sig != 0).sum() >= 3:
                trades_df, equity = run_backtest(df_oos, sig, qual, best_cfg)
                if not trades_df.empty:
                    best_trades = trades_df
                    # Añadir columna capital acumulada para gráfico
                    if "capital" not in best_trades.columns:
                        caps = [CAPITAL]
                        for pnl in best_trades["pnl"].values:
                            caps.append(caps[-1] + pnl)
                        best_trades = best_trades.copy()
                        best_trades["capital"] = caps[1:]
                    mc_results = run_monte_carlo(best_trades)
        except Exception as e:
            print(f"  [WARN] Monte Carlo falló: {e}")

    # ── Veredicto final ───────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  VEREDICTO FINAL")
    print(f"{'='*65}")

    edge = 0
    if wf_summary:
        pos_r = sum(1 for r in wf_summary if r.get("oos_pnl", 0) > 0) / len(wf_summary)
        ae    = np.mean([r.get("efficiency", 0) for r in wf_summary])
        edge += (2 if pos_r >= 0.6 else 1 if pos_r >= 0.4 else 0)
        edge += (2 if ae >= 0.5 else 1 if ae >= 0.3 else 0)
    if split_results:
        n_surv = sum(1 for r in split_results if r.get("survived"))
        edge += (2 if n_surv > 0 else 0)
    if mc_results:
        edge += (2 if mc_results.get("ruin_pct", 100) < 1
                 else 1 if mc_results.get("ruin_pct", 100) < 5 else 0)

    print(f"\n  Score: {edge}/8")
    if edge >= 6:
        print("  ACCIÓN: EDGE SÓLIDO → Paper trading 30d, luego capital mínimo")
    elif edge >= 4:
        print("  ACCIÓN: EDGE PROBABLE → Paper trading 60d, no capital real aún")
    elif edge >= 2:
        print("  ACCIÓN: EDGE DÉBIL → Seguir buscando, ajustar filtros")
    else:
        print("  ACCIÓN: SIN EDGE → Volver a optimizar")

    # ── Gráfico ───────────────────────────────────────────────────────────────
    print("\n[CHART] Generando reporte...")
    plot_report(df, wf_summary, mc_results, best_trades, tf)
    print("[DONE] Walk-Forward + Monte Carlo completado.")


if __name__ == "__main__":
    main()
