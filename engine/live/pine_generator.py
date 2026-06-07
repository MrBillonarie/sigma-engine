"""
SIGMA ENGINE — Pine Script Generator para Produccion
Genera un Pine Script por TF que:
  1. Replica la logica del HUD v12.9.5 con los mejores params del backtest
  2. Envia alertas al formato CSV del Excel SIGMA K1
  3. Conecta con Make.com via webhook
  4. Cierra el circulo de retroalimentacion

Circulo completo:
  Python Search → Pine Script → HUD → Trade Alert → Make.com → Excel → Monitor → Re-optimizar
"""

import json
import os
from pathlib import Path
from datetime import datetime

OUTPUT_DIR  = Path(__file__).parent.parent.parent
CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
with open(CONFIG_PATH) as f:
    CFG = json.load(f)

# Schema CSV del Excel (desde settings.json)
CSV_SCHEMA = CFG["pipeline"]["csv_schema"]
TELEGRAM_ID= CFG.get("risk", {}).get("tg_chat_id", "")


def generate_production_pine(tf, params=None, metrics=None):
    """
    Genera Pine Script de produccion para un TF especifico.
    params: dict con los mejores parametros del backtest
    metrics: dict con las metricas del backtest
    """
    # Cargar desde models/ si no se proveen
    if params is None or metrics is None:
        # Preferir best_validated.json (modelo OOS validado) sobre config.json
        validated_path = OUTPUT_DIR / "models" / tf / "best_validated.json"
        config_path    = OUTPUT_DIR / "models" / tf / "config.json"
        if validated_path.exists():
            with open(validated_path) as f:
                model = json.load(f)
            params  = model.get("params", {})
            m_oos   = model.get("metrics_oos", model.get("metrics_is", model.get("metrics", {})))
            metrics = m_oos
        elif config_path.exists():
            with open(config_path) as f:
                model = json.load(f)
            params  = model.get("params", {})
            metrics = model.get("metrics", {})
        else:
            print(f"  [WARN] Sin modelo para {tf}. Usando defaults.")
            params  = {}
            metrics = {}

    # Parametros con fallbacks
    p = {
        "use_execute":    params.get("use_execute",    True),
        "use_trend":      params.get("use_trend",      True),
        "use_range":      params.get("use_range",      False),
        "use_asia":       params.get("use_asia",       True),
        "use_sess_b":     params.get("use_sess_b",     True),
        "allow_friday":   params.get("allow_friday",   True),
        "req_htf2":       params.get("req_htf2",       True),
        "use_be":         params.get("use_be",         True),
        "elite_sl":       round(params.get("elite_sl_mult",  1.3),  4),
        "elite_tp":       round(params.get("elite_tp_mult",  2.5),  4),
        "exec_sl":        round(params.get("exec_sl_mult",   1.5),  4),
        "exec_tp":        round(params.get("exec_tp_mult",   2.0),  4),
        "risk_pct":       round(params.get("risk_pct",       CFG["risk"]["risk_per_trade_pct"]), 4),
        "qty_tp1":        round(params.get("qty_tp1",        0.5),  4),
        "adx_min":        params.get("adx_min",        18),
        "hurst_t":        round(params.get("hurst_t",        0.55), 4),
        "adx_t":          params.get("adx_t",          25),
        "hurst_r":        round(params.get("hurst_r",        0.50), 4),
        "adx_r":          params.get("adx_r",          20),
        "ofi_thr":        round(params.get("ofi_threshold",  0.6),  4),
        "cooldown":       params.get("signal_cooldown", 8),
        "temp_min":       params.get("temp_min",        15),
        "temp_max":       params.get("temp_max",        90),
    }

    # HTF por TF
    htf_map = {
        "1m":  ("5",   "15"),
        "5m":  ("15",  "60"),
        "15m": ("60",  "240"),
        "1h":  ("240", "D"),
        "4h":  ("D",   "W"),
        "1d":  ("W",   "M"),
    }
    htf1, htf2 = htf_map.get(tf, ("60", "240"))

    # Sesion por TF
    friday_line = "dayofweek.friday" if p.get("allow_friday", True) else "dayofweek.thursday"
    if tf in ("4h", "1d"):
        session_filter = "true  // Sin filtro de sesion en TFs altos"
    else:
        asia_line = f"or (h_utc>={CFG['sessions_utc']['asia']['start']} and h_utc<{CFG['sessions_utc']['asia']['end']})" if p.get("use_asia", False) else ""
        session_filter = f"""(h_utc>={CFG['sessions_utc']['london']['start']} and h_utc<{CFG['sessions_utc']['london']['end']}) or
    (h_utc>={CFG['sessions_utc']['new_york']['start']} and h_utc<{CFG['sessions_utc']['new_york']['end']}) {asia_line}"""

    # Modes string
    modes = ["ELITE"]
    if p["use_execute"]: modes.append("EXEC")
    if p["use_trend"]:   modes.append("TREND")
    if p["use_range"]:   modes.append("RANGE")
    mode_str = "+".join(modes)

    m = metrics
    bt_summary = (f"Backtest: {m.get('trades',0)}T | WR {m.get('winrate',0):.1f}% | "
                  f"CAGR {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año | "
                  f"PF {m.get('profit_factor',0):.2f} | DD {m.get('max_dd',0):.1f}%")

    # RSI-W filter (1H only — da +1.6% OOS vs sin filtro)
    rsiw_inputs = (
        '\nuse_rsiw   = input.bool(true, "Filtro RSI-W activo",          group="Filtros")'
        '\nrsiw_max   = input.int(65,   "RSI-W bloquea LONG si >=",      group="Filtros", minval=50, maxval=80)'
    ) if tf == "1h" else ""
    rsiw_calc = (
        '\n// RSI Semanal (proxy Fear & Greed)\n'
        'rsi_w     = request.security(syminfo.tickerid, "W", ta.rsi(close, 14), lookahead=barmerge.lookahead_off)\n'
        'rsiw_ok_l = not use_rsiw or rsi_w < rsiw_max'
    ) if tf == "1h" else ""
    rsiw_cond  = "rsiw_ok_l and " if tf == "1h" else ""
    table_rows = "7" if tf == "1h" else "6"
    rsiw_table = (
        '\n    table.cell(t, 0, 6, "RSI-W",    text_color=color.gray,  text_size=size.tiny)'
        '\n    table.cell(t, 1, 6, str.tostring(math.round(rsi_w)),     text_color=rsiw_ok_l ? color.lime : color.red, text_size=size.tiny)'
    ) if tf == "1h" else ""

    # Range vars (solo cuando use_range=True para evitar dead code)
    range_vars_pine = (
        "[bbM, bbU, bbL] = ta.bb(close, 20, 2)\n"
        "bull_div = low<=ta.lowest(low,14) and rsi>ta.lowest(rsi,14)[1] and rsi>30\n"
        "bear_div = high>=ta.highest(high,14) and rsi<ta.highest(rsi,14)[1] and rsi<70"
    ) if p["use_range"] else ""

    pine = f"""//@version=6
// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  SIGMA K1 {tf.upper()} — {mode_str}  [PRODUCCION]
// ║  {bt_summary}
// ║  Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}
// ║  Conectar: TradingView → Alert → Make.com → Excel SIGMA K1
// ╚══════════════════════════════════════════════════════════════════════════════╝
strategy("SIGMA K1 {tf.upper()} — {mode_str}", overlay=true,
         default_qty_type=strategy.percent_of_equity, default_qty_value=100,
         commission_type=strategy.commission.percent, commission_value=0.04,
         slippage=2, initial_capital={CFG['capital']['initial']}, pyramiding=0,
         process_orders_on_close=false, calc_on_every_tick=false)

// ═══════════════════════════════════════════════════════════════════════════════
// INPUTS
// ═══════════════════════════════════════════════════════════════════════════════
// SL/TP
elite_sl   = input.float({p['elite_sl']},  "ELITE SL x ATR",  group="SL/TP")
elite_tp   = input.float({p['elite_tp']},  "ELITE TP x ATR",  group="SL/TP")
exec_sl    = input.float({p['exec_sl']},   "EXEC SL x ATR",   group="SL/TP")
exec_tp    = input.float({p['exec_tp']},   "EXEC TP x ATR",   group="SL/TP")
qty_tp1    = input.float({p['qty_tp1']},   "% cerrar en TP1", group="SL/TP", minval=0.1, maxval=0.9)
use_be     = input.bool({str(p['use_be']).lower()},  "BE tras TP1",     group="SL/TP")

// Risk
risk_pct   = input.float({p['risk_pct']},  "Riesgo % / trade", group="Risk", minval=0.1, maxval=5.0)
capital    = input.float({CFG['capital']['initial']}, "Capital ($)", group="Risk")

// Filtros
adx_min    = input.int({p['adx_min']},    "ADX minimo",       group="Filtros")
ofi_thr    = input.float({p['ofi_thr']},  "OFI umbral",       group="Filtros")
temp_min   = input.int({p['temp_min']},   "Temp minima",      group="Filtros")
temp_max   = input.int({p['temp_max']},   "Temp maxima",      group="Filtros"){rsiw_inputs}

// Make.com webhook
webhook_url = input.string("", "Make.com Webhook URL", group="Conexion")
send_alerts = input.bool(true, "Enviar alertas CSV", group="Conexion")

// ═══════════════════════════════════════════════════════════════════════════════
// INDICADORES BASE
// ═══════════════════════════════════════════════════════════════════════════════
atr     = ta.atr(14)
ema20   = ta.ema(close, 20)
ema50   = ta.ema(close, 50)
ema200  = ta.ema(close, 200)
[ml, sl2, hist] = ta.macd(close, 12, 26, 9)
rsi     = ta.rsi(close, 14)
[dp, dm, adxV] = ta.dmi(14, 14)
bull    = ema50 > ema200
bear    = ema50 < ema200

// HTF
ema50_h1  = request.security(syminfo.tickerid, "{htf1}", ta.ema(close, 50),  lookahead=barmerge.lookahead_off)
ema200_h1 = request.security(syminfo.tickerid, "{htf1}", ta.ema(close, 200), lookahead=barmerge.lookahead_off)
ema50_h2  = request.security(syminfo.tickerid, "{htf2}", ta.ema(close, 50),  lookahead=barmerge.lookahead_off)
ema200_h2 = request.security(syminfo.tickerid, "{htf2}", ta.ema(close, 200), lookahead=barmerge.lookahead_off)
htf1_long  = ema50_h1 > ema200_h1
htf1_short = ema50_h1 < ema200_h1
htf2_long  = ema50_h2 > ema200_h2{rsiw_calc}

// ═══════════════════════════════════════════════════════════════════════════════
// REGIMEN & CONDICIONES BASE
// ═══════════════════════════════════════════════════════════════════════════════
// Hurst proxy
rn    = ta.highest(close,50) - ta.lowest(close,50)
rn2   = ta.highest(close,25) - ta.lowest(close,25)
hurst = rn2 > 0 ? math.log(rn / math.max(rn2,0.001)) / math.log(2.0) : 0.5

// Fake move / Gap / Spike
liq_up   = high > ta.highest(high,20)[1]
liq_dn   = low  < ta.lowest(low, 20)[1]
fake_move = (liq_up and close < open) or (liq_dn and close > open)
is_spike  = (high - low) > atr * 2.0
var int bsg = 9999
is_gap    = math.abs(open - close[1]) > atr * 2.0
bsg      := is_gap ? 0 : bsg + 1
gap_ok    = bsg >= 2

// OFI
body_r   = math.abs(close-open) / math.max(high-low, 0.0001)
buy_v    = volume * (close > open ? body_r : 0.0)
sell_v   = volume * (close < open ? body_r : 0.0)
tot_v    = math.sum(buy_v,20) + math.sum(sell_v,20)
ofi      = ta.ema(tot_v > 0 ? (math.sum(buy_v,20)-math.sum(sell_v,20))/tot_v : 0.0, 3)
ofi_bull = ofi >  ofi_thr
ofi_bear = ofi < -ofi_thr

// Market Temperature
atr_ratio   = atr / math.max(ta.sma(atr,50), 0.0001)
rsi_heat    = math.abs(rsi-50) * 2
price_chg   = math.abs(close-close[3]) / math.max(atr*3, 0.0001) * 100
vol_pct_raw = ta.percentrank(atr, 100)
mkt_temp    = math.min(100, price_chg*0.4 + vol_pct_raw*0.3 + math.min(atr_ratio*50,100)*0.1 + rsi_heat*0.2)
mkt_dead    = mkt_temp < 15
temp_ok     = mkt_temp >= temp_min and mkt_temp <= temp_max

// Sesion & Dia
h_utc  = hour(time, "UTC")
dow_v  = dayofweek(time, "UTC")
in_sess = {session_filter}
dow_ok  = dow_v >= dayofweek.tuesday and dow_v <= {friday_line}

// ═══════════════════════════════════════════════════════════════════════════════
// ORDER BLOCKS & FVG (ICT)
// ═══════════════════════════════════════════════════════════════════════════════
ob_imp_up  = close[9]>open[9] and (close[9]-open[9])>atr[9]*0.8
in_bull_ob = close[10]<open[10] and ob_imp_up and close<=open[10] and close>=close[10] and bull
ob_imp_dn  = close[9]<open[9] and (open[9]-close[9])>atr[9]*0.8
in_bear_ob = close[10]>open[10] and ob_imp_dn and close>=open[10] and close<=close[10] and bear
fvg_bull   = low > high[2] and (low-high[2])/close*100 >= 0.05
fvg_bear   = high < low[2] and (low[2]-high)/close*100 >= 0.05
fill_b_fvg = close<=low[1] and close>=high[3] and bull
fill_s_fvg = close>=high[1] and close<=low[3] and bear

// AVWAP semanal
var float avn = na
var float avdn = na
wk_start    = ta.change(time("W")) != 0
avn        := (wk_start or na(avn))  ? (high+low+close)/3.0*volume : avn+(high+low+close)/3.0*volume
avdn       := (wk_start or na(avdn)) ? volume : avdn+volume
avwap       = avdn > 0 ? avn/avdn : close
above_avwap = close > avwap

// ═══════════════════════════════════════════════════════════════════════════════
// SEÑALES
// ═══════════════════════════════════════════════════════════════════════════════
adx_ok     = adxV > adx_min
htf_ok_l   = htf1_long  {("and htf2_long" if p["req_htf2"] else "")}
htf_ok_s   = htf1_short {("and not htf2_long" if p["req_htf2"] else "")}
base_ok    = not fake_move and not is_spike and gap_ok and dow_ok and in_sess and temp_ok

// Smart / Elite
trend_gate   = math.abs(ema50-ema200) > atr*0.5
smart_long   = bull and trend_gate and ml>sl2 and htf1_long  and not is_spike
smart_short  = bear and trend_gate and ml<sl2 and htf1_short and not is_spike
tf3_bull     = bull and htf1_long  and htf2_long
tf3_bear     = bear and htf1_short and not htf2_long
elite_long   = smart_long  and tf3_bull and not fake_move and rsi < 70
elite_short  = smart_short and tf3_bear and not fake_move and rsi > 30
eit_long     = elite_long  and (in_bull_ob or fill_b_fvg or above_avwap)
eit_short    = elite_short and (in_bear_ob or fill_s_fvg or not above_avwap)

// Execute
{"exec_long  = smart_long  and not elite_long  and adx_ok and htf_ok_l and not fake_move" if p["use_execute"] else "exec_long  = false"}
{"exec_short = smart_short and not elite_short and adx_ok and htf_ok_s and not fake_move" if p["use_execute"] else "exec_short = false"}

// Dual Trend
{"is_tu = hurst>" + str(p["hurst_t"]) + " and adxV>" + str(p["adx_t"]) + " and bull and close>ema50" if p["use_trend"] else "is_tu = false"}
{"is_td = hurst>" + str(p["hurst_t"]) + " and adxV>" + str(p["adx_t"]) + " and bear and close<ema50" if p["use_trend"] else "is_td = false"}
{"trend_long  = is_tu and low<=ema20*1.005 and close>ema20 and close>open and ml>sl2 and htf_ok_l and not fake_move" if p["use_trend"] else "trend_long  = false"}
{"trend_short = is_td and high>=ema20*0.995 and close<ema20 and close<open and ml<sl2 and htf_ok_s and not fake_move" if p["use_trend"] else "trend_short = false"}

// Dual Range
{"is_wr = hurst<" + str(p["hurst_r"]) + " and adxV<" + str(p["adx_r"]) if p["use_range"] else "is_wr = false"}
{range_vars_pine}
{"range_long  = is_wr and low<=bbL and close>bbL and rsi<30 and bull_div and not fake_move" if p["use_range"] else "range_long  = false"}
{"range_short = is_wr and high>=bbU and close<bbU and rsi>70 and bear_div and not fake_move" if p["use_range"] else "range_short = false"}

// Entrada final
entry_long  = base_ok and {rsiw_cond}(eit_long  or (elite_long  and htf_ok_l) or exec_long  or trend_long  or range_long)
entry_short = base_ok and (eit_short or (elite_short and htf_ok_s) or exec_short or trend_short or range_short)

// Calidad para SL/TP
is_elite = eit_long or eit_short or elite_long or elite_short
sl_m = is_elite ? elite_sl : exec_sl
tp_m = is_elite ? elite_tp : exec_tp
quality_str = eit_long or eit_short ? "ELITE_ICT" : elite_long or elite_short ? "ELITE" : "EXECUTE"

// Regimen
regime_str = bull and close>ema50 ? "TREND_BULL" : bear and close<ema50 ? "TREND_BEAR" :
     hurst < 0.5 and adxV < 20 ? "RANGE" : "TRANSITION"
session_str = h_utc>=8 and h_utc<12 ? "LONDON" : h_utc>=13 and h_utc<20 ? "NY_AM" :
     h_utc>=1 and h_utc<6 ? "ASIA" : "OFF"

// ═══════════════════════════════════════════════════════════════════════════════
// GESTION DE POSICION
// ═══════════════════════════════════════════════════════════════════════════════
var float entry_ref  = na
var float sl_ref     = na
var float tp1_ref    = na
var float tp2_ref    = na
var bool  be_done    = false
var string side_str  = ""
var float  rr_plan   = 0.0

if entry_long and strategy.position_size == 0
    entry_ref := close
    sl_ref    := close - atr * sl_m
    tp1_ref   := close + atr * tp_m
    tp2_ref   := close + atr * tp_m * 1.5
    be_done   := false
    side_str  := "LONG"
    rr_plan   := tp_m / sl_m
    strategy.entry("Long", strategy.long)

if entry_short and strategy.position_size == 0
    entry_ref := close
    sl_ref    := close + atr * sl_m
    tp1_ref   := close - atr * tp_m
    tp2_ref   := close - atr * tp_m * 1.5
    be_done   := false
    side_str  := "SHORT"
    rr_plan   := tp_m / sl_m
    strategy.entry("Short", strategy.short)

// BE tras TP1
if strategy.position_size > 0 and high >= tp1_ref and not be_done and use_be
    sl_ref  := entry_ref
    be_done := true
if strategy.position_size < 0 and low <= tp1_ref and not be_done and use_be
    sl_ref  := entry_ref
    be_done := true

// Exits
if strategy.position_size > 0
    strategy.exit("Long_TP1", "Long", qty_percent=qty_tp1*100, stop=sl_ref, limit=tp1_ref, comment="TP1")
    strategy.exit("Long_TP2", "Long",                           stop=sl_ref, limit=tp2_ref, comment="TP2")
if strategy.position_size < 0
    strategy.exit("Short_TP1","Short",qty_percent=qty_tp1*100, stop=sl_ref, limit=tp1_ref, comment="TP1")
    strategy.exit("Short_TP2","Short",                          stop=sl_ref, limit=tp2_ref, comment="TP2")

// ═══════════════════════════════════════════════════════════════════════════════
// ALERTAS → MAKE.COM → EXCEL SIGMA K1
// CSV Schema: {CSV_SCHEMA}
// ═══════════════════════════════════════════════════════════════════════════════
var float rr_real = 0.0
if strategy.position_size != 0 and not na(entry_ref)
    rr_real := (close - entry_ref) / math.max(math.abs(entry_ref - sl_ref), 0.0001) * (strategy.position_size > 0 ? 1 : -1)
else
    rr_real := 0.0

// Alerta de ENTRADA
alert_entry = entry_long or entry_short
if alert_entry and send_alerts
    _dir     = entry_long ? "LONG" : "SHORT"
    _qual    = quality_str
    _sl      = entry_long ? close - atr*sl_m : close + atr*sl_m
    _tp1     = entry_long ? close + atr*tp_m : close - atr*tp_m
    _tp2     = entry_long ? close + atr*tp_m*1.5 : close - atr*tp_m*1.5
    _csv     = str.tostring(time) + "," +
               syminfo.ticker + "," +
               _dir + "," +
               "0," +      // score HUD (conectar con HUD para score real)
               _qual + "," +
               regime_str + "," +
               session_str + "," +
               str.tostring(close,"#.##") + "," +
               str.tostring(_sl,  "#.##") + "," +
               str.tostring(_tp1, "#.##") + "," +
               str.tostring(_tp2, "#.##") + "," +
               "0," +      // exit (se completa al cerrar)
               str.tostring(rr_plan, "#.##") + "," +
               "0," +      // rr_real (se completa al cerrar)
               str.tostring(risk_pct,"#.##") + "," +
               "0," +      // pnl_usd (se completa al cerrar)
               "0," +      // pnl_pct (se completa al cerrar)
               "OPEN," +   // status
               "0," +      // duration
               "0," +      // mae_pct
               "SIGMA_{tf.upper()}_" + _qual
    alert(_csv, alert.freq_once_per_bar_close)

// Alerta de CIERRE (para completar el CSV en Make.com)
alert_close = strategy.position_size[1] != 0 and strategy.position_size == 0
if alert_close and send_alerts
    _result = strategy.netprofit > strategy.netprofit[1] ? "WIN" : "LOSS"
    _pnl    = strategy.netprofit - strategy.netprofit[1]
    _csv_close = "CLOSE," +
                 syminfo.ticker + "," +
                 side_str + "," +
                 _result + "," +
                 str.tostring(close,"#.##") + "," +
                 str.tostring(_pnl,"#.##") + "," +
                 str.tostring(rr_real,"#.##")
    alert(_csv_close, alert.freq_once_per_bar_close)

// ═══════════════════════════════════════════════════════════════════════════════
// VISUALIZACION
// ═══════════════════════════════════════════════════════════════════════════════
plot(ema50,  "EMA50",  color.new(color.yellow, 10), 1)
plot(ema200, "EMA200", color.new(color.orange, 10), 2)
plot(strategy.position_size != 0 ? sl_ref  : na, "SL",  color.new(color.red,  0), 2, plot.style_linebr)
plot(strategy.position_size != 0 ? tp1_ref : na, "TP1", color.new(color.lime, 0), 2, plot.style_linebr)
plot(strategy.position_size != 0 ? tp2_ref : na, "TP2", color.new(color.lime, 40),1, plot.style_linebr)

plotshape(entry_long  and (eit_long  or elite_long),  "L ELITE", shape.triangleup,   location.belowbar, color.aqua, size=size.normal)
plotshape(entry_long  and not elite_long,             "L EXEC",  shape.triangleup,   location.belowbar, color.lime, size=size.small)
plotshape(entry_short and (eit_short or elite_short), "S ELITE", shape.triangledown, location.abovebar, color.aqua, size=size.normal)
plotshape(entry_short and not elite_short,            "S EXEC",  shape.triangledown, location.abovebar, color.red,  size=size.small)

bgcolor(entry_long  ? color.new(color.green, 88) : na)
bgcolor(entry_short ? color.new(color.red,   88) : na)

// Info table
var table t = table.new(position.top_right, 2, {table_rows}, bgcolor=color.new(color.navy,60), border_width=1)
if barstate.islast
    table.cell(t, 0, 0, "SIGMA {tf.upper()}", text_color=color.white,  bgcolor=color.navy, text_size=size.normal)
    table.cell(t, 1, 0, "{mode_str}",          text_color=color.yellow, bgcolor=color.navy, text_size=size.small)
    table.cell(t, 0, 1, "Regimen",  text_color=color.gray,  text_size=size.tiny)
    table.cell(t, 1, 1, regime_str,text_color=color.white,  text_size=size.tiny)
    table.cell(t, 0, 2, "Sesion",   text_color=color.gray,  text_size=size.tiny)
    table.cell(t, 1, 2, session_str,text_color=color.white, text_size=size.tiny)
    table.cell(t, 0, 3, "Temp",     text_color=color.gray,  text_size=size.tiny)
    table.cell(t, 1, 3, str.tostring(math.round(mkt_temp)) + "%", text_color=mkt_dead ? color.red : color.lime, text_size=size.tiny)
    table.cell(t, 0, 4, "OFI",      text_color=color.gray,  text_size=size.tiny)
    table.cell(t, 1, 4, ofi_bull ? "BULL" : ofi_bear ? "BEAR" : "NEUTRAL", text_color=ofi_bull ? color.lime : ofi_bear ? color.red : color.gray, text_size=size.tiny)
    table.cell(t, 0, 5, "ADX",      text_color=color.gray,  text_size=size.tiny)
    table.cell(t, 1, 5, str.tostring(math.round(adxV)),      text_color=adxV>adx_min ? color.lime : color.red, text_size=size.tiny){rsiw_table}
"""
    return pine.strip()


def generate_all_tfs():
    """Genera Pine Scripts para todos los TFs con modelos disponibles."""
    print("\n" + "="*65)
    print("  SIGMA ENGINE — Generando Pine Scripts de Produccion")
    print("="*65)

    models_dir = OUTPUT_DIR / "models"
    pine_dir   = OUTPUT_DIR / "results" / "pine_scripts"
    pine_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    tfs = ["1m", "5m", "15m", "1h", "4h", "1d"]

    for tf in tfs:
        # Preferir best_validated.json, fallback a config.json
        model_path = models_dir / tf / "best_validated.json"
        if not model_path.exists():
            model_path = models_dir / tf / "config.json"
        if not model_path.exists():
            print(f"  {tf.upper()}: sin modelo — skipping")
            continue

        print(f"\n  Generando SIGMA {tf.upper()}...")
        pine = generate_production_pine(tf)

        # Guardar
        filename = f"SIGMA_{tf.upper()}_PRODUCTION.pine"
        path = pine_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            f.write(pine)

        # Copiar tambien en models/tf/
        with open(models_dir / tf / "strategy.pine", "w", encoding="utf-8") as f:
            f.write(pine)

        generated.append(tf)
        with open(model_path) as f:
            m = json.load(f).get("metrics", {})
        print(f"  {tf.upper()} guardado: {filename}")
        print(f"  {m.get('trades',0)}T | WR {m.get('winrate',0):.1f}% | "
              f"CAGR {m.get('cagr', m.get('pnl_pct',0)):+.1f}%/año | "
              f"PF {m.get('profit_factor',0):.2f}")

    print(f"\n{'='*65}")
    print(f"  {len(generated)} Pine Scripts generados: {generated}")
    print(f"  Ubicacion: results/pine_scripts/")
    print(f"\n  COMO CONECTAR AL HUD Y EXCEL:")
    print(f"  1. Abrir TradingView → Pine Script Editor")
    print(f"  2. Pegar SIGMA_{{TF}}_PRODUCTION.pine")
    print(f"  3. Crear Alerta → Condition: strategy.order.action")
    print(f"     Webhook URL: [URL de Make.com]")
    print(f"     Message: {{{{strategy.order.alert_message}}}}")
    print(f"  4. En Make.com: recibir webhook → parsear CSV → agregar fila en TRADES_LOG")
    print(f"  5. El monitor.py lee TRADES_LOG → detecta degradacion → re-optimiza")
    print(f"{'='*65}")

    return generated


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default=None, help="TF especifico o todos si no se especifica")
    args = parser.parse_args()

    if args.tf:
        pine = generate_production_pine(args.tf)
        path = OUTPUT_DIR / "results" / "pine_scripts" / f"SIGMA_{args.tf.upper()}_PRODUCTION.pine"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(pine)
        print(f"Guardado: {path}")
    else:
        generate_all_tfs()
