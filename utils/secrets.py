import os
from pathlib import Path
def get_tg_token():
    env = os.environ.get("SIGMA_TG_TOKEN")
    if env:
        return env.strip()
    p = Path("/opt/sigma/config/tg_token.txt")
    if p.exists():
        return p.read_text().strip()
    raise RuntimeError("TG_TOKEN no encontrado: define SIGMA_TG_TOKEN o /opt/sigma/config/tg_token.txt")
