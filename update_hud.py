"""
SIGMA ENGINE — Auto-actualiza el lookup table de SIGMA_v13_COMPLETO.pine
Lee todos los modelos validados del VPS y regenera las tablas de parámetros.
Ejecutar después de cada modelo nuevo, o via cron diario.
"""
import json, re, sys
from pathlib import Path
from datetime import datetime

# Rutas (funciona en VPS y local Windows)
_vps_models = Path('/opt/sigma/models')
_vps_hud    = Path('/opt/sigma/results/pine_scripts/SIGMA_v13_COMPLETO.pine')
_local_hud  = Path('c:/Users/Desktop/Desktop/TRADES/BACKTESSTING/SIGMA_v13_COMPLETO.pine')
_local_models = Path('c:/Users/Desktop/Desktop/TRADES/BACKTESSTING/models')

if _vps_models.exists() and _vps_hud.exists():
    MODELS_DIR = _vps_models
    HUD_FILE   = _vps_hud
elif _local_hud.exists():
    MODELS_DIR = _local_models
    HUD_FILE   = _local_hud
else:
    MODELS_DIR = Path('models')
    HUD_FILE   = Path('SIGMA_v13_COMPLETO.pine')

# Umbrales mínimos para considerar un modelo válido
MIN_CAGR_OOS = 10.0   # % mínimo CAGR OOS
MIN_WR_OOS   = 55.0   # % mínimo WR OOS

# Mapa: (ticker_key, tf_period) → par TradingView
# ticker_key = substring que aparece en syminfo.ticker
TICKER_MAP = {
    'BTC': ['BTC','BTCUSDT','BTCUSDTPERP','BINANCE:BTCUSDT'],
    'ETH': ['ETH','ETHUSDT'],
    'SOL': ['SOL','SOLUSDT'],
    'BNB': ['BNB','BNBUSDT'],
    'LTC': ['LTC','LTCUSDT'],
    'XRP': ['XRP','XRPUSDT'],
}

# TF Pine Script period string
TF_PINE = {
    '1m':  '"1"',   '5m':  '"5"',   '15m': '"15"',
    '30m': '"30"',  '1h':  '"60"',  '4h':  '"240"',
    '1d':  '"D"',   '1w':  '"W"',
}

# Estrategia → nombre Pine
STRAT_MAP = {
    'breakout':        'breakout_long',
    'breakout_long':   'breakout_long',
    'breakdown':       'breakdown_short',
    'breakdown_short': 'breakdown_short',
    'pullback':        'breakout_long',   # pullback tratado como breakout a efectos del HUD
    'mean_rev':        'mean_rev_long',
    'mean_rev_long':   'mean_rev_long',
    'regime_adaptive': 'regime_adaptive',
    'adaptive':        'regime_adaptive',
    'tma_bands':       'breakout_long',
    'momentum':        'breakout_long',
    'micro_momentum':  'breakout_long',
}

# Confianza según CAGR + WR
def get_conf(cagr, wr, mc=None):
    if mc is not None:
        if mc >= 85 and cagr >= 15: return 'ALTA'
        if mc >= 70 and cagr >= 10: return 'MEDIA'
        return 'BAJA'
    if cagr >= 20 and wr >= 65: return 'ALTA'
    if cagr >= 12 and wr >= 58: return 'ALTA'
    if cagr >= 8  and wr >= 55: return 'MEDIA'
    return 'BAJA'


def scan_models():
    """Lee todos los JSONs de modelos y devuelve el mejor por (asset, tf)."""
    best = {}

    for tf_dir in MODELS_DIR.iterdir():
        if not tf_dir.is_dir(): continue
        tf = tf_dir.name
        if tf not in TF_PINE: continue

        for jf in tf_dir.glob('*.json'):
            try:
                data = json.loads(jf.read_text())
            except Exception:
                continue

            # Soporta lista (adaptive_params) o dict
            if isinstance(data, list):
                continue
            if not isinstance(data, dict):
                continue

            # Extraer symbol — desde el dato o desde el nombre del archivo
            sym = data.get('symbol', '')
            if not sym:
                # eth_breakout.json → ETH
                stem = jf.stem.upper()
                for known in ['BTC','ETH','SOL','BNB','LTC','XRP']:
                    if stem.startswith(known):
                        sym = known
                        break
            if not sym:
                continue

            strategy = data.get('strategy', '')
            params   = data.get('params',  {})
            risk_pct = float(data.get('risk_pct', 3.0) or 3.0)
            m_oos    = data.get('metrics_oos') or {}
            cagr     = float(m_oos.get('cagr', 0.0) or 0.0)
            wr       = float(m_oos.get('wr',   0.0) or 0.0)
            mc_conf  = data.get('mc_conf') or data.get('mc_p_positive')

            if cagr < MIN_CAGR_OOS or wr < MIN_WR_OOS:
                continue

            asset = sym.replace('/USDT','').replace('USDT','').upper()
            if not asset:
                continue

            # Solo activos conocidos
            if asset not in ['BTC','ETH','SOL','BNB','LTC','XRP']:
                continue

            pine_strat = STRAT_MAP.get(strategy, 'breakout_long')
            key = (asset, tf)

            if key not in best or cagr > best[key]['cagr']:
                best[key] = {
                    'asset':    asset,
                    'tf':       tf,
                    'strategy': pine_strat,
                    'lookback': int(params.get('lookback',  50)),
                    'vol_mult': float(params.get('vol_mult', 2.0)),
                    'sl_mult':  float(params.get('sl_mult',  2.0)),
                    'tp_mult':  float(params.get('tp_mult',  2.5)),
                    'cooldown': int(params.get('cooldown',   8)),
                    'rsi_w_thr':int(params.get('rsi_w_thr', 55)),
                    'rsi_os':   int(params.get('rsi_os',    35)),
                    'cagr':     round(cagr, 1),
                    'wr':       round(wr,   1),
                    'risk_pct': round(risk_pct, 1),
                    'conf':     get_conf(cagr, wr, mc_conf),
                }

    return best


def build_lookup_block(models):
    """Genera el bloque Pine Script con las tablas de lookup."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Ordenar para presentación consistente
    items = sorted(models.values(), key=lambda x: (x['asset'], x['tf']))

    def tf_pine(tf):
        return TF_PINE.get(tf, f'"{tf}"')

    def asset_check(asset):
        return f'str.contains(_tk,"{asset}")'

    def row(asset, tf, val, default, fmt=None):
        """Genera línea del ternario para un modelo."""
        ac = asset_check(asset)
        tp = tf_pine(tf)
        if fmt == 'str':
            return f'  {ac} and _tf=={tp} ? "{val}" :'
        elif fmt == 'float':
            return f'  {ac} and _tf=={tp} ? {val:.1f} :'
        elif fmt == 'int':
            return f'  {ac} and _tf=={tp} ? {int(val)} :'
        return f'  {ac} and _tf=={tp} ? {val} :'

    lines_strat   = []
    lines_lb      = []
    lines_vm      = []
    lines_sl      = []
    lines_tp      = []
    lines_cd      = []
    lines_rsiwt   = []
    lines_rsios   = []
    lines_cagr    = []
    lines_wr      = []
    lines_conf    = []

    for m in items:
        a, tf = m['asset'], m['tf']
        lines_strat.append(row(a, tf, m['strategy'],  'breakout_long', 'str'))
        lines_lb.append(   row(a, tf, m['lookback'],  50,              'int'))
        lines_vm.append(   row(a, tf, m['vol_mult'],  2.0,             'float'))
        lines_sl.append(   row(a, tf, m['sl_mult'],   2.0,             'float'))
        lines_tp.append(   row(a, tf, m['tp_mult'],   2.5,             'float'))
        lines_cd.append(   row(a, tf, m['cooldown'],  8,               'int'))
        lines_rsiwt.append(row(a, tf, m['rsi_w_thr'], 55,              'int'))
        if m['strategy'] == 'mean_rev_long':
            lines_rsios.append(row(a, tf, m['rsi_os'], 35,             'int'))
        lines_cagr.append( row(a, tf, m['cagr'],      0.0,             'float'))
        lines_wr.append(   row(a, tf, m['wr'],         0.0,            'float'))
        lines_conf.append( row(a, tf, m['conf'],       'PENDIENTE',    'str'))

    def table(var, lines, default):
        body = '\n'.join(lines) if lines else ''
        return f'{var} =\n{body}\n  {default}'

    block = f'''// ── Lookup table: estrategia ─────────────────────────────── actualizado {now}
{table("eng_strategy", lines_strat, '"breakout_long"    // fallback')}

// ── Lookup table: lookback ────────────────────────────────────────────────────
{table("eng_lookback", lines_lb, '50')}

// ── Lookup table: vol_mult ────────────────────────────────────────────────────
{table("eng_vol_mult", lines_vm, '2.0')}

// ── Lookup table: sl_mult ─────────────────────────────────────────────────────
{table("eng_sl_mult", lines_sl, '2.0')}

// ── Lookup table: tp_mult ─────────────────────────────────────────────────────
{table("eng_tp_mult", lines_tp, '2.5')}

// ── Lookup table: cooldown ────────────────────────────────────────────────────
{table("eng_cooldown", lines_cd, '8')}

// ── Lookup table: rsi_w_thr ───────────────────────────────────────────────────
{table("eng_rsi_w_thr", lines_rsiwt, '55')}

// ── Lookup table: rsi_os (mean reversion) ────────────────────────────────────
{table("eng_rsi_os", lines_rsios, '35')}

// ── Lookup table: métricas del modelo ────────────────────────────────────────
{table("eng_model_cagr", lines_cagr, '0.0')}

{table("eng_model_wr", lines_wr, '0.0')}

{table("eng_model_conf", lines_conf, '"PENDIENTE"')}'''

    return block


# Regex que captura el bloque de lookup tables entre los dos comentarios ancla
BLOCK_START = r'// ── Lookup table: estrategia ──.*?actualizado.*?\n'
BLOCK_END   = r'// ── RSI semanal \(propio del ENGINE\)'
PATTERN     = re.compile(
    r'(// ── Lookup table: estrategia ──.*?)(// ── RSI semanal \(propio del ENGINE\))',
    re.DOTALL
)


def update_pine(hud_path, new_block):
    src = hud_path.read_text(encoding='utf-8')
    if not PATTERN.search(src):
        print(f'  [ERROR] No encontré el bloque de lookup tables en {hud_path}')
        return False
    updated = PATTERN.sub(new_block + '\n\n// ── RSI semanal (propio del ENGINE)', src)
    hud_path.write_text(updated, encoding='utf-8')
    return True


# Modelos validados manualmente (fallback cuando no hay JSONs del VPS)
# Actualizar cuando el VPS encuentre mejores modelos
KNOWN_MODELS = {
    ('ETH', '1h'):  {'asset':'ETH','tf':'1h', 'strategy':'breakout_long',  'lookback':31,'vol_mult':3.4,'sl_mult':1.7,'tp_mult':2.4,'cooldown':8, 'rsi_w_thr':62,'rsi_os':35,'cagr':65.3,'wr':64.3,'risk_pct':4.5,'conf':'ALTA'},
    ('SOL', '4h'):  {'asset':'SOL','tf':'4h', 'strategy':'breakout_long',  'lookback':33,'vol_mult':1.5,'sl_mult':3.0,'tp_mult':1.7,'cooldown':5, 'rsi_w_thr':46,'rsi_os':35,'cagr':17.9,'wr':83.3,'risk_pct':3.3,'conf':'ALTA'},
    ('SOL', '15m'): {'asset':'SOL','tf':'15m','strategy':'mean_rev_long',  'lookback':20,'vol_mult':2.0,'sl_mult':3.2,'tp_mult':2.7,'cooldown':14,'rsi_w_thr':50,'rsi_os':42,'cagr':14.8,'wr':83.3,'risk_pct':3.3,'conf':'ALTA'},
    ('BNB', '4h'):  {'asset':'BNB','tf':'4h', 'strategy':'breakout_long',  'lookback':65,'vol_mult':1.2,'sl_mult':2.2,'tp_mult':3.7,'cooldown':9, 'rsi_w_thr':59,'rsi_os':35,'cagr':23.8,'wr':69.2,'risk_pct':4.5,'conf':'MEDIA'},
}


def main():
    print(f'\n{"="*55}')
    print(f'  SIGMA ENGINE — Update HUD  ({datetime.now().strftime("%Y-%m-%d %H:%M")})')
    print(f'{"="*55}\n')

    if not HUD_FILE.exists():
        print(f'  [ERROR] No encontre {HUD_FILE}')
        sys.exit(1)

    models = scan_models()

    if not models:
        print(f'  [INFO] Sin JSONs nuevos — usando modelos validados conocidos')
        models = dict(KNOWN_MODELS)
    else:
        # Fusionar: VPS tiene prioridad, KNOWN_MODELS como fallback
        for k, v in KNOWN_MODELS.items():
            if k not in models:
                models[k] = v
                print(f'  [FALLBACK] {k[0]} {k[1]} — sin JSON, usando conocido')

    print(f'  {len(models)} modelos encontrados:\n')
    for (asset, tf), m in sorted(models.items()):
        print(f'  {asset:4s} {tf:4s}  {m["strategy"]:18s}  '
              f'CAGR:{m["cagr"]:+.1f}%  WR:{m["wr"]:.1f}%  [{m["conf"]}]')

    new_block = build_lookup_block(models)
    ok = update_pine(HUD_FILE, new_block)

    if ok:
        print(f'\n  [OK] {HUD_FILE} actualizado')
        print(f'  Sube SIGMA_v13_COMPLETO.pine a TradingView para aplicar cambios')
    else:
        print(f'\n  [FAIL] No se pudo actualizar el HUD')

    print(f'{"="*55}\n')


if __name__ == '__main__':
    main()
