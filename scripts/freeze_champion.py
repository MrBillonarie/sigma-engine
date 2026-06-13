#!/usr/bin/env python3
import sys, os, json
from datetime import datetime
from pathlib import Path
sys.path.insert(0, '/opt/sigma')
SNAP = Path('/opt/sigma/results/reports/port_snapshot.json')

def load_snap(): return json.loads(SNAP.read_text())
def save_snap(d): SNAP.write_text(json.dumps(d, indent=2))

def send_tg(msg):
    try:
        from utils.secrets import get_tg_token
        import urllib.request, urllib.parse
        token = get_tg_token()
        url = 'https://api.telegram.org/bot' + token + '/sendMessage'
        data = urllib.parse.urlencode({'chat_id': '-1003787411069', 'text': msg,
                                       'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print('TG error: ' + str(e))

def freeze(sym, tf, reason='manual'):
    key = sym.upper() + '|' + tf.lower()
    d = load_snap()
    frozen = d.setdefault('frozen_champions', {})
    champion = d.get('champions', {}).get(key, '?')
    if key in frozen:
        print(key + ' ya congelado desde ' + frozen[key].get('frozen_at', '?'))
        return
    frozen[key] = {
        'frozen_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'reason': reason,
        'champion': champion,
        'paper_trades_since_freeze': 0,
        'paper_wr_since_freeze': None,
    }
    save_snap(d)
    print('CONGELADO: ' + key + ' (' + champion + ') razon: ' + reason)
    NL = chr(10)
    msg = ('<b>SIGMA - Champion Congelado</b>' + NL + NL
           + '<b>Slot:</b> ' + key + NL
           + '<b>Estrategia:</b> ' + champion + NL
           + '<b>Razon:</b> ' + reason + NL + NL
           + 'Paper-only hasta recuperacion (WR >= 55% en 20+ trades).')
    send_tg(msg)

def thaw(sym, tf, reason='recuperacion_confirmada'):
    key = sym.upper() + '|' + tf.lower()
    d = load_snap()
    frozen = d.get('frozen_champions', {})
    if key not in frozen:
        print(key + ' no estaba congelado')
        return
    info = frozen.pop(key)
    d['frozen_champions'] = frozen
    save_snap(d)
    champion = info.get('champion', '?')
    n = info.get('paper_trades_since_freeze', 0)
    wr = info.get('paper_wr_since_freeze', None)
    wr_str = str(round(wr, 1)) + '%' if wr is not None else 'N/A'
    print('DESCONGELADO: ' + key + ' ' + str(n) + ' trades WR=' + wr_str)
    NL = chr(10)
    msg = ('<b>SIGMA - Champion Descongelado</b>' + NL + NL
           + '<b>Slot:</b> ' + key + NL
           + '<b>Estrategia:</b> ' + champion + NL
           + '<b>Paper trades:</b> ' + str(n) + ' (WR ' + wr_str + ')' + NL
           + '<b>Razon:</b> ' + reason + NL + NL
           + 'Vuelve al MACRO loop.')
    send_tg(msg)

def status():
    d = load_snap()
    frozen = d.get('frozen_champions', {})
    champs = d.get('champions', {})
    print('Champions activos: ' + str(len(champs) - len(frozen)))
    print('Champions congelados: ' + str(len(frozen)))
    if frozen:
        print()
        for k, v in sorted(frozen.items()):
            wr = v.get('paper_wr_since_freeze', None)
            wr_s = str(wr) + '%' if wr is not None else 'N/A'
            print('  CONGELADO ' + k + ': ' + v.get('champion', '?') + ' | desde: ' + v.get('frozen_at', '?'))
            print('    razon: ' + v.get('reason', '?') + ' | paper: ' + str(v.get('paper_trades_since_freeze', 0)) + ' trades WR=' + wr_s)
    else:
        print('  (ninguno congelado)')

def auto_check():
    d = load_snap()
    frozen = d.setdefault('frozen_champions', {})
    champs = d.get('champions', {})
    try:
        pm_state = json.loads(Path('/opt/sigma/results/per_model_state.json').read_text())
    except Exception:
        print('No per_model_state.json')
        return
    changed = False
    for key, champ_val in champs.items():
        parts = key.split('|')
        if len(parts) != 2:
            continue
        sym_k, tf_k = parts
        strat_k = champ_val.split('|')[0]
        pm = (pm_state.get(sym_k + '/USDT_' + tf_k + '_' + strat_k)
              or pm_state.get(sym_k + '_' + tf_k + '_' + strat_k))
        if not pm:
            continue
        hist = pm.get('history', [])
        if len(hist) < 10:
            continue
        recent = hist[-10:]
        wr = sum(1 for t in recent if t.get('pnl_pct', 0) > 0) / len(recent) * 100
        if key not in frozen and wr < 45:
            frozen[key] = {
                'frozen_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'reason': 'auto: WR ' + str(round(wr)) + '% en ultimos 10 trades',
                'champion': champ_val,
                'paper_trades_since_freeze': 0,
                'paper_wr_since_freeze': None,
            }
            changed = True
            print('AUTO-FREEZE: ' + key + ' WR=' + str(round(wr)) + '%')
            NL = chr(10)
            send_tg('<b>SIGMA - Auto-Congelado</b>' + NL + NL + key + ': ' + champ_val + NL + 'WR reciente: ' + str(round(wr)) + '% (umbral 45%)')
        elif key in frozen:
            freeze_ts = frozen[key].get('frozen_at', '2000-01-01')
            post = [t for t in hist if t.get('closed_at', '') > freeze_ts]
            frozen[key]['paper_trades_since_freeze'] = len(post)
            if len(post) >= 20:
                wr_post = sum(1 for t in post if t.get('pnl_pct', 0) > 0) / len(post) * 100
                frozen[key]['paper_wr_since_freeze'] = round(wr_post, 1)
                changed = True
                if wr_post >= 55:
                    info = frozen.pop(key)
                    changed = True
                    print('AUTO-THAW: ' + key + ' WR=' + str(round(wr_post)) + '%')
                    NL = chr(10)
                    send_tg('<b>SIGMA - Auto-Descongelado</b>' + NL + NL + key + ': ' + info.get('champion', '?') + NL + 'WR post-freeze: ' + str(round(wr_post)) + '% en ' + str(len(post)) + ' trades')
    if changed:
        d['frozen_champions'] = frozen
        save_snap(d)

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or args[0] == 'status':
        status()
    elif args[0] == 'freeze' and len(args) >= 3:
        freeze(args[1], args[2], ' '.join(args[3:]) if len(args) > 3 else 'manual')
    elif args[0] == 'thaw' and len(args) >= 3:
        thaw(args[1], args[2], ' '.join(args[3:]) if len(args) > 3 else 'recuperacion_confirmada')
    elif args[0] == 'auto':
        auto_check()
    else:
        print('Uso: freeze_champion.py [status | freeze SYM TF razon | thaw SYM TF | auto]')
