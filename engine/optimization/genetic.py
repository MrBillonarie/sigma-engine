"""
SIGMA ENGINE — Algoritmo Genetico
Evoluciona estrategias combinando las mejores configs entre si.
Logica: survival of the fittest aplicado a parametros de trading.

Ciclo:
  1. Poblacion inicial: top configs del historial (DB) + random
  2. Evaluacion: backtest de cada individuo
  3. Seleccion: top 30% sobreviven (elites)
  4. Crossover: combinar params de 2 padres → hijo
  5. Mutacion: cambiar 1-2 params aleatoriamente
  6. Nueva generacion → repetir

Ventaja vs random search:
  Random: explora al azar
  Bayesian: aprende zonas buenas
  Genetico: COMBINA los mejores → hijos mejores que los padres
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import random
import numpy as np
import copy
import warnings
from pathlib import Path
warnings.filterwarnings("ignore")

random.seed(42); np.random.seed(42)

OUTPUT_DIR = Path(__file__).parent.parent.parent

# ─── PARAMETROS DEL ALGORITMO ────────────────────────────────────────────────
POPULATION_SIZE  = 60    # individuos por generacion
N_GENERATIONS    = 25    # generaciones
ELITE_RATE       = 0.25  # top 25% pasan directo
CROSSOVER_RATE   = 0.65  # 65% de hijos vienen de crossover
MUTATION_RATE    = 0.15  # probabilidad de mutar cada gen
TOURNAMENT_SIZE  = 4     # torneo para seleccion
MIN_TRADES       = 30

# ─── ESPACIO DE GENES ─────────────────────────────────────────────────────────
GENE_SPACE = {
    # Señales (booleanos)
    "use_execute":    [True, False],
    "use_trend":      [True, True, False],
    "use_range":      [True, False],
    "use_watch":      [False, True],
    "use_sess_b":     [True, True, False],
    "use_asia":       [True, False],
    "allow_friday":   [True, False],
    "req_htf2":       [True, True, False],
    "use_be":         [True, True, False],
    # Numericos continuos
    "adx_min":        list(range(12, 32, 2)),
    "hurst_t":        [round(x, 2) for x in np.arange(0.50, 0.63, 0.01)],
    "adx_t":          list(range(18, 34, 2)),
    "hurst_r":        [round(x, 2) for x in np.arange(0.44, 0.53, 0.01)],
    "adx_r":          list(range(14, 26, 2)),
    "temp_min":       list(range(5, 28, 3)),
    "temp_max":       list(range(75, 101, 5)),
    "ofi_threshold":  [round(x, 2) for x in np.arange(0.35, 0.76, 0.05)],
    "elite_sl_mult":  [round(x, 1) for x in np.arange(1.0, 2.6, 0.1)],
    "elite_tp_mult":  [round(x, 2) for x in np.arange(1.5, 5.1, 0.25)],
    "exec_sl_mult":   [round(x, 1) for x in np.arange(1.2, 2.6, 0.1)],
    "exec_tp_mult":   [round(x, 2) for x in np.arange(1.5, 4.6, 0.25)],
    "risk_pct":       [round(x, 1) for x in np.arange(0.3, 1.6, 0.1)],
    "qty_tp1":        [round(x, 2) for x in np.arange(0.35, 0.66, 0.05)],
    "signal_cooldown":list(range(2, 22, 2)),
}


# ─── INDIVIDUO ────────────────────────────────────────────────────────────────
def random_individual():
    return {k: random.choice(v) for k, v in GENE_SPACE.items()}


def crossover(parent1, parent2):
    """Combina genes de dos padres. Crossover de punto uniforme."""
    child = {}
    for key in GENE_SPACE:
        # Cada gen viene del padre 1 o padre 2 con prob 50%
        if random.random() < 0.5:
            child[key] = parent1.get(key, random.choice(GENE_SPACE[key]))
        else:
            child[key] = parent2.get(key, random.choice(GENE_SPACE[key]))
    return child


def mutate(individual, mutation_rate=MUTATION_RATE):
    """Muta aleatoriamente algunos genes."""
    mutant = copy.deepcopy(individual)
    for key, options in GENE_SPACE.items():
        if random.random() < mutation_rate:
            mutant[key] = random.choice(options)
    return mutant


def tournament_select(population_with_scores, k=TOURNAMENT_SIZE):
    """Seleccion por torneo — mejor de k candidatos aleatorios."""
    candidates = random.sample(population_with_scores, min(k, len(population_with_scores)))
    return max(candidates, key=lambda x: x[1])[0]


# ─── FITNESS ──────────────────────────────────────────────────────────────────
def evaluate(individual, df):
    """Calcula el fitness de un individuo (backtest completo)."""
    try:
        sys.path.insert(0, str(OUTPUT_DIR / "engine"))
        from core.signals import get_signals
        from core.backtest import run_backtest, calc_metrics, score_config

        days = (df.index[-1] - df.index[0]).days
        signals, quality = get_signals(df, individual)
        if (signals != 0).sum() < MIN_TRADES // 2:
            return -999, {}

        trades, equity = run_backtest(df, signals, quality, individual)
        m = calc_metrics(trades, equity, days_period=days)
        s = score_config(m, min_trades=MIN_TRADES)
        return s, m
    except Exception:
        return -999, {}


# ─── ALGORITMO GENETICO PRINCIPAL ────────────────────────────────────────────
def run_genetic(tf="15m", df=None, n_gen=N_GENERATIONS, pop_size=POPULATION_SIZE,
                seed_from_db=True):
    """
    Ejecuta el algoritmo genetico.
    Si seed_from_db=True, inicializa con las mejores configs del historial.
    """
    sys.path.insert(0, str(OUTPUT_DIR / "engine"))
    from core.data import fetch_ohlcv
    from core.features import build_features
    from core.database import save_run, get_top_runs, get_best

    TF_MAP = {
        "1m":  ("5m",  "15m", 30),
        "5m":  ("15m", "1h",  90),
        "15m": ("1h",  "4h",  180),
        "1h":  ("4h",  "1d",  365),
        "4h":  ("1d",  "1d",  730),
        "1d":  ("1d",  "1d",  1095),
    }
    htf1, htf2, days_hist = TF_MAP.get(tf, ("1h","4h",180))

    print(f"\n{'='*65}")
    print(f"  SIGMA GENETIC ALGORITHM — {tf.upper()}")
    print(f"  {pop_size} individuos | {n_gen} generaciones")
    print(f"  Crossover: {CROSSOVER_RATE:.0%} | Mutacion: {MUTATION_RATE:.0%} | Elite: {ELITE_RATE:.0%}")
    print(f"{'='*65}")

    # Cargar datos
    if df is None:
        print(f"\n[DATA] Cargando {tf} ({days_hist} dias)...")
        df_base = fetch_ohlcv(tf=tf, days=days_hist)
        df_htf1 = fetch_ohlcv(tf=htf1, days=days_hist*2)
        df_htf2 = fetch_ohlcv(tf=htf2, days=days_hist*3)
        df = build_features(df_base, {htf1: df_htf1, htf2: df_htf2})
        df.dropna(subset=["close","atr","ema50"], inplace=True)
        print(f"  {len(df)} velas listas")

    # Poblacion inicial
    print(f"\n[GEN 0] Inicializando poblacion...")
    population = []

    # Seed desde DB (top configs del historial)
    if seed_from_db:
        top_runs = get_top_runs(tf, n=pop_size//3)
        for run in top_runs:
            params = json.loads(run["params"]) if isinstance(run["params"], str) else run["params"]
            # Filtrar solo los genes validos
            ind = {k: params.get(k, random.choice(GENE_SPACE[k])) for k in GENE_SPACE}
            population.append(ind)
        print(f"  {len(population)} individuos cargados del historial")

    # Seed desde modelo guardado
    best_known = get_best(tf)
    if best_known and best_known.get("params"):
        p = best_known["params"]
        ind = {k: p.get(k, random.choice(GENE_SPACE[k])) for k in GENE_SPACE}
        population.append(ind)
        print(f"  +1 desde mejor modelo conocido (score {best_known['score']:.4f})")

    # Completar con random
    while len(population) < pop_size:
        population.append(random_individual())
    print(f"  Poblacion total: {len(population)} individuos")

    # Historia de evolicion
    best_score_history = []
    best_individual    = None
    best_score_global  = -9999
    best_metrics_global= {}

    # ── EVOLUCIONAR ──────────────────────────────────────────────────────────
    for gen in range(n_gen):
        # Evaluar poblacion
        scored = []
        for i, ind in enumerate(population):
            s, m = evaluate(ind, df)
            scored.append((ind, s, m))
            # Guardar en DB
            if m.get("trades", 0) >= MIN_TRADES // 2:
                save_run(tf, f"genetic_gen{gen}", ind, m, s)

        # Ordenar por fitness
        scored.sort(key=lambda x: x[1], reverse=True)
        best_gen   = scored[0]
        avg_score  = np.mean([x[1] for x in scored if x[1] > -100])

        if best_gen[1] > best_score_global:
            best_score_global   = best_gen[1]
            best_individual     = copy.deepcopy(best_gen[0])
            best_metrics_global = best_gen[2]
            m = best_metrics_global
            print(f"  [Gen {gen+1:2d}] NUEVO MEJOR: "
                  f"{m.get('trades',0)}T | WR {m.get('winrate',0):.1f}% | "
                  f"CAGR {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año | "
                  f"PF {m.get('profit_factor',0):.2f} | DD {m.get('max_dd',0):.1f}% | "
                  f"Score {best_gen[1]:.4f}")
        else:
            print(f"  [Gen {gen+1:2d}] Best: {best_gen[1]:.4f} | Avg: {avg_score:.4f} | "
                  f"Top: {best_gen[2].get('winrate',0):.1f}%WR {best_gen[2].get('cagr',0):+.1f}%CAGR")

        best_score_history.append(best_score_global)

        # Early stopping si no mejora en 5 generaciones
        if len(best_score_history) > 5:
            recent = best_score_history[-5:]
            if max(recent) - min(recent) < 0.001:
                print(f"  Early stopping: sin mejora en 5 generaciones")
                break

        if gen == n_gen - 1:
            break  # Ultima generacion, no crear nueva

        # Seleccion de elites
        n_elite    = max(2, int(pop_size * ELITE_RATE))
        elites     = [x[0] for x in scored[:n_elite]]

        # Nueva generacion
        new_pop = copy.deepcopy(elites)  # Elites pasan directo

        # Crossover + mutacion
        while len(new_pop) < pop_size:
            if random.random() < CROSSOVER_RATE:
                # Crossover
                p1 = tournament_select([(x[0],x[1]) for x in scored])
                p2 = tournament_select([(x[0],x[1]) for x in scored])
                child = crossover(p1, p2)
            else:
                # Solo mutacion de un elite
                child = copy.deepcopy(random.choice(elites))

            child = mutate(child)
            new_pop.append(child)

        population = new_pop

    # ── RESULTADOS FINALES ────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  RESULTADO GENETICO — {tf.upper()}")
    print(f"{'='*65}")
    m = best_metrics_global
    if m:
        print(f"  Trades:  {m.get('trades',0)} ({m.get('trades_month',0):.1f}T/mes)")
        print(f"  WR:      {m.get('winrate',0):.1f}%")
        print(f"  CAGR:    {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año")
        print(f"  Sharpe:  {m.get('sharpe',0):.2f}")
        print(f"  MaxDD:   {m.get('max_dd',0):.1f}%")
        print(f"  PF:      {m.get('profit_factor',0):.2f}")
        print(f"  Calmar:  {m.get('calmar',0):.2f}")
        print(f"  Score:   {best_score_global:.4f}")

    # Guardar
    if best_individual and m:
        model_dir = OUTPUT_DIR / "models" / tf
        model_dir.mkdir(parents=True, exist_ok=True)

        # Comparar con modelo existente
        existing_path = model_dir / "config.json"
        should_save = True
        if existing_path.exists():
            with open(existing_path) as f:
                existing = json.load(f)
            existing_score = existing.get("score", -999)
            if best_score_global > existing_score:
                print(f"\n  MEJORA sobre modelo existente: {existing_score:.4f} → {best_score_global:.4f}")
            else:
                print(f"\n  Sin mejora vs modelo existente ({existing_score:.4f}). Manteniendo anterior.")
                should_save = False

        if should_save:
            with open(model_dir / "config.json", "w") as f:
                json.dump({
                    "tf": tf,
                    "params": best_individual,
                    "metrics": {k: round(v,4) if isinstance(v,float) else v
                                for k,v in m.items()},
                    "score": best_score_global,
                    "source": "genetic",
                    "generations": gen+1,
                }, f, indent=2)
            print(f"  [SAVED] models/{tf}/config.json (genetico)")

    # Grafico de evolucion
    _plot_evolution(best_score_history, tf)

    return best_individual, best_metrics_global, best_score_global


def _plot_evolution(history, tf):
    """Grafico de la curva de evolucion del fitness."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 4))
        fig.patch.set_facecolor("#0f0f23")
        ax.set_facecolor("#1a1a2e")
        ax.plot(history, color="#3498db", lw=2, marker="o", markersize=4)
        ax.set_xlabel("Generacion", color="#aaaaaa")
        ax.set_ylabel("Mejor Score", color="#aaaaaa")
        ax.set_title(f"SIGMA Genetico {tf.upper()} — Evolucion del Fitness",
                     color="white", fontsize=11)
        ax.tick_params(colors="#aaaaaa")
        for sp in ax.spines.values(): sp.set_edgecolor("#333355")
        ax.grid(alpha=0.2)

        path = OUTPUT_DIR / "results" / "charts" / f"genetic_evolution_{tf}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(path, dpi=120, bbox_inches="tight", facecolor="#0f0f23")
        plt.close()
        print(f"  [CHART] {path.name}")
    except Exception:
        pass


def run_genetic_all_tfs(tfs=None):
    """Corre el genetico en todos los TFs secuencialmente."""
    import subprocess, winsound
    tfs = tfs or ["15m", "1h", "5m"]
    results = {}

    for tf in tfs:
        print(f"\n{'#'*65}")
        print(f"# GENETICO — {tf.upper()}")
        print(f"{'#'*65}")
        best_p, best_m, best_s = run_genetic(tf)
        results[tf] = {"params": best_p, "metrics": best_m, "score": best_s}

    # Resumen
    print(f"\n{'='*70}")
    print(f"{'RESUMEN GENETICO CROSS-TF':^70}")
    print(f"{'='*70}")
    print(f"{'TF':<6} {'T/mes':>6} {'WR%':>7} {'CAGR%':>8} {'PF':>6} {'DD%':>7} {'Score':>8}")
    print("-"*55)
    for tf, r in results.items():
        m = r["metrics"]
        if m:
            print(f"{tf:<6} {m.get('trades_month',0):>5.1f} "
                  f"{m.get('winrate',0):>6.1f}% "
                  f"{m.get('cagr', m.get('pnl_pct',0)):>7.1f}% "
                  f"{m.get('profit_factor',0):>6.2f} "
                  f"{m.get('max_dd',0):>6.1f}% "
                  f"{r['score']:>8.4f}")

    # Generar Pine Scripts del ganador
    try:
        sys.path.insert(0, str(OUTPUT_DIR / "engine"))
        from live.pine_generator import generate_all_tfs
        generate_all_tfs()
    except Exception as e:
        print(f"  [WARN] pine_generator error: {e}")

    # Notificar
    try:
        for _ in range(5): winsound.Beep(1200, 300)
        msg_lines = ["GENETICO COMPLETADO!\n"]
        for tf, r in results.items():
            m = r["metrics"]
            if m:
                msg_lines.append(f"{tf.upper()}: WR {m.get('winrate',0):.1f}% | "
                                 f"CAGR {m.get('cagr',0):+.1f}%/año")
        msg = "\\n".join(msg_lines)
        subprocess.Popen([
            "powershell", "-WindowStyle", "Hidden", "-Command",
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg}", "SIGMA Genetico", "OK", "Information")'
        ])
    except Exception:
        pass

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf",   default="15m")
    parser.add_argument("--gens", type=int, default=N_GENERATIONS)
    parser.add_argument("--pop",  type=int, default=POPULATION_SIZE)
    parser.add_argument("--all",  action="store_true")
    args = parser.parse_args()

    if args.all:
        run_genetic_all_tfs()
    else:
        run_genetic(args.tf, n_gen=args.gens, pop_size=args.pop)
