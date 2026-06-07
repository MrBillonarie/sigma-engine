"""
SIGMA ENGINE — Sistema Autonomo Permanente
Corre indefinidamente, aprende y mejora solo.

Pipeline diario (06:00 UTC):
  → Descarga datos frescos de todos los TFs
  → Verifica si los modelos necesitan re-optimizacion
  → Corre Bayesian search (aprende del historial en sigma.db)
  → Walk-Forward validation del ganador
  → Genera Pine Scripts actualizados
  → Notifica con beeps + popup

Monitor cada 30 minutos:
  → Lee Excel SIGMA K1 (trades reales)
  → Verifica WR rolling
  → Si detecta degradacion → re-optimiza automaticamente
  → Alerta inmediata si hay problema critico

Memoria persistente:
  → sigma.db guarda TODOS los backtests
  → Cada re-optimizacion empieza desde el mejor resultado anterior
  → Nunca empieza desde cero
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import schedule
import subprocess
import winsound
import traceback
from pathlib import Path
from datetime import datetime

OUTPUT_DIR  = Path(__file__).parent.parent.parent
CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

LOG_PATH = OUTPUT_DIR / "results" / "reports" / "autonomous_log.txt"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def log(msg, level="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(title, msg, critical=False):
    """Notificacion Windows + beep."""
    try:
        freq = 1500 if critical else 1000
        n    = 5    if critical else 3
        for _ in range(n):
            winsound.Beep(freq, 300)
        msg_safe = msg.replace('"', "'").replace("\n", "\\n")
        subprocess.Popen([
            "powershell", "-WindowStyle", "Hidden", "-Command",
            f'Add-Type -AssemblyName PresentationFramework;'
            f'[System.Windows.MessageBox]::Show("{msg_safe}", "{title}", "OK", '
            f'"{"Error" if critical else "Information"}")'
        ])
    except Exception:
        pass


def needs_reoptimization(tf, max_days=7):
    """Verifica si el modelo es viejo o no existe."""
    model = OUTPUT_DIR / "models" / tf / "best_validated.json"
    if not model.exists():
        log(f"  {tf}: sin modelo → re-optimizar")
        return True
    mtime = datetime.fromtimestamp(model.stat().st_mtime)
    days  = (datetime.now() - mtime).days
    if days >= max_days:
        log(f"  {tf}: modelo de {days} dias → re-optimizar")
        return True
    return False


def run_bayesian(tf, trials=200):
    """Corre Bayesian search en background."""
    script = OUTPUT_DIR / "engine" / "optimization" / "bayesian_search.py"
    if not script.exists():
        return False
    log(f"  Bayesian {tf}: {trials} trials...")
    try:
        result = subprocess.run(
            ["python", "-u", "-X", "utf8", str(script), "--tf", tf, "--trials", str(trials)],
            cwd=str(OUTPUT_DIR), capture_output=True, text=True, timeout=3600
        )
        if result.returncode == 0:
            log(f"  Bayesian {tf}: OK")
            return True
        else:
            log(f"  Bayesian {tf}: error - {result.stderr[-100:]}", "ERROR")
            return False
    except Exception as e:
        log(f"  Bayesian {tf}: timeout/error - {e}", "ERROR")
        return False


def run_lower_tf_search(tf, samples=500):
    """Corre improve_lower_tf.py para 1m/5m/15m — estrategias simples."""
    script = OUTPUT_DIR / "engine" / "optimization" / "improve_lower_tf.py"
    if not script.exists():
        return
    log(f"  Lower-TF search {tf}: {samples} muestras...")
    try:
        subprocess.run(
            ["python", "-u", "-X", "utf8", str(script),
             "--tf", tf, "--samples", str(samples)],
            cwd=str(OUTPUT_DIR), capture_output=True, text=True, timeout=7200
        )
        log(f"  Lower-TF {tf}: OK")
    except Exception as e:
        log(f"  Lower-TF {tf}: error - {e}", "ERROR")


def run_crypto_search(tf, samples=300):
    """Corre crypto strategies search."""
    script = OUTPUT_DIR / "engine" / "optimization" / "crypto_strategies.py"
    if not script.exists():
        return
    log(f"  Crypto search {tf}: {samples} muestras...")
    try:
        subprocess.run(
            ["python", "-u", "-X", "utf8", str(script), "--tf", tf, "--samples", str(samples)],
            cwd=str(OUTPUT_DIR), capture_output=True, text=True, timeout=3600
        )
    except Exception as e:
        log(f"  Crypto search {tf}: error - {e}", "ERROR")


def generate_pine_scripts(tfs):
    """Genera Pine Scripts para todos los TFs con modelos."""
    script = OUTPUT_DIR / "engine" / "live" / "pine_generator.py"
    if not script.exists():
        return
    log("  Generando Pine Scripts...")
    try:
        subprocess.run(
            ["python", "-X", "utf8", str(script)],
            cwd=str(OUTPUT_DIR), capture_output=True, text=True, timeout=60
        )
        log("  Pine Scripts actualizados")
    except Exception as e:
        log(f"  Pine generator error: {e}", "ERROR")


def update_dashboard():
    """Actualiza el dashboard HTML."""
    script = OUTPUT_DIR / "engine" / "live" / "dashboard.py"
    if not script.exists():
        return
    try:
        subprocess.run(
            ["python", "-X", "utf8", str(script)],
            cwd=str(OUTPUT_DIR), capture_output=True, text=True, timeout=30
        )
    except Exception:
        pass


def check_live_performance(tfs):
    """Verifica performance de trades reales en Excel."""
    try:
        from live.monitor import PerformanceMonitor
        monitor = PerformanceMonitor()
        trades  = monitor.load_trades_from_excel()

        if len(trades) < 5:
            return

        for tf in tfs:
            m10 = monitor.calc_rolling_metrics(trades, 10)
            m20 = monitor.calc_rolling_metrics(trades, 20)
            level, msg = monitor.check_degradation(m10, m20)

            if level == "CRITICAL":
                log(f"  CRITICO {tf}: {msg}", "WARN")
                notify(f"SIGMA ALERTA — {tf.upper()}",
                       f"Performance degradada!\n\n{msg}\n\nWR10: {m10.get('wr',0):.1f}%",
                       critical=True)
                run_bayesian(tf, trials=100)
            elif level == "WARN":
                log(f"  WARN {tf}: {msg}", "WARN")
    except Exception as e:
        log(f"  Monitor error: {e}", "ERROR")


def run_adaptive_retrain(tf="1h"):
    """Reentrenamiento adaptativo cada 30 dias — recalibra al mercado actual."""
    script = OUTPUT_DIR / "engine" / "optimization" / "adaptive_retrain.py"
    if not script.exists():
        return
    log(f"  Adaptive retrain {tf}...")
    try:
        result = subprocess.run(
            ["python", "-u", "-X", "utf8", str(script)],
            cwd=str(OUTPUT_DIR), capture_output=True, text=True, timeout=14400
        )
        if result.returncode == 0:
            current = OUTPUT_DIR / "models" / tf / "current_params.json"
            if current.exists():
                log(f"  Adaptive retrain {tf}: params actualizados")
                notify(f"SIGMA {tf.upper()} — Recalibrado",
                       f"Modelo {tf} recalibrado al mercado actual.\nVer models/{tf}/current_params.json")
        else:
            log(f"  Adaptive retrain {tf}: error", "ERROR")
    except Exception as e:
        log(f"  Adaptive retrain {tf}: {e}", "ERROR")


def search_lower_tfs(tfs):
    """
    Busca estrategias para 1m/5m/15m cada 12 horas.
    Usa semilla aleatoria diferente cada vez para explorar distinto espacio.
    Solo notifica si encuentra algo valido (CAGR > 0 en OOS).
    """
    import random
    seed = random.randint(0, 99999)
    log(f"BUSQUEDA LOWER-TF (seed={seed}): {tfs}")

    for tf in tfs:
        script = OUTPUT_DIR / "engine" / "optimization" / "improve_lower_tf.py"
        if not script.exists():
            continue
        samples = {"1m": 400, "5m": 500, "15m": 600}.get(tf, 500)
        log(f"  {tf}: {samples} muestras...")
        try:
            result = subprocess.run(
                ["python", "-u", "-X", "utf8", str(script),
                 "--tf", tf, "--samples", str(samples)],
                cwd=str(OUTPUT_DIR), capture_output=True, text=True,
                timeout=7200, env={**__import__("os").environ, "PYTHONHASHSEED": str(seed)}
            )
            # Verificar si encontro modelo
            model = OUTPUT_DIR / "models" / tf / "best_validated.json"
            if model.exists():
                with open(model) as f:
                    m = json.load(f)
                mi = m.get("metrics_oos", m.get("metrics_is", {}))
                cagr = mi.get("cagr", 0)
                wr   = mi.get("winrate", 0)
                if cagr > 0:
                    log(f"  {tf}: EDGE ENCONTRADO! CAGR {cagr:+.1f}% | WR {wr:.1f}%")
                    notify(f"SIGMA {tf.upper()} — NUEVO MODELO",
                           f"Edge encontrado en {tf.upper()}!\n\nCAGR: {cagr:+.1f}%/ano\nWR: {wr:.1f}%\n\nPine Script generado.")
                    # Generar Pine Script
                    generate_pine_scripts([tf])
                    update_dashboard()
                else:
                    log(f"  {tf}: sin edge (CAGR {cagr:+.1f}%) — reintentando en 12h")
        except Exception as e:
            log(f"  {tf}: error - {e}", "ERROR")


def daily_pipeline(tfs):
    """Pipeline completo diario — corre a las 06:00 UTC."""
    log(f"PIPELINE DIARIO INICIADO — TFs: {tfs}")
    results_summary = []

    for tf in tfs:
        log(f"--- {tf.upper()} ---")
        try:
            # Frecuencia de re-optimizacion por TF
            max_days = {"1m":3, "5m":5, "15m":7, "1h":7, "4h":14}.get(tf, 7)

            if needs_reoptimization(tf, max_days=max_days):
                if tf in ("1h", "4h", "1d"):
                    # TFs altos: Bayesian + crypto (funcionan bien)
                    trials = {"1h": 300, "4h": 150, "1d": 200}.get(tf, 200)
                    run_bayesian(tf, trials=trials)
                    samples = {"1h": 400, "4h": 300, "1d": 200}.get(tf, 300)
                    run_crypto_search(tf, samples=samples)
                else:
                    # TFs bajos: estrategias simples (Bayesian no funciona aqui)
                    run_lower_tf_search(tf)

            # Leer mejor resultado guardado
            model_path = OUTPUT_DIR / "models" / tf / "best_validated.json"
            if model_path.exists():
                with open(model_path) as f:
                    m_data = json.load(f)
                m = m_data.get("metrics_oos", m_data.get("metrics_is", {}))
                results_summary.append({
                    "tf": tf,
                    "cagr": m.get("cagr", 0),
                    "winrate": m.get("winrate", 0),
                    "trades": m.get("trades", 0),
                    "max_dd": m.get("max_dd", 0),
                })
                log(f"  {tf}: CAGR {m.get('cagr',0):+.1f}%/año | "
                    f"WR {m.get('winrate',0):.1f}% | "
                    f"Trades {m.get('trades',0)}")

        except Exception as e:
            log(f"  Error en {tf}: {e}", "ERROR")
            traceback.print_exc()

    # Pine Scripts y dashboard
    generate_pine_scripts(tfs)
    update_dashboard()

    # Reporte del dia
    log("PIPELINE COMPLETADO")
    if results_summary:
        lines = ["SIGMA ENGINE — Reporte Diario\n"]
        for r in results_summary:
            lines.append(f"{r['tf'].upper()}: CAGR {r['cagr']:+.1f}%/año | "
                         f"WR {r['winrate']:.1f}% | {r['trades']}T | DD {r['max_dd']:.1f}%")
        notify("SIGMA — Pipeline Diario", "\n".join(lines))


def monitor_check(tfs):
    """Check cada 30 minutos."""
    check_live_performance(tfs)
    update_dashboard()


def show_status(tfs):
    """Muestra estado en consola cada hora."""
    log("=== STATUS ===")
    for tf in tfs:
        model = OUTPUT_DIR / "models" / tf / "best_validated.json"
        if model.exists():
            with open(model) as f:
                m_data = json.load(f)
            m    = m_data.get("metrics_oos", m_data.get("metrics_is", {}))
            days = (datetime.now() -
                    datetime.fromtimestamp(model.stat().st_mtime)).days
            log(f"  {tf}: CAGR {m.get('cagr',0):+.1f}%/año | "
                f"WR {m.get('winrate',0):.1f}% | "
                f"Trades {m.get('trades',0)} | "
                f"Modelo: {days}d")
        else:
            log(f"  {tf}: sin modelo")


def start_autonomous(tfs=None, check_interval=30):
    """
    Inicia el sistema autonomo permanente.
    No retorna — corre hasta que se detiene manualmente.
    """
    tfs = tfs or ["1m", "5m", "15m", "1h", "4h"]

    log("="*60)
    log("SIGMA ENGINE — MODO AUTONOMO PERMANENTE INICIADO")
    log(f"TFs: {tfs}")
    log(f"Monitor: cada {check_interval} min")
    log(f"Pipeline diario: 06:00 UTC")
    log(f"Logs: {LOG_PATH}")
    log("="*60)

    # Pipeline ahora si hay TFs sin modelo
    missing_upper = [tf for tf in tfs if tf in ("1h","4h","1d")
                     and not (OUTPUT_DIR/"models"/tf/"best_validated.json").exists()]
    missing_lower = [tf for tf in tfs if tf in ("1m","5m","15m")
                     and not (OUTPUT_DIR/"models"/tf/"best_validated.json").exists()]

    if missing_upper:
        log(f"TFs altos sin modelo: {missing_upper} → pipeline inmediato")
        daily_pipeline(missing_upper)

    if missing_lower:
        log(f"TFs bajos sin modelo: {missing_lower} → busqueda inmediata")
        search_lower_tfs(missing_lower)

    if not missing_upper and not missing_lower:
        log("Todos los TFs tienen modelo. Proximos pipelines programados.")

    # 1m eliminado — no viable con 30 dias de datos
    lower_tfs = [tf for tf in tfs if tf in ("5m", "15m")]
    upper_tfs = [tf for tf in tfs if tf in ("1h", "4h", "1d")]

    # Schedules
    schedule.every().day.at("06:00").do(lambda: daily_pipeline(upper_tfs))
    schedule.every().day.at("18:00").do(lambda: daily_pipeline(upper_tfs))  # 2x/dia para TFs altos
    schedule.every(check_interval).minutes.do(lambda: monitor_check(tfs))
    schedule.every().hour.do(lambda: show_status(tfs))
    schedule.every().monday.at("09:00").do(
        lambda: notify("SIGMA Semanal",
                       "Revisa optimization_results.csv y pine_scripts/")
    )

    # TFs bajos: buscar cada 12 horas hasta encontrar edge
    if lower_tfs:
        schedule.every(12).hours.do(lambda: search_lower_tfs(lower_tfs))
        log(f"TFs bajos {lower_tfs}: buscando cada 12h")

    # Adaptive retrain 1H cada 30 dias — recalibra parametros al mercado actual
    schedule.every(30).days.do(lambda: run_adaptive_retrain("1h"))
    log("Adaptive retrain 1H: cada 30 dias")

    # Status inicial
    show_status(tfs)
    update_dashboard()

    notify("SIGMA ENGINE ACTIVO",
           f"Sistema autonomo iniciado.\n"
           f"TFs: {', '.join(tfs)}\n"
           f"Pipeline diario: 06:00 UTC\n"
           f"Monitor: cada {check_interval} min\n\n"
           f"Ver logs: results/reports/autonomous_log.txt")

    log("Scheduler activo. Ctrl+C para detener.")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            log("Sistema detenido manualmente.")
            break
        except Exception as e:
            log(f"Error en loop principal: {e}", "ERROR")
            time.sleep(60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tfs",      nargs="+", default=["5m","15m","1h","4h"])
    parser.add_argument("--interval", type=int,  default=30)
    parser.add_argument("--now",      action="store_true")
    args = parser.parse_args()

    if args.now:
        daily_pipeline(args.tfs)
    else:
        start_autonomous(args.tfs, args.interval)
