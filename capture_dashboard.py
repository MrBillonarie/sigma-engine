#!/usr/bin/env python3
"""capture_dashboard.py — captura screenshot del dashboard SIGMA y envia a Telegram.

Reglas obligatorias:
- Acceder via http://localhost:8080 (nunca IP externa)
- Capturar viewport SIN browser chrome (sin barra URL)
- Validacion pre-envio: regex sobre HTML para detectar IP del VPS
- Si falla validacion → log + abort (no enviar)

Uso:
    python capture_dashboard.py [section]

Donde section es opcional:
    matrix      — matriz de champions
    portfolio   — panel portafolio ponderado
    paper       — per-model paper trading
    trades      — posiciones abiertas
    default     — viewport completo
"""
import sys, os, json, re, time, asyncio
from pathlib import Path
import requests

sys.path.insert(0, "/opt/sigma")
try:
    from utils.secrets import get_tg_token
except Exception:
    get_tg_token = None

URL_INTERNAL = "http://localhost:8080"
CHAT_ID = "-1003787411069"

# Patrones que NO deben aparecer en el screenshot enviado
FORBIDDEN_PATTERNS = [
    r"178\.104\.10\.97",
    r"178\.104\.\d+\.\d+",
    r":8080(?!/api/)",  # :8080 standalone, no /api/X
]

LOG_PATH = "/opt/sigma/results/reports/dashboard_screenshots.log"

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def validate_html_no_ip(html_text):
    """Verifica que el HTML NO contenga la IP del VPS. Retorna (ok, motivo)."""
    for pat in FORBIDDEN_PATTERNS:
        matches = re.findall(pat, html_text)
        if matches:
            return False, f"Pattern {pat} matched {len(matches)} times (ejemplo: {matches[0]})"
    return True, "ok"

async def capture(section="default"):
    """Captura screenshot. Retorna path al PNG o None si abort."""
    from playwright.async_api import async_playwright

    output_path = f"/tmp/sigma_dash_{section}.png"

    async with async_playwright() as p:
        # Headless shell — sin browser chrome
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        try:
            ctx = await browser.new_context(
                viewport={"width": 1600, "height": 1000},
                device_scale_factor=1,
            )
            page = await ctx.new_page()

            log(f"Navegando a {URL_INTERNAL}/ ...")
            await page.goto(URL_INTERNAL + "/", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2500)  # dar tiempo a animaciones / lazy renders

            # Validacion pre-screenshot: HTML no debe tener IP externa
            html_now = await page.content()
            ok, motivo = validate_html_no_ip(html_now)
            if not ok:
                log(f"ABORT — HTML contiene IP: {motivo}")
                return None
            log("HTML OK — sin IP visible")

            # Seleccion de region segun section
            if section == "matrix":
                selector = ".matrix, #matrix-section, [data-section=matrix]"
            elif section == "portfolio":
                selector = ".kpi-strip, .portfolio-panel, [data-section=portfolio]"
            elif section == "paper":
                # Para per-model paper trading hay que ir a otra URL
                await page.goto(URL_INTERNAL + "/models", wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                html_now = await page.content()
                ok, motivo = validate_html_no_ip(html_now)
                if not ok:
                    log(f"ABORT /models — IP visible: {motivo}")
                    return None
                selector = None  # viewport completo
            elif section == "trades":
                selector = ".trades-panel, [data-section=trades], #posiciones"
            else:
                selector = None  # viewport completo

            if selector:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.screenshot(path=output_path)
                        log(f"Screenshot region {section} -> {output_path}")
                    else:
                        await page.screenshot(path=output_path, full_page=True)
                        log(f"Region {section} no encontrada, viewport completo -> {output_path}")
                except Exception as e:
                    log(f"err selector {section}: {e}, viewport completo")
                    await page.screenshot(path=output_path, full_page=True)
            else:
                await page.screenshot(path=output_path, full_page=True)
                log(f"Viewport completo -> {output_path}")

            return output_path
        finally:
            await browser.close()

def send_photo(image_path, caption=""):
    """Envia foto al chat de SIGMA Telegram."""
    if not get_tg_token:
        log("ABORT — get_tg_token no disponible")
        return False
    token = get_tg_token()
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(image_path, "rb") as f:
        files = {"photo": f}
        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        r = requests.post(url, files=files, data=data, timeout=60)
    if r.status_code == 200:
        log(f"sent OK -> {image_path}")
        return True
    log(f"sendPhoto error {r.status_code}: {r.text[:300]}")
    return False

def main():
    section = sys.argv[1] if len(sys.argv) > 1 else "default"
    caption = sys.argv[2] if len(sys.argv) > 2 else f"Dashboard SIGMA · {section}"

    log(f"=== start section={section} ===")

    path = asyncio.run(capture(section))
    if not path:
        print("ABORT — captura abortada (ver log)")
        sys.exit(1)

    print(f"Captura OK: {path}")

    # Si pasamos --send, enviar al TG
    if "--send" in sys.argv:
        ok = send_photo(path, caption)
        if ok:
            print("Enviado al TG")
        else:
            print("ERROR enviando al TG")
            sys.exit(2)
    else:
        print("(usar --send para enviar al TG)")

if __name__ == "__main__":
    main()
