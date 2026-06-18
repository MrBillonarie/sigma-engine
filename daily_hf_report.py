#!/usr/bin/env python3
"""
SIGMA ENGINE — Daily Hedge Fund P&L Report
Genera reporte diario con metricas de un HF profesional:
  - P&L diario / MTD / YTD
  - Sharpe live (anualizado)
  - Attribution por estrategia y activo
  - Risk utilization (concentracion, VaR, vol budget)
  - Bayesian edges confirmados
  - Posiciones abiertas

Cron: 21:30 Chile (post evening_news)
"""
import json, math, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, '/opt/sigma')

BASE   = Path('/opt/sigma')
CHILE  = timezone(timedelta(hours=-4))

def _now():
    return datetime.now(CHILE)

def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except:
        return {}

def _sharpe(pnl_list, rf_per_trade=0.0):
    if len(pnl_list) < 5:
        return None
    mean = sum(pnl_list) / len(pnl_list)
    var  = sum((x - mean)**2 for x in pnl_list) / len(pnl_list)
    std  = math.sqrt(var) if var > 0 else 0
    if std == 0:
        return None
    per_trade = (mean - rf_per_trade) / std
    # Annualize: assume ~260 trades/year (5/week for active system)
    return round(per_trade * math.sqrt(260), 2)

def _sortino(pnl_list, rf_per_trade=0.0):
    # Como _sharpe pero el denominador solo cuenta retornos por debajo de rf_per_trade
    # (downside deviation) -- no penaliza la volatilidad al alza.
    if len(pnl_list) < 5:
        return None
    mean = sum(pnl_list) / len(pnl_list)
    downside = [min(0.0, x - rf_per_trade) for x in pnl_list]
    downside_var = sum(d*d for d in downside) / len(pnl_list)
    downside_dev = math.sqrt(downside_var)
    if downside_dev == 0:
        return None
    per_trade = (mean - rf_per_trade) / downside_dev
    return round(per_trade * math.sqrt(260), 2)

def build_report():
    ts    = _load(BASE / 'results/trade_state.json')
    snap  = _load(BASE / 'results/reports/port_snapshot.json')
    pr    = _load(BASE / 'results/reports/portfolio_risk.json')
    rb    = _load(BASE / 'results/reports/risk_budget.json')
    bayes = _load(BASE / 'results/reports/bayesian_edges.json')

    hist  = ts.get('history', [])
    port  = ts.get('portfolio', {})
    open_ = ts.get('open', {})

    now_str = _now().strftime('%d/%m/%Y %H:%M')
    today   = _now().strftime('%Y-%m-%d')
    month   = _now().strftime('%Y-%m')
    year    = _now().strftime('%Y')

    equity   = port.get('equity', 10000)
    initial  = port.get('initial_capital', 10000)
    peak     = port.get('peak_equity', equity)

    # ─── Period P&L ──────────────────────────────────────────────────────────
    def period_pnl(trades):
        wins  = [t for t in trades if (t.get('pnl_pct') or 0) > 0]
        total = sum(t.get('pnl_pct', 0) or 0 for t in trades)
        wr    = round(len(wins)/len(trades)*100, 1) if trades else 0
        return total, wr, len(trades)

    today_trades = [t for t in hist if str(t.get('closed_at','')).startswith(today)]
    mtd_trades   = [t for t in hist if str(t.get('closed_at','')).startswith(month)]
    ytd_trades   = [t for t in hist if str(t.get('closed_at','')).startswith(year)]

    today_pnl, today_wr, today_n = period_pnl(today_trades)
    mtd_pnl,   mtd_wr,   mtd_n   = period_pnl(mtd_trades)
    ytd_pnl,   ytd_wr,   ytd_n   = period_pnl(ytd_trades)
    total_pnl, total_wr, total_n  = period_pnl(hist)

    # ─── Sharpe / Sortino (live) ───────────────────────────────────────────────
    pnl_series = [t.get('pnl_pct', 0) or 0 for t in hist]
    live_sharpe  = _sharpe(pnl_series)
    live_sortino = _sortino(pnl_series)

    # ─── DD from peak ─────────────────────────────────────────────────────────
    dd_from_peak = round((equity - peak) / peak * 100, 2) if peak > 0 else 0
    max_dd       = abs(port.get('max_dd_pct', port.get('max_dd', 0)) or 0)

    # ─── Attribution by strategy ─────────────────────────────────────────────
    by_strat = defaultdict(lambda: {'n': 0, 'pnl': 0.0, 'wins': 0})
    for t in hist:
        key = f"{t.get('sym','?')}/{t.get('tf','?')}/{t.get('strategy','?')}"
        pnl = t.get('pnl_pct', 0) or 0
        by_strat[key]['n']   += 1
        by_strat[key]['pnl'] += pnl
        if pnl > 0:
            by_strat[key]['wins'] += 1

    top_strats = sorted(by_strat.items(), key=lambda x: -x[1]['pnl'])[:5]
    bot_strats = sorted(by_strat.items(), key=lambda x: x[1]['pnl'])[:3]

    # ─── Attribution by asset ─────────────────────────────────────────────────
    by_asset = pr.get('concentration', {}).get('assets', {})

    # ─── Risk utilization ─────────────────────────────────────────────────────
    vol_m     = rb.get('vol_metrics', {})
    rb_status = rb.get('status', 'UNKNOWN')
    ann_vol   = vol_m.get('annual_vol_pct', 0)
    vol_tgt   = vol_m.get('target_vol_pct', 30)
    kelly_g   = rb.get('kelly_guidance', {})
    hhi       = pr.get('concentration', {}).get('hhi', 0)
    n_eff     = pr.get('concentration', {}).get('n_effective_assets', 0)
    var_95    = pr.get('portfolio', {}).get('var_95_pct', 0)
    dir_split = pr.get('direction_split', {})

    # ─── Bayesian edges ───────────────────────────────────────────────────────
    edges     = bayes.get('strategies', {})
    confirmed = [(k, v) for k, v in edges.items() if v.get('confirmed_edge')]
    prob_edge = [(k, v) for k, v in edges.items() if v.get('posterior_mean_wr', 0) >= 0.55]

    # ─── Open positions ───────────────────────────────────────────────────────
    open_pos = list(open_.values()) if isinstance(open_, dict) else []

    # ─── Backtest reference ───────────────────────────────────────────────────
    bt_cagr  = snap.get('port_cagr_operational', snap.get('port_cagr', 0))
    bt_wr    = snap.get('port_wr', 0)
    bt_dd    = snap.get('port_dd', 0)
    bt_pf    = snap.get('port_pf', 0)
    n_champs = len(snap.get('champions', {}))

    lines = []
    div   = '─' * 50

    lines.append(div)
    lines.append(f'SIGMA ENGINE  |  HF Daily Report')
    lines.append(f'{now_str} (Chile)')
    lines.append(div)

    # P&L Summary
    lines.append(f'PERFORMANCE LIVE')
    lines.append(f'  Equity     : ${equity:,.2f}  ({(equity-initial)/initial*100:+.2f}% total)')
    lines.append(f'  Peak equity: ${peak:,.2f}  |  DD actual: {dd_from_peak:+.2f}%')
    lines.append(f'  Max DD ever: {-max_dd:.2f}%')
    if live_sharpe is not None:
        lines.append(f'  Sharpe live: {live_sharpe:+.2f}  (annualized, {total_n} trades)')
    if live_sortino is not None:
        lines.append(f'  Sortino live: {live_sortino:+.2f}  (annualized, downside-only)')
    lines.append('')

    lines.append('P&L PERIODS')
    lines.append(f'  Hoy ({today_n} trades) : {today_pnl:+.2f}%  WR {today_wr:.0f}%')
    lines.append(f'  MTD ({mtd_n} trades)   : {mtd_pnl:+.2f}%  WR {mtd_wr:.0f}%')
    lines.append(f'  Total ({total_n} trades): {total_pnl:+.2f}%  WR {total_wr:.0f}%  PF —')
    lines.append('')

    lines.append('BACKTEST REFERENCIA (47 champions)')
    lines.append(f'  CAGR: {bt_cagr:.1f}%  |  WR: {bt_wr:.1f}%  |  DD: {bt_dd:.1f}%  |  PF: {bt_pf:.2f}')
    lines.append(f'  Champions activos: {n_champs}')
    lines.append('')

    lines.append('TOP 5 ESTRATEGIAS (por P&L acumulado)')
    for k, v in top_strats:
        wr_s = round(v['wins']/v['n']*100) if v['n'] else 0
        lines.append(f'  {k}: {v["pnl"]:+.2f}%  ({v["n"]}T, WR {wr_s}%)')
    lines.append('')

    lines.append('CONCENTRACION POR ACTIVO')
    for asset, info in sorted(by_asset.items(), key=lambda x: -x[1].get('weight_pct', 0)):
        lines.append(f'  {asset}: {info["weight_pct"]:.1f}% del portafolio  ({info["n"]} trades, WR {info["wr"]:.0f}%)')
    lines.append(f'  HHI: {hhi:.3f}  |  N efectivo: {n_eff:.1f}  |  {"ALERTA concentracion" if hhi > 0.35 else "Diversificacion OK"}')
    lines.append('')

    lines.append('EXPOSICION DIRECCIONAL')
    for d, info in dir_split.items():
        lines.append(f'  {d.upper()}: {info["weight_pct"]:.0f}% ({info["n"]} trades, WR {info["wr"]:.0f}%)')
    lines.append('')

    lines.append('RISK UTILIZATION')
    lines.append(f'  Vol anual live: {ann_vol:.1f}%  /  target: {vol_tgt:.0f}%  |  Estado: {rb_status}')
    lines.append(f'  VaR 95% (1 trade): {var_95:.2f}%')
    if kelly_g:
        lines.append(f'  Kelly guidance: {kelly_g.get("recommendation","N/A")}')
    lines.append('')

    stress = pr.get('stress_test', {})
    if stress:
        lines.append('STRESS TEST (posiciones reales abiertas hoy)')
        for sc in stress.values():
            lines.append(f'  BTC {sc["btc_shock_pct"]:+.0f}%: portafolio {sc["portfolio_pnl_pct"]:+.2f}%  (${sc["portfolio_pnl_usd"]:+,.2f})')
        lines.append('')

    if edges:
        lines.append('BAYESIAN TRACKER')
        for k, v in list(edges.items())[:5]:
            n_b = v.get('n_trades', 0)
            wr_b = round(v.get('posterior_mean_wr', 0)*100, 1)
            conf = 'EDGE' if v.get('confirmed_edge') else '...'
            lines.append(f'  {k}: n={n_b}  WR post.={wr_b}%  [{conf}]')
        lines.append('')

    if open_pos:
        lines.append(f'POSICIONES ABIERTAS ({len(open_pos)})')
        for p in open_pos:
            lines.append(f'  {p.get("sym")}/{p.get("tf")} {p.get("direction","").upper()} @ {p.get("entry")} | SL {p.get("sl")} | TP {p.get("tp")}')
    else:
        lines.append('POSICIONES ABIERTAS: ninguna')

    lines.append(div)

    return '\n'.join(lines)


def telegram_format(report_text):
    lines = report_text.split('\n')
    msg = '<b>📊 SIGMA — Reporte HF Diario</b>\n\n'
    msg += '<code>'
    for line in lines[3:]:  # skip header dividers
        msg += line + '\n'
        if len(msg) > 3500:
            break
    msg += '</code>'
    return msg


if __name__ == '__main__':
    import os

    report = build_report()
    print(report)

    if '--telegram' in sys.argv or os.getenv('SEND_TELEGRAM', '0') == '1':
        import urllib.request, urllib.parse
        sys.path.insert(0, '/opt/sigma')
        try:
            from utils.secrets import get_tg_token
            token = get_tg_token()
        except Exception:
            token = None

        if token:
            chat_id = '-1003787411069'
            msg = telegram_format(report)
            params = urllib.parse.urlencode({
                'chat_id': chat_id,
                'text': msg,
                'parse_mode': 'HTML',
            }).encode()
            req = urllib.request.Request(
                f'https://api.telegram.org/bot{token}/sendMessage',
                data=params,
            )
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                res  = json.loads(resp.read())
                if res.get('ok'):
                    print('[TG] Reporte enviado', flush=True)
                else:
                    print('[TG] Error:', res.get('description'), flush=True)
            except Exception as e:
                print('[TG] Exception:', e, flush=True)
        else:
            print('[TG] No token disponible', flush=True)

