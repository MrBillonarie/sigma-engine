#!/usr/bin/env python3
"""
SIGMA Notifier — corre en tu PC, avisa cuando hay nuevo modelo en VPS.
Uso: python notifier.py
     python notifier.py --interval 180  (revisar cada 3 min)
"""
import urllib.request, json, time, argparse, subprocess, sys
from datetime import datetime

API = 'http://178.104.10.97:8080/api/new_records'

def beep(n=2):
    try:
        import winsound
        for _ in range(n):
            winsound.Beep(1000, 300)
            time.sleep(0.15)
    except: pass

def popup(title, msg):
    try:
        subprocess.Popen([
            'powershell', '-WindowStyle', 'Hidden', '-Command',
            f'[System.Windows.Forms.MessageBox]::Show("{msg}", "{title}", '
            f'"OK", "Information") | Out-Null'
        ], creationflags=0x08000000)
    except:
        try:
            subprocess.Popen([
                'powershell', '-Command',
                f'Add-Type -AssemblyName System.Windows.Forms; '
                f'[System.Windows.Forms.MessageBox]::Show("{msg}","{title}")'
            ])
        except: pass

BASE_URL = 'http://178.104.10.97:8080'

def fetch(path):
    try:
        r = urllib.request.urlopen(BASE_URL + path, timeout=8)
        return json.loads(r.read())
    except:
        return None

def fetch_records():
    d = fetch('/api/new_records')
    return d['records'] if d else None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interval', type=int, default=300, help='Segundos entre checks (default 300)')
    args = parser.parse_args()

    print(f'SIGMA Notifier iniciado — revisando cada {args.interval}s')
    print(f'Endpoint: {API}')
    print('Ctrl+C para detener\n')

    seen_models    = set()
    seen_regimes   = set()
    seen_model_rec = {}   # (sym, tf) -> last recommendation
    seen_blocked   = set()
    seen_signals   = set()  # sym+tf activos con señal

    while True:
        ts = datetime.now().strftime('%H:%M:%S')

        # ── Chequeo 1: Nuevos modelos ─────────────────────────────────────
        records = fetch_records()
        if records is None:
            print(f'[{ts}] Sin conexión con VPS')
        elif records:
            new = [r for r in records if r['file'] + r.get('saved_at','') not in seen_models]
            if new:
                for r in new:
                    key = r['file'] + r.get('saved_at','')
                    seen_models.add(key)
                    sym = r['symbol'].replace('/USDT','')
                    msg = (f"{sym} {r['tf'].upper()} {r['strategy']}\n"
                           f"CAGR: {r['cagr']:+.1f}%  WR: {r['wr']:.0f}%\n"
                           f"Guardado: {r['saved_at'][:16]}")
                    print(f'[{ts}] NUEVO MODELO: {msg.replace(chr(10), " | ")}')
                    beep(3)
                    popup('SIGMA — Nuevo Modelo!', msg)

        # ── Chequeo 2: Cambios de régimen ─────────────────────────────────
        regime_data = fetch('/api/regime_changes')
        if regime_data:
            for ch in regime_data.get('changes', []):
                key = ch['asset'] + ch['ts']
                if key not in seen_regimes:
                    seen_regimes.add(key)
                    msg = (f"{ch['asset']}: {ch['from']} → {ch['to']}\n"
                           f"Precio: {ch['price']}  RSI_W: {ch['rsi_w']}\n"
                           f"Hora: {ch['ts']}")
                    print(f'[{ts}] RÉGIMEN CAMBIADO: {msg.replace(chr(10), " | ")}')
                    # Beeps distintos: BULL=agudo, BEAR=grave
                    if ch['to'] == 'BULL':
                        beep(4)
                        popup(f'SIGMA — {ch["asset"]} pasó a BULL 🟢', msg)
                    elif ch['to'] == 'BEAR':
                        try:
                            import winsound
                            for _ in range(3):
                                winsound.Beep(400, 500)
                                time.sleep(0.1)
                        except: beep(2)
                        popup(f'SIGMA — {ch["asset"]} pasó a BEAR 🔴', msg)
                    else:
                        beep(2)
                        popup(f'SIGMA — {ch["asset"]} pasó a RANGE ⚪', msg)

        # ── Chequeo 3: Risk status ────────────────────────────────────────
        risk = fetch('/api/risk_status')
        if risk and risk.get('level') in ('CAUTION','PAUSE'):
            level = risk['level']
            msg   = risk.get('msg','')
            if level == 'PAUSE':
                try:
                    import winsound
                    for _ in range(5): winsound.Beep(300, 600); time.sleep(0.1)
                except: beep(3)
                popup(f'SIGMA — ⛔ {level}: REDUCIR EXPOSICIÓN', msg)
                print(f'[{ts}] ⛔ RISK {level}: {msg}')
            else:
                print(f'[{ts}] ⚠ RISK {level}: {msg}')

        # ── Status + degradation detector ─────────────────────────────────
        signals = fetch('/api/signals')
        if signals and signals.get('regime') not in ('LOADING', None):
            models_list = signals.get('models', [])
            active = [m for m in models_list if m.get('signal') and m.get('slot',0)>0]
            risk_level = risk.get('level','OK') if risk else 'OK'
            max_slots  = risk.get('max_slots', 2) if risk else 2
            if active:
                names = ', '.join(f'{m["sym"]} {m["tf"]}' for m in active[:max_slots])
                print(f'[{ts}] SEÑAL ({risk_level}): {names} | slots disponibles: {max_slots}')
                # Popup solo para señales nuevas
                for m in active[:max_slots]:
                    sig_key = m['sym'] + m['tf']
                    if sig_key not in seen_signals:
                        seen_signals.add(sig_key)
                        direction = 'SHORT' if m.get('type') == 'short' else 'LONG'
                        price_txt = f"@ {m['price']}" if m.get('price') else ''
                        sl_txt    = f"SL:{m['sl']}" if m.get('sl') else ''
                        tp_txt    = f"TP:{m['tp']}" if m.get('tp') else ''
                        msg = (f"{m['sym']} {m['tf'].upper()} {direction} {price_txt}\n"
                               f"{sl_txt}  {tp_txt}\n"
                               f"Grade:{m.get('grade','?')} | {m.get('reason','')}")
                        beep(4)
                        popup(f'SIGMA — SEÑAL {direction} {m["sym"]} {m["tf"].upper()}', msg)
            else:
                # Limpiar señales que ya no están activas
                seen_signals.clear()
                print(f'[{ts}] Sin señales | Régimen: {signals.get("regime","?")} | Risk: {risk_level}')

            # Detectar degradación: modelo ACTIVAR → NO_ACTIVAR o grade bajó
            for m in models_list:
                mk = (m.get('sym',''), m.get('tf',''))
                rec = m.get('recommendation','')
                gr  = m.get('grade','?')
                prev_rec = seen_model_rec.get(mk)

                if prev_rec == 'ACTIVAR' and rec == 'NO_ACTIVAR' and mk not in seen_blocked:
                    seen_blocked.add(mk)
                    msg = (f"{mk[0]} {mk[1].upper()} — modelo degradado\n"
                           f"Grade: {gr} | {m.get('reason','')}")
                    print(f'[{ts}] ⚠ DEGRADACION: {msg.replace(chr(10)," | ")}')
                    try:
                        import winsound
                        for _ in range(3): winsound.Beep(600, 400); time.sleep(0.1)
                    except: beep(2)
                    popup(f'SIGMA — Modelo degradado {mk[0]} {mk[1]}', msg)
                elif rec == 'ACTIVAR' and mk in seen_blocked:
                    seen_blocked.discard(mk)  # recuperado

                seen_model_rec[mk] = rec

        time.sleep(args.interval)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nNotifier detenido.')
