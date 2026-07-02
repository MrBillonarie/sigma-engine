#!/usr/bin/env python3
"""SIGMA - mensaje matinal con NOTICIAS CRYPTO + estado del sistema (09:00 Chile).

GUARDRAILS:
- Solo lectura. No toca motor/SL/TP/kelly.
- --dry-run: imprime sin enviar.
- Sin --dry-run: envia al grupo y registra en tg_news.log.
- Fuentes RSS: CoinDesk, Cointelegraph, Decrypt (stdlib unicamente).
- Si TODOS los feeds fallan -> mensaje sin seccion noticias.
- Nunca link al VPS. Titulares sin links clickeables.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/sigma")
try:
    from utils.secrets import get_tg_token
except Exception:
    get_tg_token = None

CHAT_ID = "-1003787411069"
TZ_CL = ZoneInfo("America/Santiago")
LOG_PATH = Path("/opt/sigma/results/reports/tg_news.log")

TRADE_STATE = Path("/opt/sigma/results/trade_state.json")
TG_CHAMPION_LOG = Path("/opt/sigma/results/reports/tg_champion_sent.log")

REGIME_URL = "http://localhost:8080/api/regime"
SIGNALS_URL = "http://localhost:8080/api/signals"

DIAS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

FEEDS = [
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

RELEVANCE_KEYWORDS = [
    "btc", "bitcoin", "eth", "ethereum", "sol", "solana", "bnb", "binance",
    "ltc", "litecoin", "crypto", "fed", "fomc", "cpi", "inflation",
    "etf", "sec", "rate", "rates", "fed chair", "powell",
]

UA = "Mozilla/5.0 (SIGMA-NewsBot/1.0)"


def _build_performance_section():
    """Hedge-fund performance summary for morning news."""
    import json as _j2, os as _o2
    pf = "/opt/sigma/results/reports/performance_tracker.json"
    pr = "/opt/sigma/results/reports/portfolio_risk.json"
    rb = "/opt/sigma/results/reports/risk_budget.json"
    out = []
    try:
        if _o2.path.exists(pf):
            d  = _j2.load(open(pf))
            p  = d.get("portfolio", {})
            g  = d.get("gate_summary", {})
            out.append("\U0001f4ca *Live Performance*")
            out.append(f"  Equity: ${p.get('equity',10000):,.0f} ({p.get('return_pct',0):+.1f}%)")
            out.append(f"  WR live: {p.get('portfolio_wr',0) or 0:.0f}% -- {p.get('total_trades',0)} trades")
            out.append(f"  Superando BT: {g.get('beating_backtest',0)} | Gate: {p.get('total_trades',0)}/30")
    except Exception:
        pass
    try:
        if _o2.path.exists(pr):
            d  = _j2.load(open(pr))
            p  = d.get("portfolio", {})
            c  = d.get("concentration", {})
            sa = d.get("alpha_per_strategy", {})
            out.append("\U0001f3af *Riesgo*")
            out.append(f"  VaR 95%: {p.get('var_95_pct',0) or 0:.1f}% | MaxDD: {p.get('max_dd_pct',0) or 0:.1f}%")
            neff = c.get("n_effective_assets", 0) or 0
            flag = " concentrado" if not c.get("ok", True) else ""
            out.append(f"  Diversif: {neff:.1f} activos efectivos{flag}")
            pos_alpha = [(k,v) for k,v in sa.items() if v.get("positive_alpha") and v.get("n",0) >= 3]
            if pos_alpha:
                best = max(pos_alpha, key=lambda x: x[1]["alpha"])
                out.append(f"  Alpha lider: {best[0].split('/')[-1]} ({best[1]['alpha']:+.2f}%/trade)")
    except Exception:
        pass
    try:
        if _o2.path.exists(rb):
            d  = _j2.load(open(rb))
            vm = d.get("vol_metrics", {})
            kg = d.get("kelly_guidance", {})
            st = d.get("status", "?")
            out.append("\U0001f4b0 *Risk Budget*")
            out.append(f"  Vol anual: {vm.get('annual_vol_pct',0):.1f}% (target {vm.get('vol_target_pct',30)}%) {st}")
            out.append(f"  Kelly adj: {kg.get('vol_adjusted_suggestion',3.3):.1f}%")
    except Exception:
        pass
    return "\n".join(out) if out else ""


def safe_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def safe_http(url: str, timeout: int = 5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fecha_chile(now_cl: datetime) -> str:
    return f"{DIAS_ES[now_cl.weekday()]} {now_cl.day} de {MESES_ES[now_cl.month - 1]}"


def fetch_feed(name: str, url: str, timeout: int = 5):
    """Devuelve lista de dicts {title, pubdate (datetime tz-aware UTC), source}.
    Si falla, devuelve []."""
    out = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        root = ET.fromstring(raw)
    except Exception as e:
        return [], f"{name}: {type(e).__name__}: {e}"

    # RSS 2.0: channel/item/{title,pubDate}
    items = root.findall(".//item")
    # Atom fallback: feed/entry
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//a:entry", ns)
        for it in items:
            title_el = it.find("a:title", ns)
            pub_el = it.find("a:updated", ns) or it.find("a:published", ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            pubdate = None
            if pub_el is not None and pub_el.text:
                try:
                    pubdate = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00"))
                except Exception:
                    pubdate = None
            if title and pubdate:
                out.append({"title": title, "pubdate": pubdate, "source": name})
        return out, None

    for it in items:
        title_el = it.find("title")
        pub_el = it.find("pubDate")
        title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
        pubdate = None
        if pub_el is not None and pub_el.text:
            try:
                pubdate = parsedate_to_datetime(pub_el.text)
                if pubdate.tzinfo is None:
                    pubdate = pubdate.replace(tzinfo=timezone.utc)
            except Exception:
                pubdate = None
        # strip CDATA / HTML tags simples
        title = re.sub(r"<[^>]+>", "", title).strip()
        if title and pubdate:
            out.append({"title": title, "pubdate": pubdate, "source": name})
    return out, None


def collect_headlines(window_start_utc: datetime, window_end_utc: datetime,
                      max_items: int = 5, debug: dict = None):
    all_items = []
    if debug is None:
        debug = {}
    for name, url in FEEDS:
        items, err = fetch_feed(name, url)
        debug[name] = {"count": len(items), "error": err}
        all_items.extend(items)
    # Filtrar ventana
    in_window = [
        h for h in all_items
        if window_start_utc <= h["pubdate"] <= window_end_utc
    ]
    if not in_window:
        # fallback: cualquier item con pubdate
        in_window = all_items
    # Ordenar por pubdate desc
    in_window.sort(key=lambda h: h["pubdate"], reverse=True)

    # Filtro relevancia
    relevant = []
    for h in in_window:
        t = h["title"].lower()
        if any(kw in t for kw in RELEVANCE_KEYWORDS):
            relevant.append(h)
        if len(relevant) >= max_items:
            break

    if len(relevant) < 3:
        # tomar las mas recientes generales hasta completar
        seen_titles = {r["title"] for r in relevant}
        for h in in_window:
            if h["title"] in seen_titles:
                continue
            relevant.append(h)
            if len(relevant) >= max_items:
                break

    return relevant[:max_items], debug


def format_headline(h: dict) -> str:
    t = h["title"]
    if len(t) > 100:
        t = t[:99].rstrip() + "..."
    return f"- {t} - <i>{h['source']}</i>"


def champions_overnight(now_cl: datetime):
    if not TG_CHAMPION_LOG.exists():
        return []
    start = now_cl.replace(hour=21, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = now_cl.replace(hour=9, minute=0, second=0, microsecond=0)
    if end < start:
        end = now_cl
    promoted = []
    try:
        for line in TG_CHAMPION_LOG.read_text().splitlines():
            if "SENT" not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                ts = datetime.fromisoformat(parts[0])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=TZ_CL)
            except Exception:
                continue
            if start <= ts <= end:
                meta = {p.split("=")[0]: p.split("=", 1)[1] for p in parts if "=" in p}
                promoted.append(meta)
    except Exception:
        return []
    return promoted


def derivados_snapshot():
    """Snapshot compacto de F&G + LSR + OI desde DBs locales.
    Graceful: si una DB falla, esa linea muestra n/d.
    Returns: list[str] con las lineas. Lista vacia si todo falla."""
    import sqlite3 as _sq3
    DELTA = chr(916)  # Greek capital delta
    lineas = []
    # --- F&G ---
    try:
        conn = _sq3.connect('/opt/sigma/results/fng.db')
        c = conn.cursor()
        c.execute('SELECT ts, value, classification FROM fng ORDER BY ts DESC LIMIT 2')
        rows = c.fetchall()
        conn.close()
        if rows:
            cur = rows[0]
            cur_v = cur[1]; cur_cls = cur[2] or ''
            if len(rows) >= 2:
                prev_v = rows[1][1]
                delta = cur_v - prev_v
                sign = '+' if delta > 0 else ''
                lineas.append("F&G: " + str(cur_v) + " (" + cur_cls + ") - vs ayer " + DELTA + sign + str(delta))
            else:
                lineas.append("F&G: " + str(cur_v) + " (" + cur_cls + ") - vs ayer n/d")
        else:
            lineas.append("F&G: n/d")
    except Exception as e:
        lineas.append("F&G: n/d (" + type(e).__name__ + ")")

    # --- LSR top_acct BTC 1h + ETH 1h ---
    try:
        conn = _sq3.connect('/opt/sigma/results/lsr.db')
        c = conn.cursor()
        def _last_lsr(sym, tf):
            c.execute("SELECT ls_ratio FROM lsr WHERE symbol=? AND tf=? AND kind='top_acct' ORDER BY ts DESC LIMIT 1",
                      (sym, tf))
            r = c.fetchone()
            return r[0] if r else None
        btc_lsr = _last_lsr('BTCUSDT', '1h')
        eth_lsr = _last_lsr('ETHUSDT', '1h')
        conn.close()
        tag = ''
        if btc_lsr is not None and eth_lsr is not None:
            if (btc_lsr - 1.0) * (eth_lsr - 1.0) < 0:
                tag = ' (divergencia)'
        btc_s = ("%.2f" % btc_lsr) if btc_lsr is not None else 'n/d'
        eth_s = ("%.2f" % eth_lsr) if eth_lsr is not None else 'n/d'
        lineas.append("LSR top BTC 1h: " + btc_s + " - ETH 1h: " + eth_s + tag)
    except Exception as e:
        lineas.append("LSR: n/d (" + type(e).__name__ + ")")

    # --- OI BTC 1h ultimo + delta 24h ---
    try:
        conn = _sq3.connect('/opt/sigma/results/oi.db')
        c = conn.cursor()
        c.execute("SELECT ts, sum_open_interest_value FROM oi WHERE symbol='BTCUSDT' AND tf='1h' ORDER BY ts DESC LIMIT 25")
        rows = c.fetchall()
        conn.close()
        if rows:
            cur_val = rows[0][1]
            old_val = rows[24][1] if len(rows) >= 25 else (rows[-1][1] if len(rows) > 1 else None)
            cur_b = cur_val / 1e9
            if old_val and old_val > 0:
                pct = (cur_val - old_val) / old_val * 100.0
                sign = '+' if pct >= 0 else ''
                lineas.append("OI BTC 1h: $" + ("%.1f" % cur_b) + " B - " + DELTA + "24h " + sign + ("%.1f" % pct) + "%")
            else:
                lineas.append("OI BTC 1h: $" + ("%.1f" % cur_b) + " B - " + DELTA + "24h n/d")
        else:
            lineas.append("OI BTC 1h: n/d")
    except Exception as e:
        lineas.append("OI BTC 1h: n/d (" + type(e).__name__ + ")")

    return lineas




def _build_m2_section(models):
    """Seccion Motor 2 — top commodities para morning news."""
    M2_SYMS = {'XAU', 'XAG', 'WTI', 'HG', 'NG', 'PL'}
    M2_NAME = {'XAU': 'Oro', 'XAG': 'Plata', 'WTI': 'Petroleo', 'HG': 'Cobre', 'NG': 'Gas Nat.', 'PL': 'Platino'}
    m2 = [m for m in models if m.get('sym') in M2_SYMS]
    if not m2:
        return []
    activar = [m for m in m2 if m.get('recommendation') == 'ACTIVAR']
    condicional = [m for m in m2 if m.get('recommendation') == 'CONDICIONAL']
    lines = ["⚙ <b>Motor 2 — Commodities</b>"]
    show = activar[:3] if activar else condicional[:2]
    for m in sorted(show, key=lambda x: -x.get('cagr', 0)):
        sym = m.get('sym', '?')
        cagr = m.get('cagr', 0)
        grade = m.get('grade', '?')
        tf = m.get('tf', '?').upper()
        rec = m.get('recommendation', '?')
        label = M2_NAME.get(sym, sym)
        tag = 'ACTIVAR' if rec == 'ACTIVAR' else 'COND.'
        lines.append(f"  {label} ({sym}) {tf}: <b>{cagr:+.1f}%</b> [{grade}] {tag}")
    n_m2_act = len(activar)
    n_m2_tot = len(set(m.get('sym') for m in m2))
    lines.append(f"  {n_m2_act} ACTIVAR en {n_m2_tot} commodities activos")
    return lines



def _build_m3_section():
    """Seccion Motor 3 — top S&P 500 stocks champions para morning news."""
    M3_SYMS = ['AAPL', 'NVDA', 'TSLA', 'JPM', 'XOM']
    M3_NAME = {'AAPL': 'Apple', 'NVDA': 'Nvidia', 'TSLA': 'Tesla', 'JPM': 'JPMorgan', 'XOM': 'ExxonMobil'}
    M3_TFS  = ['1d', '4h', '1h', '15m']
    MODELS_DIR = Path('/opt/sigma/models')
    PFXMAP = {'AAPL':'aaplusd','NVDA':'nvdausd','TSLA':'tslausd','JPM':'jpmusd','XOM':'xomusd'}

    champions = []
    for sym in M3_SYMS:
        pfx = PFXMAP[sym]
        best = None
        best_cagr = -9999.0
        for tf in M3_TFS:
            tf_dir = MODELS_DIR / tf
            if not tf_dir.exists(): continue
            for p in tf_dir.glob(f'{pfx}_*.json'):
                try:
                    d = json.loads(p.read_text())
                    oos = d.get('metrics_oos') or {}
                    cagr = float(oos.get('cagr', 0) or 0)
                    wr   = float(oos.get('wr', 0) or 0)
                    if cagr > best_cagr:
                        best_cagr = cagr
                        strat = d.get('strategy') or p.stem[len(pfx)+1:]
                        best = {'sym': sym, 'tf': tf, 'cagr': cagr, 'wr': wr, 'strategy': strat}
                except Exception:
                    continue
        if best and best['cagr'] > 0:
            champions.append(best)

    if not champions:
        return []

    lines = ["📈 <b>Motor 3 — S&amp;P 500 Stocks</b>"]
    for ch in sorted(champions, key=lambda x: -x['cagr'])[:4]:
        label = M3_NAME.get(ch['sym'], ch['sym'])
        lines.append(f"  {label} ({ch['sym']}) {ch['tf'].upper()}: <b>{ch['cagr']:+.1f}%</b> WR {ch['wr']:.0f}% [{ch['strategy']}]")
    n_slots = len(champions)
    lines.append(f"  {n_slots}/5 tickers con champion | datos yfinance")
    return lines

def _build_dca_health_section():
    """Seccion diaria: paper trading vs BTC DCA + cold storage progress."""
    try:
        import json, urllib.request as _ur
        ts = json.load(open('/opt/sigma/results/trade_state.json'))
        bl = json.load(open('/opt/sigma/results/reports/btc_dca_baseline.json'))
        port     = ts.get('portfolio', {})
        equity   = float(port.get('equity', 10000))
        initial  = float(port.get('initial_capital', 10000))
        paper_ret = (equity - initial) / initial * 100
        start_btc = float(bl.get('start_btc_price', 82177.7))
        _resp    = json.loads(_ur.urlopen('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=4).read())
        btc_now  = float(_resp['price'])
        dca_ret  = (btc_now - start_btc) / start_btc * 100
        alpha    = paper_ret - dca_ret
        hist     = ts.get('history', [])
        n_trades = len(hist)
        wins     = sum(1 for x in hist if float(x.get('pnl_pct', 0)) > 0)
        wr_live  = wins / n_trades * 100 if n_trades > 0 else 0
        conf_str = ("n={} — sin poder estadistico aun".format(n_trades)
                    if n_trades < 30 else
                    "n={} — estadisticamente valido".format(n_trades))
        # Cold storage
        cs = json.load(open('/opt/sigma/results/reports/btc_cold_storage.json'))
        total_btc = float(cs.get('total_btc', 0))
        goal_btc  = float(cs.get('goal_btc', 1.0))
        pct_btc   = total_btc / goal_btc * 100 if goal_btc > 0 else 0
        alpha_sign = "+" if alpha >= 0 else ""
        pr_sign    = "+" if paper_ret >= 0 else ""
        dr_sign    = "+" if dca_ret >= 0 else ""
        lines = [
            "<b>Rendimiento vs BTC DCA</b>",
            "SIGMA paper:  <b>{}{:.1f}%</b>  (${:,.0f})".format(pr_sign, paper_ret, equity),
            "BTC DCA:      {}{:.1f}%  (${:,.0f}/BTC)".format(dr_sign, dca_ret, btc_now),
            "Alpha real:   <b>{}{:.1f}pp</b>".format(alpha_sign, alpha),
            "Confianza:    {}".format(conf_str),
            "WR live:      {:.1f}% ({} trades)".format(wr_live, n_trades),
            "",
            "<b>Mision BTC Cold Storage</b>",
            "Acumulado: {:.6f} / {:.2f} BTC ({:.1f}%)".format(total_btc, goal_btc, pct_btc),
        ]
        return lines
    except Exception as _e:
        return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now_utc = datetime.now(timezone.utc)
    now_cl = now_utc.astimezone(TZ_CL)
    fecha = fecha_chile(now_cl)

    # Noticias ultimas 24h
    window_start = now_utc - timedelta(hours=24)
    headlines, feed_debug = collect_headlines(window_start, now_utc, max_items=5)

    # Estado sistema
    ts = safe_json(TRADE_STATE) or {}
    open_trades = ts.get("open", {}) or {}
    if isinstance(open_trades, dict):
        open_list = list(open_trades.values())
    else:
        open_list = list(open_trades)
    n_long = sum(1 for t in open_list if (t.get("direction") or "").lower() == "long")
    n_short = sum(1 for t in open_list if (t.get("direction") or "").lower() == "short")
    n_open = len(open_list)

    signals = safe_http(SIGNALS_URL) or {}
    models = signals.get("models", []) if isinstance(signals, dict) else []
    activar = [m for m in models if m.get("recommendation") == "ACTIVAR"]
    esperar = [m for m in models if m.get("recommendation") == "ESPERAR"]
    na_long = sum(1 for m in activar if (m.get("type") or "").lower() == "long")
    na_short = sum(1 for m in activar if (m.get("type") or "").lower() == "short")

    promoted = champions_overnight(now_cl)

    # Regimen
    regime = safe_http(REGIME_URL) or {}
    n_bear = sum(1 for v in regime.values() if isinstance(v, dict) and v.get("regime") == "BEAR")
    n_range = sum(1 for v in regime.values() if isinstance(v, dict) and v.get("regime") == "RANGE")
    n_bull = sum(1 for v in regime.values() if isinstance(v, dict) and v.get("regime") == "BULL")
    btc_reg = regime.get("BTC", {}).get("regime", "n/d") if isinstance(regime, dict) else "n/d"

    highlight = ""
    if btc_reg == "RANGE" and n_bear >= 3:
        highlight = f"\nBTC en RANGE pero {n_bear} alts en BEAR - divergencia."
    elif btc_reg == "BULL" and n_bear >= 2:
        highlight = f"\nBTC en BULL, alts aun en BEAR - rotacion en curso."

    # ---- Construir mensaje ----
    partes = [f"<b>SIGMA - {fecha}</b>", ""]

    if headlines:
        partes.append("<b>Noticias crypto (24h)</b>")
        for h in headlines:
            partes.append(format_headline(h))
        partes.append("")
    else:
        partes.append("<b>Noticias crypto:</b> feeds no disponibles este ciclo.")
        partes.append("")

    partes.append("<b>Estado del sistema</b>")
    partes.append(f"Abiertos: {n_open} ({n_long}L / {n_short}S)")
    partes.append(f"Cola operable: {len(activar)} ACTIVAR ({na_long}L / {na_short}S)")
    partes.append(f"Banca (watchlist): {len(esperar)} modelos validados fuera de slot")
    if promoted:
        partes.append(f"Champions overnight: {len(promoted)}")
    else:
        partes.append("Champions overnight: sin novedades")
    partes.append("")

    partes.append("<b>Regimen actual</b>")
    partes.append(f"{n_bear} BEAR - {n_range} RANGE - {n_bull} BULL{highlight}")
    partes.append("")

    # --- Motor 2 commodities ---
    try:
        m2_lines = _build_m2_section(models)
    except Exception:
        m2_lines = []
    if m2_lines:
        for _l in m2_lines:
            partes.append(_l)
        partes.append("")

    # --- Motor 3 S&P 500 stocks ---
    try:
        m3_lines = _build_m3_section()
    except Exception:
        m3_lines = []
    if m3_lines:
        for _l in m3_lines:
            partes.append(_l)
        partes.append("")

    # --- Derivados snapshot (OI + F&G + LSR) ---
    try:
        deriv_lineas = derivados_snapshot()
    except Exception:
        deriv_lineas = []
    if deriv_lineas:
        partes.append("")
        partes.append("📡 <b>Derivados (snapshot)</b>")
        for _l in deriv_lineas:
            partes.append(_l)
        partes.append("")
        partes.append("- SIGMA 24/7")

    _pf_sec = _build_performance_section()
    # Health: paper vs DCA + cold storage
    _hlth = _build_dca_health_section()
    if _hlth:
        partes.append("")
        for _hl in _hlth:
            partes.append(_hl)
    if _pf_sec:
        partes.append("")
        partes.append(_pf_sec)
    msg = "\n".join(partes)

    if args.dry_run:
        print(msg)
        print()
        print("=== DEBUG feeds ===")
        for k, v in feed_debug.items():
            print(f"  {k}: count={v['count']} error={v['error']}")
        return 0

    if get_tg_token is None:
        print("ERROR: get_tg_token no disponible", file=sys.stderr)
        return 2
    try:
        token = get_tg_token()
    except Exception as e:
        print(f"ERROR token: {e}", file=sys.stderr)
        return 2

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    err = ""
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
    except Exception as e:
        ok = False
        err = str(e)

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            status = "SENT" if ok else f"FAILED({err})"
            feed_counts = ",".join(f"{k}={v['count']}" for k, v in feed_debug.items())
            f.write(f"{now_utc.isoformat()} morning {status} headlines={len(headlines)} feeds=[{feed_counts}] open={n_open} activar={len(activar)} promoted={len(promoted)}\n")
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
