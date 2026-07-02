"""Cron wrapper para reconcile() — corre cada 5 min via crontab.
Importa SOLO live_executor (nunca web_server — ver feedback_never_import_web_server.md).

min_age_min=10: periodo de gracia OBLIGATORIO en modo cron. Este proceso corre
en paralelo a web_server; sin gracia, si el cron dispara en la ventana de
segundos entre el fill de entrada y la colocacion del SL, cerraria de
emergencia una posicion sana recien abierta (ver docstring de reconcile()).
"""
import sys, os
sys.path.insert(0, '/opt/sigma')
os.chdir('/opt/sigma')

from engine.live.live_executor import reconcile
reconcile(min_age_min=10)
