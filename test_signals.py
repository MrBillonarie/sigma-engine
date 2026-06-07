import sys, traceback
sys.path.insert(0, '/opt/sigma')
import os; os.chdir('/opt/sigma')
exec(open('/opt/sigma/web_server.py').read())
try:
    r = _compute_signals()
    print("OK models:", len(r.get("models",[])), "regime:", r.get("regime"))
except Exception as e:
    traceback.print_exc()
