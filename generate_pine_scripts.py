"""
Genera Pine Scripts v5 para todos los modelos con confianza ALTA.
Cada script incluye: entrada, SL/TP, alertas, régimen visual, tabla HUD.
"""
import sys, os, json
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

from pathlib import Path
from datetime import datetime

BASE = Path('/opt/sigma/models')
OUT  = Path('/opt/sigma/results/pine_scripts')
OUT.mkdir(parents=True, exist_ok=True)


def pine_breakout(symbol, tf, params, risk_pct, cagr_oos, wr_oos, conf):
    lb   = params.get('lookback', 50)
    vm   = params.get('vol_mult', 2.0)
    slm  = params.get('sl_mult', 2.0)
    tpm  = params.get('tp_mult', 2.5)
    cd   = params.get('cooldown', 8)
    rwthr= params.get('rsi_w_thr', 55)
    sym  = symbol.replace('/USDT', '')
    tf_l = tf.upper()

    return f'''// SIGMA ENGINE — {sym}/USDT {tf_l} BREAKOUT
// Confianza: {conf} | OOS CAGR: {cagr_oos:+.1f}% | WR: {wr_oos:.1f}%
// Auto-generado: {datetime.now().strftime("%Y-%m-%d")}
// v5 — TradingView Pine Script

//@version=5
indicator(title="SIGMA {sym} {tf_l} Breakout", shorttitle="Σ{sym}{tf_l}", overlay=true, max_bars_back=500)

// ═══════════════════════════ PARAMETROS ═══════════════════════════
lookback    = input.int({lb},    "Lookback (barras)")
vol_mult    = input.float({vm:.1f},  "Volume Multiplier", step=0.1)
sl_mult     = input.float({slm:.1f},  "SL Multiplier (ATR)", step=0.1)
tp_mult     = input.float({tpm:.1f},  "TP Multiplier (ATR)", step=0.1)
cooldown    = input.int({cd},    "Cooldown (barras)")
rsi_w_thr   = input.int({rwthr}, "RSI Semanal threshold")
risk_pct_v  = input.float({risk_pct:.1f}, "Risk % por trade", step=0.1)
show_hud    = input.bool(true, "Mostrar HUD")

// ═══════════════════════════ INDICADORES ═══════════════════════════
atr    = ta.atr(14)
ema200 = ta.ema(close, 200)
vol_ma = ta.sma(volume, 20)

// RSI Semanal
rsi_w_raw = request.security(syminfo.tickerid, "W", ta.rsi(close, 14))
rsi_w     = ta.valuewhen(not na(rsi_w_raw), rsi_w_raw, 0)

// Regimen del activo
regime_bull  = rsi_w > rsi_w_thr and close > ema200
regime_bear  = rsi_w < 40 or close < ema200 * 0.97
regime_range = not regime_bull and not regime_bear

// ═══════════════════════════ SENALES ═══════════════════════════════
prev_high   = ta.highest(high, lookback)[1]
vol_ok      = volume > vol_ma * vol_mult
above_200   = close > ema200

// Breakout signal con cooldown
var int last_sig = -cooldown - 1
bar_dist = bar_index - last_sig
raw_signal = close > prev_high and vol_ok and above_200 and regime_bull and bar_dist >= cooldown
if raw_signal
    last_sig := bar_index

long_signal = raw_signal

// SL / TP
sl_price = close - atr * sl_mult
tp_price = close + atr * tp_mult
rr       = tp_mult / sl_mult

// ═══════════════════════════ VISUALES ══════════════════════════════
// Barras de régimen
bgcolor(regime_bull  ? color.new(color.green,  95) : na, title="Bull regime")
bgcolor(regime_range ? color.new(color.yellow, 97) : na, title="Range regime")
bgcolor(regime_bear  ? color.new(color.red,    95) : na, title="Bear regime")

// EMA200
plot(ema200, "EMA200", color=color.new(color.white, 40), linewidth=2)

// Breakout high
plot(prev_high, "Prev High", color=color.new(color.blue, 60), style=plot.style_stepline)

// Señales
plotshape(long_signal, title="LONG", location=location.belowbar,
    color=color.green, style=shape.triangleup, size=size.normal, text="▲")

// SL / TP lines en la entrada
var float entry_sl = na
var float entry_tp = na
if long_signal
    entry_sl := sl_price
    entry_tp := tp_price

plot(long_signal ? sl_price : na, "SL", color=color.red,   style=plot.style_circles, linewidth=2)
plot(long_signal ? tp_price : na, "TP", color=color.green, style=plot.style_circles, linewidth=2)

// ═══════════════════════════ HUD ════════════════════════════════════
if show_hud
    reg_txt  = regime_bull ? "🟢 BULL" : regime_bear ? "🔴 BEAR" : "🟡 RANGE"
    hud_bg   = regime_bull ? color.new(color.green, 85) : regime_bear ? color.new(color.red, 85) : color.new(color.yellow, 90)
    table_hud = table.new(position.top_right, 2, 7, bgcolor=color.new(color.black, 75), border_color=color.gray, border_width=1)
    table.cell(table_hud, 0, 0, "SIGMA {sym} {tf_l}", text_color=color.white, text_size=size.normal, colspan=2, bgcolor=color.new(color.blue,70))
    table.cell(table_hud, 0, 1, "Régimen",  text_color=color.gray,  text_size=size.small)
    table.cell(table_hud, 1, 1, reg_txt,   text_color=color.white, text_size=size.small, bgcolor=hud_bg)
    table.cell(table_hud, 0, 2, "RSI_W",   text_color=color.gray,  text_size=size.small)
    table.cell(table_hud, 1, 2, str.tostring(math.round(rsi_w, 1)), text_color=color.white, text_size=size.small)
    table.cell(table_hud, 0, 3, "vs EMA200", text_color=color.gray, text_size=size.small)
    table.cell(table_hud, 1, 3, str.tostring(math.round((close/ema200-1)*100,1)) + "%", text_color=close>ema200?color.green:color.red, text_size=size.small)
    table.cell(table_hud, 0, 4, "OOS CAGR", text_color=color.gray,  text_size=size.small)
    table.cell(table_hud, 1, 4, "{cagr_oos:+.1f}% ({conf})", text_color=color.yellow, text_size=size.small)
    table.cell(table_hud, 0, 5, "RR",       text_color=color.gray,  text_size=size.small)
    table.cell(table_hud, 1, 5, str.tostring(math.round(rr,1)) + ":1", text_color=color.white, text_size=size.small)
    table.cell(table_hud, 0, 6, "Risk",     text_color=color.gray,  text_size=size.small)
    table.cell(table_hud, 1, 6, str.tostring(risk_pct_v) + "%", text_color=color.white, text_size=size.small)

// ═══════════════════════════ ALERTAS ════════════════════════════════
alertcondition(long_signal, "SIGMA {sym} {tf_l} LONG",
    "{{{{ticker}}}} {tf_l} BREAKOUT — Entry: {{{{close}}}} | SL: " + str.tostring(math.round(close - atr*sl_mult, 2)) + " | TP: " + str.tostring(math.round(close + atr*tp_mult, 2)) + " | Risk: {risk_pct:.1f}%")
alertcondition(regime_bear and not regime_bear[1], "SIGMA {sym} Entró en BEAR", "{{{{ticker}}}} cambió a BEAR — pausar operaciones")
alertcondition(regime_bull and not regime_bull[1], "SIGMA {sym} Volvió a BULL", "{{{{ticker}}}} volvió a BULL — reactivar operaciones")
'''


def pine_mean_rev(symbol, tf, params, risk_pct, cagr_oos, wr_oos, conf):
    slm  = params.get('sl_mult', 2.5)
    tpm  = params.get('tp_mult', 2.5)
    cd   = params.get('cooldown', 10)
    rwthr= params.get('rsi_w_thr', 50)
    rsos = params.get('rsi_os', 35)
    sym  = symbol.replace('/USDT', '')
    tf_l = tf.upper()

    return f'''// SIGMA ENGINE — {sym}/USDT {tf_l} MEAN REVERSION
// Confianza: {conf} | OOS CAGR: {cagr_oos:+.1f}% | WR: {wr_oos:.1f}%
// Auto-generado: {datetime.now().strftime("%Y-%m-%d")}

//@version=5
indicator(title="SIGMA {sym} {tf_l} MeanRev", shorttitle="Σ{sym}{tf_l}MR", overlay=true, max_bars_back=500)

// ═══════════════════════════ PARAMETROS ═══════════════════════════
sl_mult    = input.float({slm:.1f}, "SL Multiplier", step=0.1)
tp_mult    = input.float({tpm:.1f}, "TP Multiplier", step=0.1)
cooldown   = input.int({cd}, "Cooldown")
rsi_w_thr  = input.int({rwthr}, "RSI Semanal threshold")
rsi_os     = input.int({rsos},  "RSI14 oversold level")
risk_pct_v = input.float({risk_pct:.1f}, "Risk %", step=0.1)
show_hud   = input.bool(true, "Mostrar HUD")

// ═══════════════════════════ INDICADORES ═══════════════════════════
atr    = ta.atr(14)
rsi14  = ta.rsi(close, 14)
ema200 = ta.ema(close, 200)
rsi_w_raw = request.security(syminfo.tickerid, "W", ta.rsi(close, 14))
rsi_w     = ta.valuewhen(not na(rsi_w_raw), rsi_w_raw, 0)

regime_bull = rsi_w > rsi_w_thr and close > ema200
regime_bear = rsi_w < 40 or close < ema200 * 0.97

// ═══════════════════════════ SEÑAL ════════════════════════════════
var int last_sig = -cooldown - 1
bar_dist = bar_index - last_sig
raw_signal = rsi14 < rsi_os and close > ema200 and regime_bull and bar_dist >= cooldown
if raw_signal
    last_sig := bar_index

long_signal = raw_signal
sl_price = close - atr * sl_mult
tp_price = close + atr * tp_mult

// ═══════════════════════════ VISUALES ══════════════════════════════
bgcolor(regime_bull ? color.new(color.green, 95) : na)
bgcolor(regime_bear ? color.new(color.red,   95) : na)
plot(ema200, "EMA200", color=color.new(color.white, 40), linewidth=2)
plotshape(long_signal, location=location.belowbar, color=color.green, style=shape.triangleup, size=size.normal, text="MR▲")
plot(long_signal ? sl_price : na, "SL", color=color.red,   style=plot.style_circles, linewidth=2)
plot(long_signal ? tp_price : na, "TP", color=color.green, style=plot.style_circles, linewidth=2)

// HUD
if show_hud
    t = table.new(position.top_right, 2, 5, bgcolor=color.new(color.black, 75), border_color=color.gray, border_width=1)
    table.cell(t, 0, 0, "SIGMA {sym} {tf_l} MR", text_color=color.white, colspan=2, bgcolor=color.new(color.purple, 70))
    table.cell(t, 0, 1, "RSI14",  text_color=color.gray,  text_size=size.small)
    table.cell(t, 1, 1, str.tostring(math.round(rsi14,1)), text_color=rsi14<rsi_os?color.green:color.white, text_size=size.small)
    table.cell(t, 0, 2, "RSI_W",  text_color=color.gray,  text_size=size.small)
    table.cell(t, 1, 2, str.tostring(math.round(rsi_w,1)), text_color=color.white, text_size=size.small)
    table.cell(t, 0, 3, "OOS",    text_color=color.gray,  text_size=size.small)
    table.cell(t, 1, 3, "{cagr_oos:+.1f}% {conf}", text_color=color.yellow, text_size=size.small)
    table.cell(t, 0, 4, "Régimen", text_color=color.gray,  text_size=size.small)
    table.cell(t, 1, 4, regime_bull?"🟢 BULL":regime_bear?"🔴 BEAR":"🟡 RANGE", text_color=color.white, text_size=size.small)

alertcondition(long_signal, "SIGMA {sym} {tf_l} MEAN REV LONG", "{{{{ticker}}}} Oversold bounce — RSI14=" + str.tostring(math.round(rsi14,1)))
alertcondition(regime_bull and not regime_bull[1], "SIGMA {sym} BULL activado", "{{{{ticker}}}} régimen cambió a BULL")
'''


# ── MAIN ──────────────────────────────────────────────────────────────────────
MODELS = [
    ('ETH/USDT', '1h',  'breakout',  {'sl_mult':1.7,'tp_mult':2.4,'cooldown':8,'rsi_w_thr':62,'lookback':31,'vol_mult':3.4}, 4.5, 65.3, 64.3, 'ALTA'),
    ('BNB/USDT', '4h',  'breakout',  {'sl_mult':2.2,'tp_mult':3.7,'cooldown':9,'rsi_w_thr':59,'lookback':65,'vol_mult':1.2}, 4.5, 23.8, 69.2, 'ALTA'),
    ('SOL/USDT', '4h',  'breakout',  {'sl_mult':3.0,'tp_mult':1.7,'cooldown':5,'rsi_w_thr':46,'lookback':33,'vol_mult':1.5}, 3.3, 17.9, 83.3, 'ALTA'),
    ('SOL/USDT', '15m', 'mean_rev',  {'sl_mult':3.2,'tp_mult':2.7,'cooldown':14,'rsi_w_thr':50,'rsi_os':42}, 3.3, 14.8, 83.3, 'ALTA'),
]

print(f'\n{"="*55}')
print(f'  GENERANDO PINE SCRIPTS — {len(MODELS)} modelos ALTA')
print(f'{"="*55}\n')

for sym, tf, strat, params, risk_pct, cagr, wr, conf in MODELS:
    asset = sym.replace('/USDT', '')
    fname = f'SIGMA_{asset}_{tf.upper()}_{strat.upper()}.pine'

    if strat == 'breakout':
        code = pine_breakout(sym, tf, params, risk_pct, cagr, wr, conf)
    elif strat == 'mean_rev':
        code = pine_mean_rev(sym, tf, params, risk_pct, cagr, wr, conf)
    else:
        print(f'  [SKIP] {asset} {tf} — estrategia {strat} no tiene template')
        continue

    out = OUT / fname
    out.write_text(code, encoding='utf-8')
    print(f'  [SAVED] {fname}  ({len(code):,} chars)')

print(f'\n  Scripts en: {OUT}')
print(f'{"="*55}\n')
