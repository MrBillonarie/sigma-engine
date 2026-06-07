#!/usr/bin/env python3
"""SIGMA - cierre diario con NOTICIAS CRYPTO + balance del dia (21:00 Chile).

GUARDRAILS:
- Solo lectura. No toca motor/SL/TP/kelly.
- --dry-run: imprime sin enviar.
- Sin --dry-run: envia y registra en tg_news.log.
- Fuentes RSS (stdlib): CoinDesk, Cointelegraph, Decrypt.
- Si TODOS los feeds fallan -> mensaje sin noticias.
- Nunca link al VPS. Titulares sin links clickeables.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
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
PIPELINE_EVENTS = Path("/opt/sigma/results/reports/pipeline_events.jsonl")

REGIME_URL = "http://localhost:8080/api/regime"

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
    "etf", "sec", "rate", "rates", "powell",
]

UA = "Mozilla/5.0 (SIGMA-NewsBot/1.0)"


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


def fecha_corta(now_cl: datetime) -> str:
    return f"{now_cl.day} {MESES_ES[now_cl.month - 1]}"


def parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_CL)
        return dt
    except Exception:
        return None


def fetch_feed(name: str, url: str, timeout: int = 5):
    out = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        root = ET.fromstring(raw)
    except Exception as e:
        return [], f"{name}: {type(e).__name__}: {e}"

    items = root.findall(".//item")
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
        title = re.sub(r"<[^>]+>", "", title).strip()
        if title and pubdate:
            out.append({"title": title, "pubdate": pubdate, "source": name})
    return out, None


def collect_headlines(window_start_utc, window_end_utc, max_items=3, debug=None):
    all_items = []
    if debug is None:
        debug = {}
    for name, url in FEEDS:
        items, err = fetch_feed(name, url)
        debug[name] = {"count": len(items), "error": err}
        all_items.extend(items)
    in_window = [h for h in all_items if window_start_utc <= h["pubdate"] <= window_end_utc]
    if not in_window:
        in_window = all_items
    in_window.sort(key=lambda h: h["pubdate"], reverse=True)

    relevant = []
    for h in in_window:
        t = h["title"].lower()
        if any(kw in t for kw in RELEVANCE_KEYWORDS):
            relevant.append(h)
        if len(relevant) >= max_items:
            break
    if len(relevant) < max_items:
        seen = {r["title"] for r in relevant}
        for h in in_window:
            if h["title"] in seen:
                continue
            relevant.append(h)
            if len(relevant) >= max_items:
                break
    return relevant[:max_items], debug


def format_headline(h):
    t = h["title"]
    if len(t) > 100:
        t = t[:99].rstrip() + "..."
    return f"- {t} - <i>{h['source']}</i>"


def trades_del_dia(history, start_cl, end_cl):
    out = []
    for h in history:
        ct = parse_dt(h.get("closed_at"))
        if ct and start_cl <= ct <= end_cl:
            out.append(h)
    return out


def champions_del_dia(start_cl, end_cl):
    if not TG_CHAMPION_LOG.exists():
        return []
    out = []
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
        if start_cl <= ts <= end_cl:
            meta = {p.split("=")[0]: p.split("=", 1)[1] for p in parts if "=" in p}
            out.append(meta)
    return out


def pipeline_stats_del_dia(start_cl, end_cl):
    if not PIPELINE_EVENTS.exists():
        return {}
    try:
        mtime = datetime.fromtimestamp(PIPELINE_EVENTS.stat().st_mtime, tz=TZ_CL)
    except Exception:
        return {}
    if not (start_cl <= mtime <= end_cl + timedelta(hours=1)):
        return {"stale": True}
    counts = {"OOS_NEG": 0, "OVERFIT": 0, "SIN_TRADES": 0, "SIN_EDGE_IS": 0,
              "POSITIVO_NO_MEJOR": 0, "NUEVO_RECORD": 0, "PROMOVIDO": 0}
    total = 0
    try:
        for line in PIPELINE_EVENTS.read_text().splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            r = e.get("result", "")
            if r in counts:
                counts[r] += 1
            total += 1
    except Exception:
        return {}
    counts["TOTAL"] = total
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now_utc = datetime.now(timezone.utc)
    now_cl = now_utc.astimezone(TZ_CL)
    start_cl = now_cl.replace(hour=0, minute=0, second=0, microsecond=0)
    fecha = fecha_corta(now_cl)

    # Noticias del dia (00:00-21:00 Chile)
    window_start_utc = start_cl.astimezone(timezone.utc)
    window_end_utc = now_utc
    headlines, feed_debug = collect_headlines(window_start_utc, window_end_utc, max_items=3)

    # Trades cerrados hoy
    ts = safe_json(TRADE_STATE) or {}
    history = ts.get("history", []) or []
    open_trades = ts.get("open", {}) or {}
    if isinstance(open_trades, dict):
        open_list = list(open_trades.values())
    else:
        open_list = list(open_trades)
    closed_today = trades_del_dia(history, start_cl, now_cl)

    # Pipeline stats
    stats = pipeline_stats_del_dia(start_cl, now_cl)
    promoted = champions_del_dia(start_cl, now_cl)

    silent_day = (len(closed_today) == 0 and len(promoted) == 0)

    partes = [f"<b>SIGMA - Cierre {fecha}</b>", ""]
    partes.append("<b>El dia en cifras</b>")

    if silent_day:
        if stats and not stats.get("stale"):
            k_trials = stats.get("TOTAL", 0)
            partes.append(f"Sistema en observacion - sin trades ejecutados, trainer corriendo en background ({k_trials} trials).")
        else:
            partes.append("Sistema en observacion - sin trades ejecutados, trainer en background.")
    else:
        if closed_today:
            pnl_sum = sum((t.get("pnl_pct") or 0) for t in closed_today)
            n_win = sum(1 for t in closed_today if (t.get("pnl_pct") or 0) > 0)
            n_loss = len(closed_today) - n_win
            best = max(closed_today, key=lambda t: t.get("pnl_pct") or -999)
            worst = min(closed_today, key=lambda t: t.get("pnl_pct") or 999)
            partes.append(f"Trades cerrados: {len(closed_today)} ({n_win}W / {n_loss}L) | P&L: {pnl_sum:+.2f}%")
            partes.append(f"Mejor: {best.get('sym','?')} {best.get('tf','?')} {best.get('pnl_pct',0):+.2f}%")
            if n_loss > 0 and worst is not best:
                partes.append(f"Peor:  {worst.get('sym','?')} {worst.get('tf','?')} {worst.get('pnl_pct',0):+.2f}%")
        else:
            partes.append("Trades cerrados: 0")
        partes.append(f"Champions promovidos: {len(promoted)}")
        if stats and not stats.get("stale"):
            total = stats.get("TOTAL", 0)
            overfit = stats.get("OVERFIT", 0)
            nuevo = stats.get("NUEVO_RECORD", 0) + stats.get("PROMOVIDO", 0)
            partes.append(f"Trainer: {total} trials evaluados, {overfit} overfits, {nuevo} promovidos")

    partes.append("")

    # Champions del dia
    if promoted:
        partes.append("<b>Champions de hoy</b>")
        for p in promoted[:3]:
            sym = p.get("symbol", "?").replace("/USDT", "")
            tf = p.get("tf", "?")
            strat = p.get("strategy", "?")
            cagr = p.get("cagr", "?")
            partes.append(f"- {sym} {tf} {strat} (CAGR {cagr}%)")
        partes.append("")

    # Noticias del dia
    if headlines:
        partes.append("<b>Noticias que marcaron el dia</b>")
        for h in headlines:
            partes.append(format_headline(h))
        partes.append("")
    else:
        partes.append("<b>Noticias:</b> feeds no disponibles este ciclo.")
        partes.append("")

    # Overnight
    partes.append("<b>Overnight</b>")
    if open_list:
        sub = []
        for t in open_list[:5]:
            sym = t.get("sym", "?")
            tf = t.get("tf", "?")
            d = (t.get("direction") or "?").upper()
            sub.append(f"{sym} {tf} {d}")
        partes.append(f"Trades abiertos: {len(open_list)} ({', '.join(sub)})")
    else:
        partes.append("Trades abiertos: ninguno")

    regime = safe_http(REGIME_URL) or {}
    n_bear = sum(1 for v in regime.values() if isinstance(v, dict) and v.get("regime") == "BEAR")
    n_range = sum(1 for v in regime.values() if isinstance(v, dict) and v.get("regime") == "RANGE")
    n_bull = sum(1 for v in regime.values() if isinstance(v, dict) and v.get("regime") == "BULL")
    partes.append(f"Regimen actual: {n_bear} BEAR - {n_range} RANGE - {n_bull} BULL")
    partes.append("")
    partes.append("- SIGMA 24/7")

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
            f.write(f"{now_utc.isoformat()} evening {status} headlines={len(headlines)} feeds=[{feed_counts}] closed={len(closed_today)} promoted={len(promoted)} open={len(open_list)}\n")
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
