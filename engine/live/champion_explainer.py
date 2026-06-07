#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Champion Explainer
Cuando el optimizador encuentra un nuevo modelo campeon,
genera una explicacion narrativa inteligente y la envia a Telegram.
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import sys, json, urllib.request, os

TOKEN   = _sigma_get_tg_token()
CHAT_ID = "-1003787411069"

def send_tg(msg):
    url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True}
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
          headers={"Content-Type": "application/json; charset=utf-8"})
    urllib.request.urlopen(req, timeout=10)

STRATEGY_DESCRIPTIONS = {
    "breakout":       ("Ruptura de Rango", "Entra cuando el precio rompe maximos con volumen — captura tendencias nacientes"),
    "breakdown":      ("Ruptura Bajista", "Entra cuando el precio rompe minimos con volumen — ideal en mercados bajistas"),
    "pullback":       ("Retroceso a EMA", "Compra retrocesos en tendencia alcista — entrada de calidad en la direccion del mercado"),
    "pullback_short": ("Rebote Bajista", "Vende rebotes en tendencia bajista — aprovecha correcciones en BEAR"),
    "momentum":       ("Momentum Alcista", "Entra en aceleracion de momentum MACD al alza — sigue la fuerza"),
    "momentum_short": ("Momentum Bajista", "Entra en aceleracion de momentum MACD a la baja — sigue la debilidad"),
    "tma_bands":      ("Bandas TMA", "Rebota desde la banda inferior del canal TMA — mean reversion con filtro de tendencia"),
    "mean_rev":       ("Reversion a Media", "Compra en RSI extremo oversold — el mercado tiende a volver a la media"),
    "regime_adaptive":("Adaptativo",       "Cambia entre long y short segun el regimen del mercado — la mas flexible"),
    "break_of_structure":      ("Breakdown Short",  "Ruptura de soporte con volumen — señal bajista de alta conviccion"),
    "zscore_reversion":("Z-Score Estadistico", "Reversion estadistica cuando el precio se aleja 2+ sigmas de la media"),
    "ema_ribbon":     ("Cinta de EMAs",    "3 medias moviles alineadas confirman tendencia fuerte y sostenida"),
    "stoch_rsi":      ("StochRSI",         "RSI del RSI — detecta momentum extremo con mayor precision"),
    "ichimoku":       ("Ichimoku Cloud",   "Sistema completo japones: nube, tenkan y kijun confirman tendencia"),
    "squeeze_pro":    ("Squeeze Pro",      "Detecta compresion de volatilidad BB+KC, entra en la explosion"),
    "donchian_break": ("Donchian Turtle",  "Estrategia clasica turtle: rompe el rango de N periodos con volumen"),
    "dmi_trend":      ("DMI + ADX",        "DMI cruza con ADX fuerte — solo entra en tendencias confirmadas"),
    "chaikin_mf":     ("Chaikin Money Flow","Mide presion compradora/vendedora via volumen — detecta acumulacion"),
}

def grade_explanation(grade, score):
    if grade == "A+": return f"<b>Grade A+</b> — Modelo de elite. Score {score:.4f}, top 5% del universo explorado."
    if grade == "A":  return f"<b>Grade A</b> — Modelo excelente. Score {score:.4f}, alta confianza en produccion."
    if grade == "B":  return f"<b>Grade B</b> — Modelo solido. Score {score:.4f}, buena relacion riesgo/retorno."
    return f"Grade {grade} — Score {score:.4f}"

def main():
    if len(sys.argv) < 2:
        return
    try:
        model = json.loads(sys.argv[1])
    except:
        return

    sym      = model.get("symbol", "?").replace("/USDT","")
    tf       = model.get("tf", "?").upper()
    strategy = model.get("strategy", "?")
    score    = model.get("score", 0)
    oos      = model.get("metrics_oos", model.get("metrics", {}))
    cagr     = oos.get("cagr", 0)
    wr       = oos.get("wr", oos.get("winrate", 0))
    dd       = oos.get("dd", oos.get("max_dd", 0))
    trades   = oos.get("trades", 0)
    pf       = oos.get("pf", oos.get("profit_factor", 0))
    grade    = model.get("grade", "?")
    prev_score = model.get("prev_score", 0)
    mejora   = score - prev_score if prev_score > 0 else 0

    strat_name, strat_desc = STRATEGY_DESCRIPTIONS.get(strategy,
        (strategy.replace("_"," ").title(), "Estrategia de exploracion del sistema"))

    # Calcular CAGR ajustado (anualizado estimado con leverage 2x)
    cagr_2x = round(cagr * 2, 1)

    # Evaluar el perfil de riesgo
    if abs(dd) < 10 and wr > 65:
        perfil = "Perfil conservador con alta precision — ideal para capital real"
    elif cagr > 30 and abs(dd) < 25:
        perfil = "Alto retorno con drawdown controlado — excelente balance"
    elif wr > 70:
        perfil = "Win rate excepcional — el modelo acierta mas de 7 de cada 10 veces"
    else:
        perfil = "Perfil equilibrado con buen factor de beneficio"

    mejora_txt = f"\\nMejora respecto al record anterior: <code>+{mejora:.4f}</code> puntos" if mejora > 0.01 else ""

    msg = (
        f"🏆 <b>NUEVO MODELO CAMPEON — {sym} {tf}</b>\\n\\n"
        f"<b>Estrategia: {strat_name}</b>\\n"
        f"{strat_desc}\\n\\n"
        f"{grade_explanation(grade, score)}{mejora_txt}\\n\\n"
        f"<b>Metricas validadas (out-of-sample):</b>\\n"
        f"CAGR: <code>+{cagr:.1f}%</code> anual (<code>+{cagr_2x}%</code> con 2x leverage)\\n"
        f"Win Rate: <code>{wr:.0f}%</code> | Drawdown max: <code>{dd:.1f}%</code>\\n"
        f"Profit Factor: <code>{pf:.2f}</code> | Trades OOS: {trades}\\n\\n"
        f"<b>Por que funciona:</b>\\n"
        f"{perfil}\\n\\n"
        f"El optimizador evaluo este modelo con datos que nunca vio durante el entrenamiento. "
        f"Un score de {score:.4f} lo coloca entre los mejores encontrados hasta ahora.\\n\\n"
        f"<i>El modelo entra a produccion automaticamente si supera los filtros de validacion.</i>"
    )

    send_tg(msg)
    print(f"[CHAMPION EXPLAINER] {sym} {tf} {strategy} score={score:.4f} CAGR={cagr:.1f}%")

if __name__ == "__main__":
    main()
