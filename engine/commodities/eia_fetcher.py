#!/usr/bin/env python3
"""
EIA Weekly Data Fetcher - Alpha signals para WTI y NG
Endpoint: https://api.eia.gov/v2/petroleum/stoc/wstk/data/
API Key gratuita en: https://www.eia.gov/opendata/register.php

USO: python eia_fetcher.py
Guarda en /opt/sigma/models/eia_weekly.json

Cuando tengas la key, setear en /opt/sigma/engine/config/secrets.json:
  "EIA_API_KEY": "tu_key_aqui"
"""
import json, requests
from pathlib import Path
from datetime import datetime

SECRETS = Path('/opt/sigma/engine/config/secrets.json')
OUT_FILE = Path('/opt/sigma/models/eia_weekly.json')


def get_key():
    try:
        s = json.loads(SECRETS.read_text())
        return s.get('EIA_API_KEY', '')
    except Exception:
        return ''


def fetch_crude_stocks(api_key):
    """Crude oil inventory semanal (millones de barriles). Primer indicador de presion WTI."""
    url = 'https://api.eia.gov/v2/petroleum/stoc/wstk/data/'
    params = {
        'api_key': api_key,
        'frequency': 'weekly',
        'data[0]': 'value',
        'facets[product][]': 'EPC0',   # crude oil total
        'facets[duoarea][]': 'NUS',     # USA
        'sort[0][column]': 'period',
        'sort[0][direction]': 'desc',
        'length': 8,                    # 8 semanas
        'offset': 0,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    rows = data.get('response', {}).get('data', [])
    if not rows:
        return []
    return [{'period': d['period'], 'crude_mbbl': float(d['value'])} for d in rows if d.get('value')]


def fetch_ng_storage(api_key):
    """NG storage semanal (Bcf). Principal driver de precio natural gas."""
    url = 'https://api.eia.gov/v2/natural-gas/stor/wkly/data/'
    params = {
        'api_key': api_key,
        'frequency': 'weekly',
        'data[0]': 'value',
        'facets[duoarea][]': 'NUS',
        'sort[0][column]': 'period',
        'sort[0][direction]': 'desc',
        'length': 8,
        'offset': 0,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    rows = data.get('response', {}).get('data', [])
    if not rows:
        return []
    return [{'period': d['period'], 'ng_bcf': float(d['value'])} for d in rows if d.get('value')]


def run():
    api_key = get_key()
    if not api_key:
        print('[EIA] Sin API key. Registrar en https://www.eia.gov/opendata/register.php')
        print('[EIA] Luego agregar EIA_API_KEY en engine/config/secrets.json')
        return

    result = {'updated_at': datetime.now().isoformat(), 'crude_stocks': [], 'ng_storage': []}

    try:
        result['crude_stocks'] = fetch_crude_stocks(api_key)
        print(f'[EIA] Crude stocks: {len(result["crude_stocks"])} semanas')
    except Exception as e:
        print(f'[EIA] Error crude: {e}')

    try:
        result['ng_storage'] = fetch_ng_storage(api_key)
        print(f'[EIA] NG storage: {len(result["ng_storage"])} semanas')
    except Exception as e:
        print(f'[EIA] Error NG: {e}')

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=2))
    print(f'[EIA] Guardado en {OUT_FILE}')


if __name__ == '__main__':
    run()
