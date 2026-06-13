#!/usr/bin/env python3
"""
SIGMA ENGINE — Agente 3: Investigador de Trades (LLM + estadistico)
Analiza trades perdedores para identificar patrones y proponer filtros.

Con ANTHROPIC_API_KEY: llama a Claude para analisis narrativo profundo.
Sin key: analisis estadistico puro (siempre util).

Output:
  - /opt/sigma/results/reports/research_insights.json
  - Mensaje Telegram con hallazgos (si hay algo nuevo)

Cron: domingos 07:00 Chile
"""
import json, math, sys, os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/opt/sigma')

BASE   = Path('/opt/sigma')
OUT    = BASE / 'results/reports/research_insights.json'
CHILE  = timezone(timedelta(hours=-4))

def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default

def _now():
    return datetime.now(CHILE)

# ─── Statistical analysis (always runs) ────────────────────────────────────────

def _stat_analysis(hist):
    """Analisis estadistico de patrones en trades perdedores."""
    if not hist:
        return {}

    losers = [t for t in hist if (t.get('pnl_pct') or 0) < 0]
    winners = [t for t in hist if (t.get('pnl_pct') or 0) > 0]

    if len(losers) < 3:
        return {'note': 'Insuficientes trades perdedores para analizar'}

    insights = []

    # 1. Perdedores por TF
    tf_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'total_pnl': 0.0})
    for t in hist:
        tf = t.get('tf', '?')
        tf_stats[tf]['n'] += 1
        pnl = t.get('pnl_pct', 0) or 0
        if pnl > 0:
            tf_stats[tf]['wins'] += 1
        tf_stats[tf]['total_pnl'] += pnl

    worst_tf = min(tf_stats.items(), key=lambda x: x[1]['total_pnl'] / max(x[1]['n'], 1))
    if worst_tf[1]['n'] >= 3:
        wr = worst_tf[1]['wins'] / worst_tf[1]['n'] * 100
        if wr < 50:
            insights.append({
                'type': 'TF_WEAK',
                'severity': 'medium',
                'msg': f"TF {worst_tf[0]} tiene WR {wr:.0f}% ({worst_tf[1]['n']} trades) — considerar reducir Kelly",
                'action': f"Reducir kelly_pct en slots {worst_tf[0]} en 20%",
            })

    # 2. Perdedores por estrategia
    strat_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in hist:
        k = f"{t.get('sym','?')}/{t.get('strategy','?')}"
        strat_stats[k]['n'] += 1
        pnl = t.get('pnl_pct', 0) or 0
        if pnl > 0:
            strat_stats[k]['wins'] += 1
        strat_stats[k]['pnl'] += pnl

    for k, v in strat_stats.items():
        if v['n'] >= 4:
            wr = v['wins'] / v['n'] * 100
            if wr < 40:
                insights.append({
                    'type': 'STRATEGY_UNDERPERFORM',
                    'severity': 'high',
                    'msg': f"{k} WR {wr:.0f}% ({v['n']} trades) — muy por debajo del backtest",
                    'action': f"Marcar {k} para re-optimizacion urgente",
                })

    # 3. Perdedores por hora del dia
    hour_pnl = defaultdict(list)
    for t in losers:
        ts = t.get('opened_at', '') or ''
        try:
            hour = int(ts[11:13])
            hour_pnl[hour].append(t.get('pnl_pct', 0) or 0)
        except Exception:
            pass

    if len(hour_pnl) >= 3:
        worst_hours = sorted(hour_pnl.items(), key=lambda x: sum(x[1]))[:2]
        for h, pnls in worst_hours:
            if len(pnls) >= 2:
                avg = sum(pnls) / len(pnls)
                if avg < -2.0:
                    insights.append({
                        'type': 'TIME_PATTERN',
                        'severity': 'low',
                        'msg': f"Hora {h:02d}:00 UTC acumula perdidas promedio {avg:.1f}% ({len(pnls)} trades)",
                        'action': f"Agregar cooldown en hora {h:02d}:00-{(h+2)%24:02d}:00 UTC",
                    })

    # 4. Racha de perdedores (max drawdown streak)
    max_streak, cur_streak = 0, 0
    for t in hist:
        if (t.get('pnl_pct') or 0) < 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0

    if max_streak >= 4:
        insights.append({
            'type': 'LOSING_STREAK',
            'severity': 'medium',
            'msg': f"Racha maxima de {max_streak} trades perdedores consecutivos",
            'action': f"Activar circuit breaker despues de {max_streak-1} perdidas seguidas",
        })

    # 5. Sesgo direccional (si 100% short o 100% long)
    directions = set(t.get('direction', '') for t in hist)
    if len(directions) == 1:
        d = list(directions)[0]
        insights.append({
            'type': 'DIRECTION_BIAS',
            'severity': 'info',
            'msg': f"100% {d.upper()} — sin diversificacion direccional (probablemente por regimen BEAR)",
            'action': "Normal en BEAR. Revisar cuando regimen cambie a BULL.",
        })

    # 6. Slippage / tamanio SL vs realidad
    sl_hits = [t for t in losers if t.get('reason') == 'SL_HIT']
    if len(sl_hits) >= 3:
        sl_dists = [t.get('sl_dist_pct_at_open', 0) or 0 for t in sl_hits]
        avg_sl = sum(sl_dists) / len(sl_dists)
        actual_losses = [abs(t.get('pnl_pct', 0) or 0) for t in sl_hits]
        avg_loss = sum(actual_losses) / len(actual_losses)
        if avg_loss > avg_sl * 1.3:
            insights.append({
                'type': 'SLIPPAGE_HIGH',
                'severity': 'high',
                'msg': f"SL teorico {avg_sl:.2f}% vs perdida real {avg_loss:.2f}% — slippage alto ({avg_loss/avg_sl:.1f}x)",
                'action': "Revisar execution quality o ampliar SL en 30%",
            })

    # Summary stats
    total_pnl = sum(t.get('pnl_pct', 0) or 0 for t in hist)
    avg_win   = sum(t.get('pnl_pct', 0) or 0 for t in winners) / len(winners) if winners else 0
    avg_loss  = sum(abs(t.get('pnl_pct', 0) or 0) for t in losers) / len(losers) if losers else 0
    rr        = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        'trades_analyzed': len(hist),
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(len(winners) / len(hist) * 100, 1),
        'avg_win_pct': round(avg_win, 2),
        'avg_loss_pct': round(-avg_loss, 2),
        'reward_risk': round(rr, 2),
        'total_pnl': round(total_pnl, 2),
        'insights': insights,
    }

# ─── LLM Analysis (with Anthropic key) ─────────────────────────────────────────

def _llm_analysis(hist, stat_result):
    """Analisis narrativo via Claude API."""
    secrets_path = BASE / 'engine/config/secrets.json'
    sec = {}
    try:
        sec = json.loads(secrets_path.read_text())
    except Exception:
        pass

    api_key = sec.get('ANTHROPIC_API_KEY', '') or os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        print(f'[RESEARCHER] Anthropic import error: {e}', flush=True)
        return None

    # Build trade summary for Claude
    losers = [t for t in hist if (t.get('pnl_pct') or 0) < 0][-20:]
    summary_lines = []
    for t in losers:
        summary_lines.append(
            f"- {t.get('sym')}/{t.get('tf')} {t.get('direction','?').upper()} "
            f"strategy={t.get('strategy','?')} "
            f"PnL={t.get('pnl_pct',0):.2f}% "
            f"reason={t.get('reason','?')} "
            f"opened={str(t.get('opened_at',''))[:16]}"
        )
    trade_text = '\n'.join(summary_lines)

    insights_text = '\n'.join(
        f"- [{i['severity'].upper()}] {i['msg']}"
        for i in stat_result.get('insights', [])
    )

    prompt = f"""Eres un analista quant de un hedge fund de crypto. Analiza estos trades perdedores del sistema SIGMA ENGINE:

ESTADISTICAS GENERALES:
- Total trades analizados: {stat_result.get('trades_analyzed')}
- Win Rate: {stat_result.get('win_rate')}%
- Reward/Risk ratio: {stat_result.get('reward_risk')}
- Avg win: {stat_result.get('avg_win_pct')}% | Avg loss: {stat_result.get('avg_loss_pct')}%

HALLAZGOS ESTADISTICOS YA IDENTIFICADOS:
{insights_text if insights_text else 'Ninguno destacado'}

ULTIMOS 20 TRADES PERDEDORES:
{trade_text}

Por favor:
1. Identifica el patron mas comun en estos trades perdedores (max 2 oraciones)
2. Propone 2-3 filtros concretos para reducir estas perdidas (ej: "si RSI > 70 en 4h, no entrar long")
3. Evalua si el sistema parece overfitteado o si es ruido normal de mercado
4. Una recomendacion de accion inmediata

Responde en ESPAÑOL, maximo 200 palabras, formato conciso."""

    try:
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return resp.content[0].text if resp.content else None
    except Exception as e:
        print(f'[RESEARCHER] Claude API error: {e}', flush=True)
        return None

# ─── Telegram ─────────────────────────────────────────────────────────────────

def _send_telegram(stat_result, llm_text):
    import urllib.request, urllib.parse
    try:
        from utils.secrets import get_tg_token
        token = get_tg_token()
    except Exception:
        token = None
    if not token:
        return

    high = [i for i in stat_result.get('insights', []) if i.get('severity') == 'high']
    medium = [i for i in stat_result.get('insights', []) if i.get('severity') == 'medium']

    if not high and not medium and not llm_text:
        return  # Nada critico que reportar

    msg  = '<b>🔬 SIGMA — Reporte Semanal de Investigacion</b>\n\n'
    msg += f'<b>Stats live:</b> {stat_result.get("trades_analyzed")} trades | WR {stat_result.get("win_rate")}% | RR {stat_result.get("reward_risk")}\n\n'

    if high:
        msg += '<b>🚨 Hallazgos criticos:</b>\n'
        for i in high:
            msg += f'• {i["msg"]}\n  <i>Accion: {i["action"]}</i>\n'
        msg += '\n'

    if medium:
        msg += '<b>⚠️ Hallazgos moderados:</b>\n'
        for i in medium:
            msg += f'• {i["msg"]}\n'
        msg += '\n'

    if llm_text:
        msg += f'<b>🤖 Analisis Claude:</b>\n<i>{llm_text[:600]}</i>'

    chat_id = '-1003787411069'
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data),
            timeout=10
        )
        print('[RESEARCHER] Telegram enviado', flush=True)
    except Exception as e:
        print(f'[RESEARCHER] Telegram error: {e}', flush=True)

# ─── Main ─────────────────────────────────────────────────────────────────────

def run(send_telegram=False):
    ts   = _load(BASE / 'results/trade_state.json', {})
    hist = ts.get('history', [])

    print(f'[RESEARCHER] Analizando {len(hist)} trades...', flush=True)
    stat = _stat_analysis(hist)

    # LLM analysis
    llm_text = None
    if len(hist) >= 10:
        llm_text = _llm_analysis(hist, stat)
        if llm_text:
            print('[RESEARCHER] Analisis LLM completado', flush=True)
        else:
            print('[RESEARCHER] Modo estadistico (sin API key o error)', flush=True)

    # Save
    out = {
        'computed_at': _now().isoformat(),
        'stat_analysis': stat,
        'llm_narrative': llm_text,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f'[RESEARCHER] Guardado en {OUT}', flush=True)

    # Report to console
    for ins in stat.get('insights', []):
        sev = ins.get('severity','?').upper()
        print(f'  [{sev}] {ins["msg"]}', flush=True)
        print(f'       Accion: {ins["action"]}', flush=True)

    if llm_text:
        print('\n[CLAUDE INSIGHT]', flush=True)
        print(llm_text, flush=True)

    if send_telegram:
        _send_telegram(stat, llm_text)

    return out


if __name__ == '__main__':
    send_tg = '--telegram' in sys.argv or os.getenv('SEND_TELEGRAM', '0') == '1'
    run(send_telegram=send_tg)
