#!/usr/bin/env python3
"""SIGMA ENGINE — Suite de regresión de bugs históricos.

Cada test clava un bug REAL que ya ocurrió y se corrigió. Si un test falla,
no es un bug nuevo: es un bug viejo que volvió. La historia de cada uno está
en el diario de decisiones / memoria del proyecto.

Correr:  /opt/sigma_env/bin/python /opt/sigma/tests/test_regression_bugs.py
(unittest puro, sin pytest; NUNCA importa web_server — ver test_04)
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path('/opt/sigma')
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'engine' / 'live'))


class TestReconcileGracePeriod(unittest.TestCase):
    """Bug potencial detectado 2026-07-02: reconcile() por cron sin período de
    gracia puede cerrar de emergencia una posición SANA si dispara en la
    ventana entre el fill de entrada y la colocación del SL (proceso separado
    de web_server). El grace period filtra trades recién abiertos."""

    def _run_reconcile(self, opened_at, min_age_min):
        import live_executor as lex
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / 'results').mkdir()
            (tmp / 'results' / 'reports').mkdir()
            state = {'open': {'LTC_4h': {
                'status': 'open', 'mode': 'LIVE', 'sym': 'LTC', 'tf': '4h',
                'opened_at': opened_at}}}
            (tmp / 'results' / 'trade_state.json').write_text(json.dumps(state))
            old_base, old_log = lex.BASE, lex.LOG_PATH
            old_getex, old_live = lex._get_exchange, lex.LIVE_MODE
            called = []
            try:
                lex.BASE = tmp
                lex.LOG_PATH = tmp / 'results' / 'reports' / 'executor.log'
                lex.LIVE_MODE = True
                # si reconcile llega a pedir exchange, lo registramos y cortamos
                lex._get_exchange = lambda: called.append(1) or None
                lex.reconcile(min_age_min=min_age_min)
            finally:
                lex.BASE, lex.LOG_PATH = old_base, old_log
                lex._get_exchange, lex.LIVE_MODE = old_getex, old_live
            return bool(called)

    def test_trade_recien_abierto_se_salta(self):
        from datetime import datetime, timezone, timedelta
        CHILE = timezone(timedelta(hours=-4))
        recien = datetime.now(CHILE).strftime('%Y-%m-%d %H:%M:%S.%f')
        self.assertFalse(
            self._run_reconcile(recien, min_age_min=10),
            'reconcile verificó un trade de <10 min: la carrera entre cron y '
            'execute_entry() volvió — puede cerrar posiciones sanas')

    def test_trade_viejo_si_se_verifica(self):
        self.assertTrue(
            self._run_reconcile('2026-06-01 10:00:00', min_age_min=10),
            'reconcile saltó un trade viejo: el grace period filtra de más '
            'y dejó posiciones sin vigilancia de SL')

    def test_opened_at_futuro_se_verifica_igual(self):
        # timestamp corrupto visto 2026-07-02 (fecha CEST + hora Chile):
        # un trade "del futuro" no puede ser recién abierto -> gana protección
        self.assertTrue(
            self._run_reconcile('2030-01-01 00:00:00', min_age_min=10),
            'timestamp corrupto en el futuro dejó el trade sin vigilancia de SL')

    def test_ambos_formatos_de_opened_at_parsean(self):
        # 2026-07-02: coexisten dos formatos reales en trade_state.json
        from datetime import datetime
        for s in ('2026-06-30 04:01:23.480255', '2026-07-02T20:42:31'):
            datetime.fromisoformat(s)  # no debe lanzar


class TestSafetyConstants(unittest.TestCase):
    """Los límites de seguridad de live_executor no se relajan por accidente.
    Cambiarlos exige decisión explícita + actualizar RISK_POLICY.md + este test."""

    def test_limites_live(self):
        import live_executor as lex
        self.assertLessEqual(lex.MAX_KELLY_PCT, 6.0)
        self.assertLessEqual(lex.MAX_KELLY_HARD_CAP, 15.0)
        self.assertLessEqual(lex.MAX_LEVERAGE, 5)
        self.assertLessEqual(lex.MAX_OPEN_SLOTS, 4)
        self.assertGreaterEqual(lex.MIN_GATE_SCORE, 85)
        # politica anti-inversion de riesgo 2026-07-02: el piso de notional de
        # Binance no puede multiplicar el Kelly decidido por mas de 2x
        self.assertLessEqual(lex.MAX_FORCED_KELLY_MULT, 2.0)


class TestImportBomb(unittest.TestCase):
    """Bug 2026-06-17: importar web_server desde un script duplicó el motor
    completo (segundo server + hilos de trading escribiendo al mismo estado).
    Regla: NADIE importa web_server. Nunca."""

    PATRON = re.compile(r'^\s*(from\s+web_server\s+import|import\s+web_server)\b',
                        re.MULTILINE)

    def test_nadie_importa_web_server(self):
        culpables = []
        for d in (BASE / 'engine', BASE / 'scripts', BASE / 'utils'):
            if not d.exists():
                continue
            for py in d.rglob('*.py'):
                if '__pycache__' in str(py) or '.bak' in py.name:
                    continue
                try:
                    if self.PATRON.search(py.read_text(errors='ignore')):
                        culpables.append(str(py))
                except OSError:
                    pass
        self.assertEqual(culpables, [],
                         f'Estos archivos importan web_server (import-bomb): {culpables}')

    def test_web_server_tiene_guard_main(self):
        # Guard agregado 2026-06-19 tras encontrar 2 motores duplicados corriendo
        src = (BASE / 'web_server.py').read_text(errors='ignore')
        self.assertRegex(src, r'if\s+__name__\s*==',
                         'web_server.py perdió el guard __main__: cualquier '
                         'import vuelve a duplicar el motor entero')


class TestSourceTripwires(unittest.TestCase):
    """Tripwires sobre el código fuente: frágiles a refactors legítimos (si
    renombras a propósito, actualiza el test), pero clavan regresiones de
    config que ya costaron semanas de cómputo o dinero real."""

    def test_pruner_warmup_cero(self):
        # Bug 2026-06-12: n_warmup_steps=5 con un solo reporte en step=0
        # = pruner muerto, 3-10x trabajo desperdiciado por trial malo
        src = (BASE / 'engine' / 'optimization' / 'asset_pipeline.py').read_text(errors='ignore')
        if 'MedianPruner' in src:
            self.assertIn('n_warmup_steps=0', src.replace(' ', ''),
                          'MedianPruner sin n_warmup_steps=0: el pruner vuelve '
                          'a ser dead code (solo hay reporte en step=0)')

    def test_reconcile_cron_usa_grace(self):
        src = (BASE / 'engine' / 'live' / 'reconcile_cron.py').read_text(errors='ignore')
        self.assertRegex(src, r'reconcile\(\s*min_age_min\s*=\s*[1-9]',
                         'reconcile_cron.py llama sin grace period: carrera '
                         'con execute_entry() reactivada')

    def test_paper_no_contamina_live(self):
        # Bug 2026-06-19/06-27: cierres LIVE caían al bucket PAPER ($10k
        # simulado) inflando equity/pnl ~20x. El fix ancla al último equity
        # real y alerta. Este tripwire exige que la señal del fix siga presente.
        src = (BASE / 'web_server.py').read_text(errors='ignore')
        self.assertIn('_last_live_equity', src)
        self.assertIn('NUNCA dejar caer un trade LIVE al bucket PAPER', src,
                      'desapareció el comentario-ancla del fix de contaminación '
                      'PnL LIVE→PAPER; verificar que el fix siga vivo antes de '
                      'actualizar este test')


class TestTradeStateIntegrity(unittest.TestCase):
    """El estado real no debe volver a los patrones que causaron incidentes."""

    def test_trades_live_abiertos_tienen_campos_minimos(self):
        state = json.loads((BASE / 'results' / 'trade_state.json').read_text())
        for k, tr in state.get('open', {}).items():
            if tr.get('status') == 'open' and tr.get('mode') == 'LIVE':
                for campo in ('sym', 'tf', 'entry', 'sl', 'opened_at'):
                    self.assertTrue(tr.get(campo),
                                    f'{k}: trade LIVE abierto sin {campo!r}')

    def test_equity_after_live_es_plausible(self):
        # Bug PnL fantasma: equity_after de $10k paper en trades LIVE reales
        state = json.loads((BASE / 'results' / 'trade_state.json').read_text())
        lives = [t for t in state.get('history', []) if t.get('mode') == 'LIVE'
                 and t.get('equity_after')]
        for t in lives:
            self.assertLess(t['equity_after'], 5000,
                            f"equity_after {t['equity_after']} en trade LIVE "
                            f"{t.get('sym')}/{t.get('tf')}: huele a contaminación "
                            f"paper ($10k) — investigar antes de tocar este umbral")


if __name__ == '__main__':
    unittest.main(verbosity=2)
