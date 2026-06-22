"""utils/regime_backtest.py -- Diagnostico de edge contra-tendencia (Fase 1 del
plan champions_regime, 2026-06-20/21).

Reusa el motor de backtest existente (engine/optimization/asset_pipeline.py:
backtest/metrics/score) sin modificarlo. Aporta dos cosas nuevas:

1. regime_tagged_backtest(): igual que backtest() pero etiqueta cada trade
   cerrado con el regimen activo EN LA ENTRADA (no en el cierre) y la
   direccion. Necesario porque backtest() no expone esto.

2. contiguous_segments() / trades_per_segment(): para el gate de Fase 3 --
   "rentable en >=2 segmentos de regimen distintos", no solo agregado sobre
   un bucket que puede ser un solo tramo largo (overfitting a un ciclo).

No reemplaza ni modifica apply_regime_gate ni ningun sig_xxx. El bypass del
gate para probar long-en-bear/short-en-bull se hace ANTES de llamar a estas
funciones, sobreescribiendo las columnas df['tradeable_long']/['tradeable_short']
en una COPIA del dataframe (ver countertrend_diagnostic.py) -- apply_regime_gate
sigue corriendo sin cambios, solo lee una columna distinta.
"""
import sys
sys.path.insert(0, "/opt/sigma")
from engine.optimization.asset_pipeline import COMMISSION, SLIPPAGE, CAPITAL

COST = COMMISSION + SLIPPAGE


def regime_tagged_backtest(df, sig, sl_s, tp_s, risk_pct=5.0):
    """Replica el loop simple de asset_pipeline.backtest() (sin Kelly dinamico,
    para comparabilidad directa entre regimenes) pero etiqueta cada trade con
    el regimen y la direccion vigentes en la barra de ENTRADA.

    Retorna lista de dicts: pnl, won, regime, direction, entry_pos (indice
    entero de la barra de entrada -- usado por contiguous_segments()).
    """
    c = df["close"].to_numpy(); h = df["high"].to_numpy(); lo = df["low"].to_numpy()
    sa = sig.to_numpy(); sla = sl_s.to_numpy(); tpa = tp_s.to_numpy()
    reg_bull = df["regime_bull"].to_numpy()
    reg_range = df["regime_range"].to_numpy()
    reg_bear = df["regime_bear"].to_numpy()

    cap = CAPITAL; pos = 0; entry_p = slv = tpv = sz = 0.0
    entry_regime = None; entry_pos = -1
    trades = []
    for i in range(1, len(c)):
        pr = c[i]
        if pos != 0:
            pnl = 0.0; closed = False
            if pos == 1:
                if lo[i] <= slv: pnl = sz * (slv - entry_p) - sz * (entry_p + slv) * COST; closed = True
                elif h[i] >= tpv: pnl = sz * (tpv - entry_p) - sz * (entry_p + tpv) * COST; closed = True
            else:
                if h[i] >= slv: pnl = sz * (entry_p - slv) - sz * (entry_p + slv) * COST; closed = True
                elif lo[i] <= tpv: pnl = sz * (entry_p - tpv) - sz * (entry_p + tpv) * COST; closed = True
            if closed:
                cap += pnl
                trades.append({
                    "pnl": pnl, "won": pnl > 0,
                    "regime": entry_regime,
                    "direction": "long" if pos == 1 else "short",
                    "entry_pos": entry_pos,
                })
                pos = 0
        if pos == 0 and sa[i - 1] != 0 and sla[i - 1] > 0 and cap > 50:
            rsl = abs(pr - sla[i - 1])
            if rsl > 0:
                sz = (cap * risk_pct / 100) / rsl
                pos = int(sa[i - 1]); entry_p = pr; slv = sla[i - 1]; tpv = tpa[i - 1]
                entry_pos = i - 1
                entry_regime = ("bull" if reg_bull[i - 1] else
                                 "bear" if reg_bear[i - 1] else
                                 "range" if reg_range[i - 1] else "?")
    return trades


def regime_days(df, regime_col, tf_minutes):
    """Dias 'activos' de un regimen (suma de barras * minutos/barra / 1440).
    Para CAGR/trades_year segmentados -- usar dias calendario completos
    diluiria artificialmente el CAGR de una estrategia que solo opera ~35%
    del tiempo por diseno (es la pregunta correcta para el gate: 'hay edge
    cuando esta activo', no 'cuanto aporta al portafolio en dias calendario').
    """
    n_bars = int(df[regime_col].sum())
    return max(n_bars * tf_minutes / 1440.0, 1.0)


def contiguous_segments(bool_series):
    """Lista de (start_pos, end_pos) inclusive de corridas contiguas True."""
    arr = bool_series.to_numpy()
    segments = []
    start = None
    for i, v in enumerate(arr):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segments.append((start, i - 1))
            start = None
    if start is not None:
        segments.append((start, len(arr) - 1))
    return segments


def filter_segments_by_duration(segments, tf_minutes, min_duration_days=14):
    """Descarta segmentos demasiado cortos para ser un 'ciclo' real antes de
    contarlos para el gate de robustez multi-ciclo.

    Hallazgo 2026-06-21: en TFs finos (15m/1h) el regimen calculado por
    add_features() (RSI semanal + EMA200) se fragmenta en decenas de tramos
    de pocos dias -- ruido de reclasificacion en el borde del umbral, no
    ciclos de mercado genuinos. Sin este filtro, 'segs=0/92' en un candidato
    con metricas fuertes no significa 'sin robustez', significa que el
    criterio de >=5 trades/segmento es imposible de cumplir cuando el 90%
    de los segmentos duran menos de una semana. Filtrar por duracion real
    (no por conteo de trades) separa ciclos genuinos de fragmentacion.
    """
    out = []
    for s, e in segments:
        n_bars = e - s + 1
        duration_days = n_bars * tf_minutes / 1440.0
        if duration_days >= min_duration_days:
            out.append((s, e))
    return out


def trades_per_segment(trades, segments):
    """Agrupa trades (de regime_tagged_backtest) por el segmento contiguo al
    que pertenece su entry_pos. Retorna dict seg_idx -> list[trade].
    Trades fuera de cualquier segmento (no deberia pasar si el regimen
    filtrado coincide con segments) se ignoran.
    """
    by_seg = {i: [] for i in range(len(segments))}
    for t in trades:
        pos = t["entry_pos"]
        for i, (s, e) in enumerate(segments):
            if s <= pos <= e:
                by_seg[i].append(t)
                break
    return by_seg


def segment_summary(by_seg, min_trades_per_segment=5):
    """Resumen por segmento + conteo de segmentos 'calificados' (n>=min y
    PnL no-catastrofico, definido como PnL total >= -50% del capital
    arriesgado total del segmento -- no exige ganar, exige no ser un
    desastre, para distinguir 'edge fragil pero real' de 'funciona una vez
    y revienta el resto').
    """
    out = []
    n_qualified = 0
    for seg_idx, trades in by_seg.items():
        if not trades:
            continue
        n = len(trades)
        wins = sum(1 for t in trades if t["won"])
        pnl = float(sum(t["pnl"] for t in trades))
        # bool() explicito -- pnl/n pueden ser numpy scalars (vienen de arrays
        # .to_numpy() en regime_tagged_backtest), "and" entre eso y un bool nativo
        # devuelve numpy.bool_, no serializable directo a JSON.
        qualified = bool(n >= min_trades_per_segment and pnl > -CAPITAL * 0.5)
        if qualified:
            n_qualified += 1
        out.append({
            "segment": int(seg_idx), "n_trades": int(n),
            "wr": round(float(wins) / n * 100, 1),
            "pnl": round(pnl, 2),
            "qualified": qualified,
        })
    return {"segments": out, "n_qualified": int(n_qualified), "n_segments_total": int(len([s for s in by_seg.values() if s]))}


def transition_zone_breakdown(trades, segments, transition_bars=10):
    """Separa trades entre 'zona de transicion' (primeras `transition_bars`
    barras de cada segmento, justo cuando el regimen recien cambio y hay
    menos historia para confiar) y 'zona establecida' (resto del segmento).

    Metrica de TRANSPARENCIA, no bloqueante (Fase 3 del plan champions_regime):
    una estrategia de rebote entrenada mayormente en pleno bear consolidado
    puede comportarse distinto justo en la transicion bull/range->bear, que es
    donde mas importa acertar y menos datos hay. No se usa para descalificar
    un candidato automaticamente -- queda para revision humana antes de Fase 5.
    """
    transition, established = [], []
    for t in trades:
        pos = t["entry_pos"]
        for s, e in segments:
            if s <= pos <= e:
                (transition if pos - s < transition_bars else established).append(t)
                break

    def _summ(lst):
        if not lst:
            return {"n": 0, "wr": None, "pnl": 0.0}
        n = len(lst)
        wins = sum(1 for t in lst if t["won"])
        pnl = float(sum(t["pnl"] for t in lst))
        return {"n": int(n), "wr": round(float(wins) / n * 100, 1), "pnl": round(pnl, 2)}

    return {"transition": _summ(transition), "established": _summ(established)}
