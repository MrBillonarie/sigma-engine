#!/usr/bin/env python3
"""funding_emergency_dispatcher.py - lee marker file y manda Telegram una vez por evento.
Cron cada 5 min. Premortem 2026-05-14 GAP 2."""
import os, sys, time, re, urllib.parse, urllib.request, json
from pathlib import Path

FLAG = '/opt/sigma/state/funding_emergency.flag'
SENT = '/opt/sigma/state/funding_emergency.sent'
CONFIG = '/opt/sigma/config/settings.json'


def get_tg():
    try:
        with open(CONFIG) as f:
            c = json.load(f)
        return c.get('TELEGRAM_BOT_TOKEN'), c.get('TELEGRAM_CHAT_ID')
    except Exception:
        pass
    try:
        with open(CONFIG) as f:
            t = f.read()
        m_tok = re.search(r'TELEGRAM_BOT_TOKEN["\':\s=]+["\']([^"\']+)["\']', t)
        m_cid = re.search(r'CHAT_ID["\':\s=]+["\']?(-?\d+)["\']?', t)
        if m_tok and m_cid:
            return m_tok.group(1), m_cid.group(1)
    except Exception:
        pass
    return None, None


def send(text):
    tok, cid = get_tg()
    if not tok or not cid:
        return False
    try:
        url = 'https://api.telegram.org/bot' + tok + '/sendMessage'
        data = urllib.parse.urlencode({
            'chat_id': cid,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        }).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
        return True
    except Exception:
        return False


def main():
    if not os.path.exists(FLAG):
        return 0
    flag_mt = os.path.getmtime(FLAG)
    sent_mt = os.path.getmtime(SENT) if os.path.exists(SENT) else 0
    if sent_mt >= flag_mt:
        return 0
    try:
        with open(FLAG) as f:
            sym, val, ts = f.read().strip().split('|')
        val_f = float(val)
    except Exception:
        return 1
    nl = '\n'
    msg = ('<b>FUNDING EMERGENCY DETECTADO</b>' + nl + nl +
           'Activo: <b>' + sym + '</b>' + nl +
           'Funding: <b>' + ('%+.3f' % val_f) + '%/8h</b>' + nl +
           'Umbral: 0.15%/8h' + nl + nl +
           'Sistema en PAUSE total (override del regimen).' + nl +
           'Requiere intervencion manual de Satoshi Nakamoto para reanudar.' + nl + nl +
           '<i>Premortem 2026-05-14 - kill-switch GAP 2</i>')
    ok = send(msg)
    if ok:
        Path(SENT).write_text(sym + '|' + val + '|' + str(int(time.time())))
    return 0 if ok else 2


if __name__ == '__main__':
    sys.exit(main())
