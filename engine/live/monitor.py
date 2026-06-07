"""
SIGMA ENGINE — Live Monitor
Corre en segundo plano, detecta degradacion del edge y decide cuando re-optimizar.

Logica:
  1. Lee el Excel motor (SIGMA K1) para obtener trades reales
  2. Calcula WR rolling, PF rolling, degradacion
  3. Si detecta degradacion → dispara re-optimizacion automatica
  4. Notifica via popup/beep cuando hay accion requerida
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import schedule
import numpy as np
import subprocess
import winsound
from pathlib import Path
from datetime import datetime, timedelta

try:
    import pandas as pd
    import openpyxl
except ImportError:
    pass

OUTPUT_DIR = Path(__file__).parent.parent.parent
CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)


class PerformanceMonitor:
    """
    Monitor de performance en tiempo real.
    Lee trades del Excel y detecta cuando el sistema necesita re-calibracion.
    """

    def __init__(self, excel_path=None):
        self.excel_path = excel_path or (OUTPUT_DIR / "SIGMA_K1_15M_PRO_v17_FINAL (2).xlsx")
        self.v = CFG["validation_thresholds"]
        self.r = CFG["risk"]
        self.history = []
        self.last_check = None
        self.alerts_sent = []

    def load_trades_from_excel(self):
        """Lee el log de trades del Excel motor."""
        if not self.excel_path.exists():
            return []
        try:
            df = pd.read_excel(self.excel_path, sheet_name="TRADES_LOG")
            # Filtrar solo trades cerrados con resultado
            closed = df[df.get("STATUS", df.columns[0]).notna()]
            return closed.to_dict("records")
        except Exception as e:
            print(f"  [MONITOR] Error leyendo Excel: {e}")
            return []

    def calc_rolling_metrics(self, trades, window=20):
        """Metricas sobre los ultimos N trades."""
        if len(trades) < 3:
            return {}
        recent = trades[-window:]
        pnls   = [t.get("pnl_usd", t.get("PNL_USD", 0)) for t in recent
                  if t.get("pnl_usd", t.get("PNL_USD")) is not None]
        if not pnls:
            return {}
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr     = len(wins) / len(pnls) * 100 if pnls else 0
        pf     = sum(wins) / abs(sum(losses)) if losses else 999
        exp    = np.mean(pnls) if pnls else 0
        return {
            "n": len(pnls), "wr": wr, "pf": pf,
            "expectancy": exp,
            "avg_win":    np.mean(wins)   if wins   else 0,
            "avg_loss":   np.mean(losses) if losses else 0,
        }

    def check_degradation(self, metrics_10, metrics_20):
        """
        Detecta si el edge se esta degradando.
        Retorna (nivel, mensaje) donde nivel = 'OK', 'WARN', 'CRITICAL'
        """
        if not metrics_10 or not metrics_20:
            return "INSUFFICIENT_DATA", "Necesitas mas trades"

        wr10  = metrics_10.get("wr", 0)
        wr20  = metrics_20.get("wr", 0)
        pf10  = metrics_10.get("pf", 0)

        min_wr = self.v["min_wr_operable"]
        min_pf = self.v["min_pf_operable"]

        issues = []

        if wr10 < min_wr - 15:
            issues.append(f"WR critica: {wr10:.1f}% (min {min_wr}%)")
        elif wr10 < min_wr:
            issues.append(f"WR baja: {wr10:.1f}% (min {min_wr}%)")

        if pf10 < min_pf:
            issues.append(f"PF bajo: {pf10:.2f} (min {min_pf})")

        wr_delta = wr20 - wr10
        if wr_delta > 15:
            issues.append(f"Degradacion WR: -{wr_delta:.1f}pts en ultimos 10T")

        if not issues:
            return "OK", f"Sistema saludable | WR10: {wr10:.1f}% | PF10: {pf10:.2f}"
        elif len(issues) >= 2:
            return "CRITICAL", " | ".join(issues)
        else:
            return "WARN", issues[0]

    def should_reoptimize(self, trades, tf="15m"):
        """
        Decide si hay que re-optimizar basado en:
        1. Degradacion de performance
        2. Cambio de regimen de mercado
        3. N dias desde ultima optimizacion
        """
        m10 = self.calc_rolling_metrics(trades, 10)
        m20 = self.calc_rolling_metrics(trades, 20)
        level, msg = self.check_degradation(m10, m20)

        # Verificar ultima optimizacion
        model_path = OUTPUT_DIR / "models" / tf / "config.json"
        days_since_opt = 999
        if model_path.exists():
            mtime = datetime.fromtimestamp(model_path.stat().st_mtime)
            days_since_opt = (datetime.now() - mtime).days

        reasons = []
        if level == "CRITICAL":
            reasons.append(f"Performance critica: {msg}")
        if days_since_opt > 14:
            reasons.append(f"Ultima optimizacion hace {days_since_opt} dias")
        if len(trades) > 0 and len(trades) % 50 == 0:
            reasons.append(f"Hito de {len(trades)} trades → re-calibrar")

        return bool(reasons), reasons, level, msg

    def run_check(self, tf="15m", auto_reoptimize=False):
        """Ejecuta un ciclo de verificacion."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"\n[{ts}] SIGMA MONITOR — Check {tf.upper()}")
        print("="*55)

        trades = self.load_trades_from_excel()
        n_trades = len(trades)
        print(f"  Trades en Excel: {n_trades}")

        if n_trades < 5:
            print(f"  Sin datos suficientes ({n_trades} trades). Necesitas al menos 5.")
            return

        m10 = self.calc_rolling_metrics(trades, 10)
        m20 = self.calc_rolling_metrics(trades, 20)
        level, msg = self.check_degradation(m10, m20)

        # Semaforo
        icons = {"OK": "[VERDE]", "WARN": "[AMARILLO]", "CRITICAL": "[ROJO]",
                 "INSUFFICIENT_DATA": "[SIN DATOS]"}
        print(f"  Estado: {icons.get(level, '?')} {msg}")

        if m10:
            print(f"  Rolling 10T: WR {m10['wr']:.1f}% | PF {m10['pf']:.2f} | "
                  f"Expect ${m10['expectancy']:.2f}")
        if m20:
            print(f"  Rolling 20T: WR {m20['wr']:.1f}% | PF {m20['pf']:.2f}")

        should_re, reasons, _, _ = self.should_reoptimize(trades, tf)

        if should_re:
            print(f"\n  ACCION REQUERIDA:")
            for r in reasons:
                print(f"    - {r}")

            if auto_reoptimize:
                print(f"\n  Auto re-optimizando {tf}...")
                self._trigger_reoptimize(tf)
            else:
                self._notify(
                    f"SIGMA MONITOR — Accion requerida",
                    f"TF: {tf.upper()}\n\n" + "\n".join(reasons) +
                    f"\n\nWR rolling: {m10.get('wr',0):.1f}%\n"
                    f"PF rolling: {m10.get('pf',0):.2f}",
                    critical=(level == "CRITICAL")
                )
        else:
            print(f"  Sin accion requerida.")

        self.last_check = datetime.now()
        return level, msg, m10, m20

    def _trigger_reoptimize(self, tf):
        """Lanza re-optimizacion automatica."""
        script = OUTPUT_DIR / "engine" / "optimization" / "bayesian_search.py"
        if script.exists():
            subprocess.Popen(
                ["python", "-u", str(script), "--tf", tf],
                cwd=str(OUTPUT_DIR)
            )
            print(f"  Bayesian optimizer lanzado para {tf}")
        else:
            print(f"  No se encontro bayesian_search.py")

    def _notify(self, title, msg, critical=False):
        """Notificacion de Windows."""
        try:
            freq = 1500 if critical else 1000
            n    = 5 if critical else 2
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

    def generate_daily_report(self, tf="15m"):
        """Genera reporte diario de performance."""
        from core.database import db_summary

        print(f"\n{'='*60}")
        print(f"  SIGMA DAILY REPORT — {datetime.now().strftime('%Y-%m-%d')}")
        print(f"{'='*60}")

        trades = self.load_trades_from_excel()

        if trades:
            all_m  = self.calc_rolling_metrics(trades, len(trades))
            m20    = self.calc_rolling_metrics(trades, 20)
            m10    = self.calc_rolling_metrics(trades, 10)

            print(f"\n  PERFORMANCE TOTAL ({len(trades)} trades):")
            print(f"  WR: {all_m.get('wr',0):.1f}% | PF: {all_m.get('pf',0):.2f} | "
                  f"Expect: ${all_m.get('expectancy',0):.2f}")

            if m20.get("n", 0) >= 10:
                print(f"\n  ROLLING 20T: WR {m20['wr']:.1f}% | PF {m20['pf']:.2f}")
            if m10.get("n", 0) >= 5:
                print(f"  ROLLING 10T: WR {m10['wr']:.1f}% | PF {m10['pf']:.2f}")

        print(f"\n  DATABASE:")
        db_summary()

        print(f"\n  MODELOS GUARDADOS:")
        for tf_dir in sorted((OUTPUT_DIR / "models").iterdir()):
            if tf_dir.is_dir() and (tf_dir / "config.json").exists():
                with open(tf_dir / "config.json") as f:
                    m = json.load(f)
                met = m.get("metrics", {})
                print(f"  {tf_dir.name}: "
                      f"CAGR {met.get('cagr', met.get('pnl_pct',0)):+.1f}%/año | "
                      f"WR {met.get('winrate',0):.1f}% | "
                      f"Trades {met.get('trades',0)}")


def start_scheduler(interval_minutes=30, tf="15m", auto_reoptimize=False):
    """
    Inicia el scheduler para checks periodicos.
    Corre como proceso en background.
    """
    monitor = PerformanceMonitor()
    print(f"\n[MONITOR] Iniciando scheduler — check cada {interval_minutes}min")
    print(f"  TF: {tf.upper()} | Auto-reopt: {auto_reoptimize}")
    print(f"  Ctrl+C para detener\n")

    def job():
        monitor.run_check(tf=tf, auto_reoptimize=auto_reoptimize)

    schedule.every(interval_minutes).minutes.do(job)
    # Reporte diario a las 08:00 UTC
    schedule.every().day.at("08:00").do(lambda: monitor.generate_daily_report(tf))

    job()  # Correr inmediatamente

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf",       default="15m")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--auto",     action="store_true",
                        help="Re-optimizar automaticamente si detecta degradacion")
    parser.add_argument("--report",   action="store_true",
                        help="Solo generar reporte y salir")
    args = parser.parse_args()

    if args.report:
        m = PerformanceMonitor()
        m.generate_daily_report(args.tf)
    else:
        start_scheduler(args.interval, args.tf, args.auto)
