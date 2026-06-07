#!/usr/bin/env python3
"""
decay_detector.py — Detecta champions cuya performance live diverge del backtest.
Corre diariamente via cron. Envia alerta a Telegram si hay decaimiento real.

Logica:
  - Para cada champion con >= MIN_LIVE_TRADES en produccion:
    - Compara live WR vs expected WR del OOS
    - Compara live avg_pnl vs 0 (profitable?)
    - Si live WR < expected * DECAY_THR O pnl_live < 0 con N>=10 → DECAY
  - Envia resumen diario (champions OK + decayendo)
"""
import json, glob, os, sys, urllib.request
from datetime import datetime, timedelta
from collections import defaultdict

TOKEN   = open('/opt/sigma/config/tg_token.txt').read().strip()
CHAT_ID = '-1003787411069'
TRADE_STATE = '/opt/sigma/results/trade_state.json'
MODELS_DIR  = '/opt/sigma/models'
MIN_LIVE_TRADES = 8    # minimo trades live para evaluar decaimiento
DECAY_THR       = 0.55 # live WR < expected_WR * 0.55 → alerta
DECAY_DAYS      = 30   # solo considerar trades de los ultimos N dias

def send_tg(msg):
    data = json.dumps({'chat_id': CHAT_ID, 'text': msg,
                       'parse_mode': 'HTML', 'disable_web_page_preview': True}).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{TOKEN}/sendMessage',
        data=data, headers={'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f'TG error: {e}')

def load_champions():
    """Carga todos los JSON de champions. Retorna dict keyed by (asset, tf, strategy)."""
    champs = {}
    for jf in glob.glob(os.path.join(MODELS_DIR, '**', '*.json'), recursive=True):
        try:
            with open(jf) as f:
                d = json.load(f)
            sym = d.get('symbol', '').replace('/USDT', '')
            tf  = d.get('tf', '')
            strat = d.get('strategy', '')
            if sym and tf and strat:
                champs[(sym, tf, strat)] = d
        except Exception:
            pass
    return champs

def load_live_trades():
    """Carga historico de trades del paper trading."""
    with open(TRADE_STATE) as f:
        ts = json.load(f)
    history = ts.get('history', [])
    # Filter to last DECAY_DAYS days
    cutoff = datetime.now() - timedelta(days=DECAY_DAYS)
    recent = []
    for t in history:
        try:
            closed_at_str = t.get('closed_at', '')
            if closed_at_str:
                closed_at = datetime.fromisoformat(closed_at_str[:19])
                if closed_at >= cutoff:
                    recent.append(t)
            else:
                recent.append(t)  # include if no date (old format)
        except Exception:
            recent.append(t)
    return recent

def analyze_decay(champions, live_trades):
    """
    Para cada champion con suficientes trades live, evalua decaimiento.
    Retorna lista de (key, status, details).
    """
    # Group live trades by (sym, tf, strategy)
    by_key = defaultdict(list)
    for t in live_trades:
        sym   = t.get('sym', '')
        tf    = t.get('tf', '')
        strat = t.get('strategy', '')
        if sym and tf and strat:
            by_key[(sym, tf, strat)].append(t)

    results = []
    for key, champ in champions.items():
        trades_live = by_key.get(key, [])
        n = len(trades_live)
        if n < MIN_LIVE_TRADES:
            continue

        m_oos = champ.get('metrics_oos', {})
        expected_wr   = m_oos.get('wr', 50)
        expected_cagr = m_oos.get('cagr', 0)

        wins  = sum(1 for t in trades_live if t.get('status') == 'TP_HIT')
        live_wr = wins / n * 100
        live_pnl_total = sum(t.get('pnl_dollar', 0) for t in trades_live)
        live_avg_pnl   = live_pnl_total / n

        wr_ratio = live_wr / expected_wr if expected_wr > 0 else 1.0

        decaying = False
        reasons  = []

        if wr_ratio < DECAY_THR:
            decaying = True
            reasons.append(f'WR live {live_wr:.0f}% vs esperado {expected_wr:.0f}% (ratio {wr_ratio:.2f}x)')

        if live_pnl_total < 0 and n >= 10:
            decaying = True
            reasons.append(f'PnL live negativo: ${live_pnl_total:+.1f} en {n} trades')

        status = 'DECAY' if decaying else 'OK'
        results.append({
            'key': key, 'status': status, 'n': n,
            'live_wr': live_wr, 'expected_wr': expected_wr,
            'live_pnl': live_pnl_total, 'reasons': reasons,
            'expected_cagr': expected_cagr
        })

    return results

def build_message(results):
    if not results:
        return (
            "🔬 <b>Decay Detector — sin datos suficientes</b>\n\n"
            "Ningún champion tiene suficientes trades live para evaluar decaimiento "
            f"(mínimo {MIN_LIVE_TRADES} trades).\n\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M')}</i>"
        )

    decaying = [r for r in results if r['status'] == 'DECAY']
    ok       = [r for r in results if r['status'] == 'OK']

    lines = ["🔬 <b>Decay Detector — Reporte Diario</b>\n"]

    if decaying:
        lines.append(f"⚠️ <b>{len(decaying)} champion(s) con señales de decaimiento:</b>")
        for r in decaying:
            sym, tf, strat = r['key']
            lines.append(f"\n  <b>{sym} {tf} — {strat}</b> ({r['n']} trades live)")
            for reason in r['reasons']:
                lines.append(f"    • {reason}")
            lines.append(f"    • PnL total: ${r['live_pnl']:+.1f} | CAGR esperado: {r['expected_cagr']:+.1f}%")
    else:
        lines.append("✅ <b>Sin decaimiento detectado</b>")

    if ok:
        lines.append(f"\n✅ <b>{len(ok)} champion(s) performando OK:</b>")
        for r in ok:
            sym, tf, strat = r['key']
            lines.append(
                f"  {sym} {tf} {strat} — "
                f"WR live {r['live_wr']:.0f}% vs {r['expected_wr']:.0f}% esperado "
                f"({r['n']}T, ${r['live_pnl']:+.1f})"
            )

    lines.append(f"\n<i>Evaluado con trades de los últimos {DECAY_DAYS} días — {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
    return '\n'.join(lines)

if __name__ == '__main__':
    print(f"[decay_detector] {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    try:
        champions   = load_champions()
        live_trades = load_live_trades()
        print(f"  Champions cargados: {len(champions)}")
        print(f"  Trades live (últimos {DECAY_DAYS}d): {len(live_trades)}")

        results = analyze_decay(champions, live_trades)
        print(f"  Champions con suficientes trades: {len(results)}")
        for r in results:
            print(f"  {r['key']} -> {r['status']} ({r['n']}T WR {r['live_wr']:.0f}% vs {r['expected_wr']:.0f}%)")

        msg = build_message(results)
        send_tg(msg)
        print("  Telegram enviado OK")
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        traceback.print_exc()
        # Send error to TG too
        send_tg(f"⚠️ Decay Detector error: {e}")
