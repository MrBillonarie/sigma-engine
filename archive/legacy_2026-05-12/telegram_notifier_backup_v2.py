#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGMA ENGINE — Notifier Profesional v2.0"""
import urllib.request, json, time, os, sys
from datetime import datetime, timezone, timedelta

TOKEN    = "8648450580:AAHpL8Sbhjo-u5RQ2If2tU_gXriT2-K1o8o"
CHAT_ID  = "-1003787411069"
VPS_URL  = "http://127.0.0.1:8080"
INTERVAL = int(os.getenv("SIGMA_INTERVAL", "20"))

# Chile Standard Time = UTC-3
CHILE = timezone(timedelta(hours=-3))

# ── Helpers ───────────────────────────────────────────────────────────────────
def send(msg, silent=False):
    try:
        url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": CHAT_ID, "text": msg,
            "parse_mode": "HTML",
            "disable_notification": silent,
            "link_preview_options": {"is_disabled": True}
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data,
              headers={"Content-Type": "application/json; charset=utf-8"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[TG] {e}", flush=True)

def pin(msg_id):
    try:
        url  = f"https://api.telegram.org/bot{TOKEN}/pinChatMessage"
        data = json.dumps({"chat_id": CHAT_ID, "message_id": msg_id,
                            "disable_notification": True}).encode()
        req  = urllib.request.Request(url, data=data,
               headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except: pass

def send_pin(msg):
    """Envia y pinea el mensaje."""
    try:
        url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": CHAT_ID, "text": msg,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True}
        }, ensure_ascii=False).encode("utf-8")
        req  = urllib.request.Request(url, data=data,
               headers={"Content-Type": "application/json; charset=utf-8"})
        r    = urllib.request.urlopen(req, timeout=10)
        mid  = json.loads(r.read()).get("result", {}).get("message_id")
        if mid: pin(mid)
    except Exception as e:
        print(f"[TG PIN] {e}", flush=True)

def fetch(path):
    try:
        r = urllib.request.urlopen(VPS_URL + path, timeout=8)
        return json.loads(r.read())
    except:
        return None

def fmt(p):
    if not p: return "—"
    return f"{p:,.2f}" if p > 100 else f"{p:.4f}"

def bar(pct, width=10):
    """Barra de progreso visual."""
    filled = int(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)

def pnl_icon(v):
    return "📈" if v >= 0 else "📉"

def medal(wr):
    if wr >= 70: return "🥇"
    if wr >= 60: return "🥈"
    if wr >= 50: return "🥉"
    return "⚠️"

def chile_now():
    return datetime.now(CHILE)

# ── Briefing matutino (08:00 Chile) ───────────────────────────────────────────
def morning_briefing(signals, trades):
    now    = chile_now()
    port   = trades.get("portfolio", {}) if trades else {}
    st     = trades.get("stats", {}) if trades else {}
    lr     = trades.get("live_readiness", {}) or {}
    regime = signals.get("regime", "?") if signals else "?"
    models = signals.get("models", []) if signals else []
    cb     = signals.get("circuit_breaker", False) if signals else False

    eq     = port.get("equity", 10000)
    ret    = port.get("return_pct", 0)
    wr     = st.get("win_rate", 0)
    total  = st.get("total", 0)
    score  = lr.get("score", 0)

    active = [m for m in models if m.get("signal") and m.get("slot", 0) > 0]

    regime_desc = {
        "BULL":  "Tendencia alcista — condiciones favorables para LONG",
        "BEAR":  "Tendencia bajista — condiciones favorables para SHORT",
        "RANGE": "Mercado lateral — mayor selectividad en entradas",
        "LOADING": "Cargando datos — analisis en curso",
    }.get(regime, regime)

    reg_icon = {"BULL": "📈", "BEAR": "📉", "RANGE": "↔️"}.get(regime, "🔄")

    cb_line = "\n⛔ <b>CIRCUIT BREAKER ACTIVO</b> — sin nuevas operaciones" if cb else ""

    signal_block = ""
    if active:
        lines = []
        for m in active:
            isL   = m.get("type") != "short"
            arrow = "▲ LONG" if isL else "▼ SHORT"
            lines.append(
                f"  • SLOT {m.get('slot')} | {arrow} {m.get('sym')} {m.get('tf','').upper()}\n"
                f"    Entrada {fmt(m.get('price'))} | SL {fmt(m.get('sl'))} | TP {fmt(m.get('tp'))}\n"
                f"    WR {m.get('wr',0):.0f}% | Grade {m.get('grade','?')}"
            )
        signal_block = f"\n\n<b>Señales activas:</b>\n" + "\n\n".join(lines)
    else:
        signal_block = "\n\nSin señales activas al momento."

    msg = (
        f"☀️ <b>Briefing Matutino — {now.strftime('%d %b %Y')}</b>\n\n"
        f"{reg_icon} Regimen: <b>{regime}</b>\n"
        f"{regime_desc}{cb_line}\n\n"
        f"<b>Estado del Portfolio</b>\n"
        f"Equity:    <code>${eq:,.2f}</code>  {pnl_icon(ret)} <code>{ret:+.2f}%</code>\n"
        f"Win Rate:  {medal(wr)} <code>{wr:.0f}%</code>  ({total} trades)\n"
        f"Gate live: <code>{bar(score)}</code> {score}/100"
        f"{signal_block}"
    )
    send_pin(msg)
    print(f"[{now.strftime('%H:%M')}] Briefing matutino enviado", flush=True)

# ── Resumen diario (20:00 Chile) ──────────────────────────────────────────────
def evening_summary(signals, trades):
    now  = chile_now()
    port = trades.get("portfolio", {}) if trades else {}
    st   = trades.get("stats", {}) if trades else {}
    hist = trades.get("history", []) if trades else []
    lr   = trades.get("live_readiness", {}) or {}

    eq    = port.get("equity", 10000)
    ret   = port.get("return_pct", 0)
    maxdd = port.get("max_dd", 0)
    wr    = st.get("win_rate", 0)
    wins  = st.get("wins", 0)
    losses= st.get("losses", 0)
    total = st.get("total", 0)
    avg_w = st.get("avg_win", 0)
    avg_l = st.get("avg_loss", 0)
    pf    = st.get("profit_factor", 0)
    score = lr.get("score", 0)

    # trades de hoy (por fecha en closed_at)
    today_str = now.strftime("%Y-%m-%d")
    today_trades = [t for t in hist if t.get("closed_at", "").startswith(today_str)]

    today_block = ""
    if today_trades:
        lines = []
        day_pnl = 0
        reason_map = {"TP_HIT": "TP", "SL_HIT": "SL",
                       "REGIME_CHANGE": "Regimen", "MANUAL": "Manual"}
        for t in today_trades:
            pnl  = t.get("pnl_pct", 0)
            day_pnl += pnl
            icon = "✅" if pnl >= 0 else "❌"
            rsn  = reason_map.get(t.get("reason", ""), "?")
            lines.append(f"  {icon} {t.get('sym')} {t.get('tf','').upper()}  <code>{pnl:+.2f}%</code>  [{rsn}]")
        dpnl_icon = "📈" if day_pnl >= 0 else "📉"
        today_block = (
            f"\n\n<b>Trades de hoy ({len(today_trades)}):</b>\n"
            + "\n".join(lines)
            + f"\n{dpnl_icon} P&amp;L del dia: <code>{day_pnl:+.2f}%</code>"
        )
    else:
        today_block = "\n\nSin trades cerrados hoy."

    # mejor y peor trade historico
    best  = max(hist, key=lambda t: t.get("pnl_pct", 0), default=None) if hist else None
    worst = min(hist, key=lambda t: t.get("pnl_pct", 0), default=None) if hist else None
    records = ""
    if best:
        records += f"\n🏆 Mejor trade: {best.get('sym')} {best.get('tf','').upper()} <code>{best.get('pnl_pct',0):+.2f}%</code>"
    if worst:
        records += f"\n💀 Peor trade:  {worst.get('sym')} {worst.get('tf','').upper()} <code>{worst.get('pnl_pct',0):+.2f}%</code>"

    pf_str = f"{pf:.2f}" if pf else "—"

    msg = (
        f"🌙 <b>Resumen Diario — {now.strftime('%d %b %Y')}</b>\n\n"
        f"<b>Portfolio</b>\n"
        f"Equity:         <code>${eq:,.2f}</code>  {pnl_icon(ret)} <code>{ret:+.2f}%</code>\n"
        f"Win Rate:       {medal(wr)} <code>{wr:.0f}%</code>  ({wins}W / {losses}L / {total} trades)\n"
        f"Avg Win/Loss:   <code>+{avg_w:.2f}%</code> / <code>{avg_l:.2f}%</code>\n"
        f"Profit Factor:  <code>{pf_str}</code>\n"
        f"Max Drawdown:   <code>{maxdd:.1f}%</code>\n"
        f"Gate capital:   <code>{bar(score)}</code> {score}/100"
        f"{today_block}"
        f"{records}"
    )
    send(msg)
    print(f"[{now.strftime('%H:%M')}] Resumen diario enviado", flush=True)

# ── Reporte semanal (lunes 09:00) ─────────────────────────────────────────────
def weekly_report(signals, trades):
    now  = chile_now()
    port = trades.get("portfolio", {}) if trades else {}
    st   = trades.get("stats", {}) if trades else {}
    hist = trades.get("history", []) if trades else []
    lr   = trades.get("live_readiness", {}) or {}

    eq    = port.get("equity", 10000)
    ret   = port.get("return_pct", 0)
    maxdd = port.get("max_dd", 0)
    wr    = st.get("win_rate", 0)
    wins  = st.get("wins", 0)
    losses= st.get("losses", 0)
    total = st.get("total", 0)
    avg_w = st.get("avg_win", 0)
    avg_l = st.get("avg_loss", 0)
    pf    = st.get("profit_factor", 0)
    score = lr.get("score", 0)
    cagr  = port.get("cagr_live")

    # Semana pasada (lun-dom)
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    week_trades = [t for t in hist if t.get("closed_at", "") >= week_ago]
    wt_pnl  = sum(t.get("pnl_pct", 0) for t in week_trades)
    wt_wins = sum(1 for t in week_trades if t.get("pnl_pct", 0) >= 0)
    wt_wr   = (wt_wins / len(week_trades) * 100) if week_trades else 0

    # Grafico de barras semanal
    chart_lines = []
    for t in week_trades[-7:]:
        pnl   = t.get("pnl_pct", 0)
        icon  = "🟢" if pnl >= 0 else "🔴"
        blen  = min(int(abs(pnl) * 2), 10)
        b     = "█" * blen
        chart_lines.append(f"  {icon} {t.get('sym')} {t.get('tf','').upper():<6} {b} <code>{pnl:+.2f}%</code>")

    chart = "\n".join(chart_lines) if chart_lines else "  Sin trades esta semana."

    cagr_line = f"\nCAGR live: <code>{cagr:+.1f}%</code>" if cagr else ""
    pf_str    = f"{pf:.2f}" if pf else "—"

    msg = (
        f"📅 <b>Reporte Semanal — Semana del {(now - timedelta(days=7)).strftime('%d %b')}</b>\n\n"
        f"<b>Esta semana</b>\n"
        f"Trades: {len(week_trades)} | P&amp;L: <code>{wt_pnl:+.2f}%</code> | WR: <code>{wt_wr:.0f}%</code>\n\n"
        f"<b>Rendimiento semana:</b>\n{chart}\n\n"
        f"<b>Portfolio acumulado</b>\n"
        f"Equity:        <code>${eq:,.2f}</code>  {pnl_icon(ret)} <code>{ret:+.2f}%</code>{cagr_line}\n"
        f"Win Rate:      {medal(wr)} <code>{wr:.0f}%</code>  ({wins}W / {losses}L / {total} trades)\n"
        f"Avg Win/Loss:  <code>+{avg_w:.2f}%</code> / <code>{avg_l:.2f}%</code>\n"
        f"Profit Factor: <code>{pf_str}</code>\n"
        f"Max Drawdown:  <code>{maxdd:.1f}%</code>\n"
        f"Gate capital:  <code>{bar(score)}</code> {score}/100\n\n"
        f"<i>El sistema sigue aprendiendo. A por la proxima semana 💪</i>"
    )
    send(msg)
    print(f"[{now.strftime('%H:%M')}] Reporte semanal enviado", flush=True)

# ── Ping silencioso 6h ────────────────────────────────────────────────────────
def silent_ping(signals, trades):
    now  = chile_now()
    port = trades.get("portfolio", {}) if trades else {}
    st   = trades.get("stats", {}) if trades else {}
    lr   = trades.get("live_readiness", {}) or {}
    eq   = port.get("equity", 10000)
    ret  = port.get("return_pct", 0)
    wr   = st.get("win_rate", 0)
    score= lr.get("score", 0)
    regime = signals.get("regime", "?") if signals else "?"

    send(
        f"📊 <b>Update</b> — {now.strftime('%d %b %H:%M')}\n"
        f"Equity: <code>${eq:,.2f}</code>  {pnl_icon(ret)} <code>{ret:+.2f}%</code>\n"
        f"WR: <code>{wr:.0f}%</code> | Regimen: {regime} | Gate: {score}/100",
        silent=True
    )

# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("SIGMA Notifier Pro v2.0 — iniciando", flush=True)
    send(
        "🤖 <b>SIGMA Notifier Pro v2.0 — Online</b>\n"
        "Briefing: 08:00 | Resumen: 20:00 | Reporte: Lunes 09:00\n"
        f"<i>{chile_now().strftime('%d/%m/%Y %H:%M')} (Chile)</i>",
        silent=True
    )

    seen_signals   = set()
    seen_trades    = set()
    last_trade_notify = 0   # timestamp del ultimo aviso de trade
    prev_regime    = None
    cb_notified    = False
    prev_readiness = 0
    last_6h        = 0
    last_morning   = None   # fecha en que se envio el briefing
    last_evening   = None   # fecha en que se envio el resumen
    last_weekly    = None   # fecha en que se envio el reporte semanal

    while True:
        now     = chile_now()
        signals = fetch("/api/signals")
        trades  = fetch("/api/trades")
        hour    = now.hour
        minute  = now.minute
        weekday = now.weekday()   # 0=lunes
        today   = now.date()

        # ── Briefing matutino ────────────────────────────────────────────────
        if hour == 8 and minute < 1 and last_morning != today:
            morning_briefing(signals, trades)
            last_morning = today

        # ── Resumen diario ───────────────────────────────────────────────────
        if hour == 20 and minute < 1 and last_evening != today:
            evening_summary(signals, trades)
            last_evening = today

        # ── Reporte semanal (lunes 09:00) ────────────────────────────────────
        if weekday == 0 and hour == 9 and minute < 1 and last_weekly != today:
            weekly_report(signals, trades)
            last_weekly = today

        # ── Ping silencioso cada 6h ──────────────────────────────────────────
        now_ts = time.time()
        if now_ts - last_6h > 43200 and trades:
            silent_ping(signals, trades)
            last_6h = now_ts

        # ── Alertas en tiempo real ───────────────────────────────────────────
        if signals:
            regime = signals.get("regime", "?")
            models = signals.get("models", [])
            cb     = signals.get("circuit_breaker", False)

            # Circuit breaker
            if cb and not cb_notified:
                send("⛔ <b>CIRCUIT BREAKER ACTIVADO</b>\n"
                     "Perdida >8% en 5 dias — sin nuevas operaciones 48h.\n"
                     "El sistema protege el capital automaticamente.")
                cb_notified = True
            elif not cb:
                cb_notified = False

            # Cambio de regimen
            if prev_regime and regime != prev_regime and regime not in ("LOADING", "?"):
                icons = {"BULL": "📈", "BEAR": "📉", "RANGE": "↔️"}
                icon  = icons.get(regime, "🔄")
                desc  = {
                    "BULL":  "Mercado en tendencia alcista. El sistema priorizara señales LONG.",
                    "BEAR":  "Mercado en tendencia bajista. El sistema priorizara señales SHORT.",
                    "RANGE": "Mercado lateral. Mayor selectividad, menos operaciones.",
                }.get(regime, "")
                send(f"{icon} <b>Cambio de Regimen</b>\n"
                     f"{prev_regime} → <b>{regime}</b>\n{desc}",
                     silent=True)
            if regime not in ("LOADING", "?"): prev_regime = regime

            # Contar trades abiertos ahora mismo
            open_now = []
            if trades:
                open_now = trades.get("open_trades", trades.get("open", []))
            MAX_SLOTS = 2
            slots_llenos = len(open_now) >= MAX_SLOTS

            # Señales reales (slot > 0 = el sistema las esta operando)
            curr_active = {}
            # Señales informativas (signal=True, grade bueno, pero slots llenos)
            curr_info = {}

            for m in models:
                if not m.get("signal"):
                    continue
                grade = m.get("grade", "D")
                key   = f"{m.get('sym')}_{m.get('tf')}"
                if m.get("slot", 0) > 0:
                    curr_active[key] = m
                elif grade in ("A+", "A") and slots_llenos:
                    curr_info[key] = m

            # ── Alertas de señales reales ────────────────────────────────────
            for key in set(curr_active) - seen_signals:
                m    = curr_active[key]
                isL  = m.get("type") != "short"
                arrow = "▲ LONG" if isL else "▼ SHORT"
                rr   = ""
                ev   = m.get("ev")
                if m.get("sl") and m.get("tp") and m.get("price"):
                    rn  = abs(m["tp"] - m["price"]) / max(abs(m["price"] - m["sl"]), 1e-6)
                    rr  = f"\nRatio RR: <code>{rn:.1f}:1</code>"
                ev_s  = f"\nValor Esperado: <code>{ev:+.1f}%</code>" if ev is not None else ""
                ens   = m.get("ensemble_count", 1)
                ens_s = f"\nEnsemble: {ens} modelos votaron esta señal" if ens > 1 else ""
                htf   = "\n⚠️ HTF no confirma — señal de menor confianza" if m.get("htf_penalty") else ""
                dd_k  = m.get("dd_kelly_mult", 1)
                dd_s  = f"\n📉 Kelly reducido x{dd_k:.2f} por drawdown" if dd_k < 1 else ""
                send(
                    f"⚡ <b>NUEVA SEÑAL — SLOT {m.get('slot')}</b>\n\n"
                    f"{arrow} <b>{m.get('sym')} {m.get('tf','').upper()}</b>\n\n"
                    f"Estrategia: <code>{m.get('strategy','?')}</code>\n"
                    f"Grade: <b>{m.get('grade','?')}</b> | WR historico: <code>{m.get('wr',0):.0f}%</code>\n\n"
                    f"Entrada: <code>{fmt(m.get('price'))}</code>\n"
                    f"Stop Loss: <code>{fmt(m.get('sl'))}</code>\n"
                    f"Take Profit: <code>{fmt(m.get('tp'))}</code>{rr}\n"
                    f"Kelly: <code>{m.get('eff_risk_pct','?')}%</code> del capital{ev_s}{ens_s}{htf}{dd_s}\n\n"
                    f"Regimen: <b>{regime}</b> | {now.strftime('%H:%M')} (Chile)"
                )

            # Alertas informativas (slots llenos)
            for key in set(curr_info) - seen_signals - set(curr_active):
                m = curr_info[key]
                arrow = "LONG" if m.get("type") != "short" else "SHORT"
                rr_str = ""
                if m.get("sl") and m.get("tp") and m.get("price"):
                    rn = abs(m["tp"] - m["price"]) / max(abs(m["price"] - m["sl"]), 1e-6)
                    rr_str = "  RR " + str(round(rn, 1)) + ":1"
                slots_txt = ", ".join(
                    op.get("sym","") + " " + op.get("tf","").upper()
                    for op in open_now
                ) if open_now else "slots llenos"
                note = (
                    chr(128065) + " SENAL DETECTADA" + chr(10)
                    + arrow + " " + m.get("sym","") + " " + m.get("tf","").upper() + chr(10)
                    + "Grade: " + m.get("grade","?") + "  WR: " + str(int(m.get("wr",0))) + "%" + chr(10)
                    + "Entrada: " + fmt(m.get("price")) + "  SL: " + fmt(m.get("sl")) + "  TP: " + fmt(m.get("tp")) + rr_str + chr(10)
                    + "[" + slots_txt + "]"
                )
                send(note, silent=True)
            seen_signals = set(curr_active) | set(curr_info)

        # ── Trades cerrados ──────────────────────────────────────────────────
        if trades:
            hist = trades.get("history", [])
            lr   = trades.get("live_readiness", {}) or {}
            port = trades.get("portfolio", {})

            nuevos = []
            for t in hist[-5:]:
                key = f"{t.get('sym')}_{t.get('tf')}_{t.get('closed_at','')}"
                if key not in seen_trades:
                    seen_trades.add(key)
                    nuevos.append(t)
            if nuevos and (now_ts - last_trade_notify) >= 3600:
                last_trade_notify = now_ts
                t = nuevos[-1]  # usar ultimo trade para datos
                if True:
                    pnl    = t.get('pnl_pct', 0)
                    reason = t.get('reason', '')
                    eq     = t.get('equity_after') or port.get('equity', 0)
                    kelly  = t.get('kelly_pct', 0)
                    if reason == 'TP_HIT':
                        send(
                            f"✅ <b>TAKE PROFIT ALCANZADO</b>\n\n"
                            f"<b>{t.get('sym')} {t.get('tf','').upper()}</b>\n\n"
                            f"P&amp;L: <code>{pnl:+.2f}%</code>  Kelly {kelly:.1f}%\n"
                            f"Entrada: <code>{fmt(t.get('entry'))}</code> → "
                            f"Salida: <code>{fmt(t.get('exit_price'))}</code>\n"
                            f"Equity: <code>${eq:,.2f}</code>"
                        )
                    else:
                        rsn_txt = {'SL_HIT':'Stop Loss','REGIME_CHANGE':'Regimen','MANUAL':'Manual'}.get(reason, reason)
                        send(
                            f"🔕 {t.get('sym')} {t.get('tf','').upper()}  "
                            f"<code>{pnl:+.2f}%</code>  [{rsn_txt}]  "
                            f"Equity <code>${eq:,.2f}</code>",
                            silent=True
                        )

                # Gate capital real: hitos
            score = lr.get("score", 0)
            if score > 0 and score != prev_readiness:
                if score >= 85 and prev_readiness < 85:
                    send_pin(
                        f"🚀 <b>HITO: Sistema listo para capital real</b>\n\n"
                        f"Gate: <code>{bar(score)}</code> {score}/100\n"
                        f"Todos los checks de validacion superados.\n\n"
                        f"El sistema puede conectarse a la API de Binance "
                        f"para operar con capital real.\n\n"
                        f"<b>Este es el momento que estuvimos construyendo.</b> 🎯"
                    )
                elif score > prev_readiness and score % 10 == 0:
                    send(
                        f"📊 Gate capital real: <code>{bar(score)}</code> {score}/100\n"
                        f"{lr.get('trades_done',0)} trades cerrados | "
                        f"{now.strftime('%H:%M')}",
                        silent=True
                    )
                prev_readiness = score

        time.sleep(INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        send("🔴 <b>SIGMA Notifier detenido.</b>", silent=True)
        print("\nDetenido.")
