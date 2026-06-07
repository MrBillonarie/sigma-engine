#!/usr/bin/env python3
"""
Monitor de milestones de la comunidad CODIGO SIGMA.
Cada vez que el numero de miembros cruza un multiplo de 10, dispara
un mensaje motivacional en multiples canales:

- Telegram (canal principal)
- Discord (#sigma-broadcasts)
- Genera versiones copy-paste para WhatsApp, X, LinkedIn (posteadas a staff)

Estado persistido en /opt/sigma/results/reports/tg_milestone.txt
Cron cada 10 min.
"""
# --- SIGMA secrets loader (audit 2026-05-13) ---
import sys as _sigma_sys
if "/opt/sigma" not in _sigma_sys.path:
    _sigma_sys.path.insert(0, "/opt/sigma")
from utils.secrets import get_tg_token as _sigma_get_tg_token
# --- end SIGMA secrets loader ---

import urllib.request, urllib.parse, json, sys, os
from pathlib import Path
from datetime import datetime

TOKEN      = _sigma_get_tg_token()
CHAT_ID    = '-1003787411069'
INVITE_URL = 'https://t.me/+nHIxMsXbvlQ0ZGQx'
STATE_FILE = Path('/opt/sigma/results/reports/tg_milestone.txt')
LOG_FILE   = Path('/opt/sigma/results/reports/tg_milestone.log')

# Canales para los copy-paste
LINKS = {
    "telegram":  "https://t.me/+1rWWJUMG9-s4NzZh",
    "whatsapp":  "https://chat.whatsapp.com/KOd3poCfJan7bbr26uQ1qk",
    "discord":   "https://discord.gg/HUqQ7wUGsj",
    "linkedin":  "https://www.linkedin.com/in/sigma-quant-desk-02b620403/",
    "x":         "https://x.com/SQuantDesk",
}

ALL_CHANNELS_HTML = (
    "<b>📡 Los 5 canales oficiales SIGMA:</b>\n"
    f"📱 Telegram   {LINKS['telegram']}\n"
    f"💬 WhatsApp   {LINKS['whatsapp']}\n"
    f"🎮 Discord    {LINKS['discord']}\n"
    f"💼 LinkedIn   {LINKS['linkedin']}\n"
    f"🐦 X/Twitter  {LINKS['x']}"
)


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}\n'
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line)


def get_member_count():
    url = f'https://api.telegram.org/bot{TOKEN}/getChatMemberCount?chat_id={CHAT_ID}'
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    if not data.get('ok'):
        raise RuntimeError(f'Telegram API error: {data}')
    return int(data['result'])


def send_telegram(text):
    url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
    payload = urllib.parse.urlencode({
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'false',
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def read_last_milestone():
    if not STATE_FILE.exists():
        return 0
    try:
        return int(STATE_FILE.read_text().strip())
    except Exception:
        return 0


def write_last_milestone(n):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str(n))


# ============================================================================
# MENSAJES MOTIVACIONALES VARIADOS POR MILESTONE
# ============================================================================

def build_telegram_message(milestone):
    """Mensaje principal para el grupo Telegram. HTML format."""
    next_milestone = milestone + 10

    # Mensaje base si no hay tier especifico
    base = (
        f"🚀 <b>SOMOS {milestone} EN CODIGO SIGMA</b>\n\n"
        f"El motor corre 24/7. Optimiza con miles de simulaciones. "
        f"Publica los champions validados acá, en tiempo real.\n\n"
        f"Cada uno tiene acceso a algo que un fondo cuant cobraría.\n\n"
        f"<b>Para llegar a {next_milestone}:</b>\n"
        f"Compartan con quien quiera operar con datos, no con hype.\n\n"
        f"{ALL_CHANNELS_HTML}\n\n"
        f"<i>Reenvíen, mencionen, inviten. Cuántos más seamos, más grande es el experimento.</i>"
    )

    # Tier 10
    if milestone == 10:
        return (
            f"🌱 <b>SOMOS LOS PRIMEROS 10</b>\n\n"
            f"Esto recién arranca. Están acá cuando todavía no es obvio.\n"
            f"El motor SIGMA está operando paper trading con 121 estrategias "
            f"y los champions empiezan a estabilizarse.\n\n"
            f"<b>Lo que sigue:</b>\n"
            f"• Cada champion nuevo se anuncia acá\n"
            f"• Hitos del backtest, alertas de mercado, briefs diarios\n"
            f"• Acceso a comandos del bot (/trades /portfolio /modelos)\n\n"
            f"<b>Meta: 20.</b> Inviten a un trader serio. Cero hype, mucho proceso.\n\n"
            f"{ALL_CHANNELS_HTML}"
        )
    # Tier 20-30 — etapa de captacion
    if milestone in (20, 30):
        return (
            f"🔥 <b>SOMOS {milestone} EN CODIGO SIGMA</b>\n\n"
            f"Crecemos. Sin paid ads, sin shitcoin promo. Solo gente que valora el rigor.\n\n"
            f"El motor SIGMA ya tiene <b>+3M trades simulados</b> en backtest, "
            f"<b>{milestone} personas</b> viendo el sistema en vivo, "
            f"y un objetivo claro: <b>sacar BTC del exchange</b> con un sistema autónomo.\n\n"
            f"<b>Próximo objetivo: {next_milestone}.</b>\n"
            f"Mándale el link a quien siempre supiste que se mueve con datos:\n\n"
            f"{ALL_CHANNELS_HTML}"
        )
    # Tier 40-50 — consolidacion
    if milestone in (40, 50):
        return (
            f"⚡ <b>{milestone} EN CODIGO SIGMA</b>\n\n"
            f"Este grupo pasó de ser un experimento a una <b>mesa de quants real</b>.\n\n"
            f"Lo que conseguimos juntos:\n"
            f"• 121 estrategias activas en el motor\n"
            f"• 16 champions validados con Monte Carlo + walk-forward\n"
            f"• Bot operativo en Telegram + Discord con datos en vivo\n"
            f"• Más de 3 millones de trades en backtest histórico\n\n"
            f"<b>Hacia {next_milestone}:</b> cada miembro nuevo sube la calidad del debate. "
            f"Compartan con su lista de WhatsApp, su círculo cercano de trading, sus colegas. "
            f"El mejor filtro es la recomendación personal.\n\n"
            f"{ALL_CHANNELS_HTML}"
        )
    # Tier 100 — primera marca grande
    if milestone == 100:
        return (
            f"💯 <b>100 EN CODIGO SIGMA</b>\n\n"
            f"Tres dígitos. Es <b>oficial</b>: la idea funciona.\n\n"
            f"Estamos construyendo algo que en pocos meses va a ser muy obvio: "
            f"un sistema autónomo, abierto en proceso, transparente en métricas, "
            f"que opera sin gurú ni hype.\n\n"
            f"Para los próximos 100: <b>invitemos a UNO más cada uno hoy</b>.\n"
            f"Un trader, un curioso, un analista. La comunidad se duplica este mes.\n\n"
            f"{ALL_CHANNELS_HTML}"
        )
    # Tier 200, 300, 400 — crecimiento acelerado
    if milestone in (200, 300, 400):
        return (
            f"🚀 <b>SOMOS {milestone}</b>\n\n"
            f"Cada vez que cruzamos un múltiplo de 100, el sistema "
            f"se vuelve más exigente: <b>más miradas, más debate, más calidad</b>.\n\n"
            f"El motor SIGMA opera en vivo. Los champions se publican acá. "
            f"Las métricas del backtest se actualizan en tiempo real.\n\n"
            f"Si conoces a alguien que estudie quant, market making, sistemas algorítmicos, "
            f"o simplemente trading con disciplina — <b>este es su lugar</b>.\n\n"
            f"{ALL_CHANNELS_HTML}"
        )
    # Tier 500+ — comunidad establecida
    if milestone >= 500:
        return (
            f"⭐ <b>{milestone} EN SIGMA</b>\n\n"
            f"Lo que empezó como un experimento personal de Satoshi Nakamoto "
            f"hoy es una comunidad de {milestone} traders, quants y curiosos serios.\n\n"
            f"El motor SIGMA opera 24/7. Los champions rotan. Los hitos se cumplen.\n"
            f"Cada miembro acá está cambiando cómo se hace trading retail.\n\n"
            f"Para los próximos {next_milestone}: <b>tráele a un colega que valore el proceso</b>.\n\n"
            f"{ALL_CHANNELS_HTML}"
        )
    return base


def build_discord_embed_data(milestone):
    """Datos del embed para Discord."""
    next_milestone = milestone + 10
    return {
        "title": f"🚀 {milestone} en la comunidad SIGMA",
        "description": (
            f"Cruzamos otro multiplo de 10. **Vamos por {next_milestone}**.\n\n"
            f"El sistema opera 24/7. Los champions se publican. Los hitos se cumplen.\n"
            f"Cada miembro nuevo eleva la calidad del debate."
        ),
        "color": 0xFFD700,  # gold
        "fields": [
            {"name": "Comunidad", "value": f"`{milestone}` miembros", "inline": True},
            {"name": "Proximo objetivo", "value": f"`{next_milestone}`", "inline": True},
            {"name": "Backtest acumulado", "value": "`3.1M+` trades", "inline": True},
            {
                "name": "📡 Canales oficiales SIGMA",
                "value": (
                    f"📱 [Telegram]({LINKS['telegram']})\n"
                    f"💬 [WhatsApp]({LINKS['whatsapp']})\n"
                    f"🎮 [Discord]({LINKS['discord']})\n"
                    f"💼 [LinkedIn]({LINKS['linkedin']})\n"
                    f"🐦 [X/Twitter]({LINKS['x']})"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": "Invita a un trader serio que valore data sobre hype"},
    }


def build_whatsapp_message(milestone):
    """Texto plano para WhatsApp/SMS - copy-paste manual."""
    next_milestone = milestone + 10
    return (
        f"🚀 SOMOS {milestone} EN CODIGO SIGMA\n\n"
        f"Comunidad de trading cuant con sistema autonomo corriendo 24/7.\n"
        f"Champions validados con Monte Carlo + walk-forward. Sin senales pagas. "
        f"Sin gurus. Solo proceso.\n\n"
        f"Vamos por {next_milestone}. Sumate o invita a alguien que valore datos:\n\n"
        f"📱 Telegram: {LINKS['telegram']}\n"
        f"💬 WhatsApp: {LINKS['whatsapp']}\n"
        f"🎮 Discord:  {LINKS['discord']}\n"
        f"💼 LinkedIn: {LINKS['linkedin']}\n"
        f"🐦 X:         {LINKS['x']}"
    )


def build_x_message(milestone):
    """Texto corto para Twitter/X — max 280 chars."""
    next_milestone = milestone + 10
    return (
        f"🚀 Somos {milestone} en SIGMA Quant Desk.\n\n"
        f"Comunidad cuant con motor autonomo: 121 estrategias, 16 champions validados, "
        f"3M+ trades simulados. Cero hype, todo proceso.\n\n"
        f"Vamos por {next_milestone}:\n{LINKS['discord']}"
    )


def build_linkedin_message(milestone):
    """Tono profesional para LinkedIn."""
    next_milestone = milestone + 10
    return (
        f"🚀 SIGMA Quant Desk — {milestone} miembros\n\n"
        f"Lo que comenzo como un experimento personal de investigacion en sistemas "
        f"de trading algoritmico hoy es una comunidad de {milestone} profesionales: "
        f"traders cuantitativos, analistas, desarrolladores e inversores que valoran "
        f"el rigor sobre el hype.\n\n"
        f"El sistema corre 24/7 en infraestructura propia. Optimizacion con Optuna, "
        f"validacion Monte Carlo (1000 sims), walk-forward y cross-asset. "
        f"Acumulamos +3 millones de trades simulados con metricas publicadas en tiempo real.\n\n"
        f"Si te interesa el cruce entre trading, quant y open process — vale la pena "
        f"sumarte. Proximo hito: {next_milestone} miembros.\n\n"
        f"Discord: {LINKS['discord']}\n"
        f"Telegram: {LINKS['telegram']}\n"
        f"X: {LINKS['x']}"
    )


# ============================================================================
# DISCORD POSTING
# ============================================================================

def post_to_discord(milestone):
    """Postea el milestone a Discord usando el bot persistente."""
    try:
        with open('/opt/sigma/config/settings.json') as f:
            cfg = json.load(f)
        dc = cfg.get('discord', {})
        if not dc.get('enabled'):
            return False
        token = dc.get('bot_token')
        broadcasts_id = dc.get('channel_ids', {}).get('broadcasts')
        staff_id = dc.get('channel_ids', {}).get('staff') or broadcasts_id  # fallback
        if not token or not broadcasts_id:
            return False
    except Exception as e:
        log(f'discord config err: {e}')
        return False

    # Use Discord REST API directly (no need to spawn discord.py async loop)
    embed = build_discord_embed_data(milestone)
    payload = {"embeds": [embed]}
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "SigmaBot/1.0",
    }

    # 1) Main broadcast embed
    url = f'https://discord.com/api/v10/channels/{broadcasts_id}/messages'
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=15)
        log(f'discord broadcast posted for milestone {milestone}')
    except Exception as e:
        log(f'discord broadcast err: {e}')

    # 2) Copy-paste pack for staff (text-only, code blocks for easy copy)
    cp_url = f'https://discord.com/api/v10/channels/{staff_id}/messages'
    pack = (
        f"📋 **Copy-paste pack para milestone {milestone}** — "
        f"compartir manualmente en los canales sin API\n\n"
        f"**WhatsApp / SMS:**\n```\n{build_whatsapp_message(milestone)}\n```\n\n"
        f"**X / Twitter (280 chars):**\n```\n{build_x_message(milestone)}\n```\n\n"
        f"**LinkedIn:**\n```\n{build_linkedin_message(milestone)}\n```"
    )
    try:
        # Discord max 2000 chars per message — split if too long
        if len(pack) <= 1900:
            req = urllib.request.Request(
                cp_url,
                data=json.dumps({"content": pack}).encode('utf-8'),
                headers=headers,
            )
            urllib.request.urlopen(req, timeout=15)
        else:
            # Send in 3 separate messages
            for label, msg in [
                ("WhatsApp / SMS",     build_whatsapp_message(milestone)),
                ("X / Twitter",        build_x_message(milestone)),
                ("LinkedIn",           build_linkedin_message(milestone)),
            ]:
                part = f"📋 **{label} — milestone {milestone}**\n```\n{msg}\n```"
                req = urllib.request.Request(
                    cp_url,
                    data=json.dumps({"content": part}).encode('utf-8'),
                    headers=headers,
                )
                urllib.request.urlopen(req, timeout=15)
        log(f'discord copy-paste pack posted for milestone {milestone}')
        return True
    except Exception as e:
        log(f'discord copy-paste err: {e}')
        return False


# ============================================================================
# MAIN
# ============================================================================

def main():
    try:
        count = get_member_count()
    except Exception as e:
        log(f'ERROR get_member_count: {e}')
        sys.exit(1)

    last = read_last_milestone()
    current_milestone = (count // 10) * 10

    log(f'Miembros={count} ultimo_milestone_enviado={last} milestone_actual={current_milestone}')

    if current_milestone > last and current_milestone >= 10:
        # 1) Telegram
        msg = build_telegram_message(current_milestone)
        try:
            resp = send_telegram(msg)
            if resp.get('ok'):
                write_last_milestone(current_milestone)
                log(f'OK telegram: milestone {current_milestone}')
            else:
                log(f'ERROR sendTelegram: {resp}')
                return
        except Exception as e:
            log(f'ERROR telegram: {e}')
            return

        # 2) Discord (mirror + copy-paste pack)
        post_to_discord(current_milestone)
    else:
        log('Sin cambio')


if __name__ == '__main__':
    main()
