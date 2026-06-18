#!/usr/bin/env python3
"""Mini servidor HTTP SIGMA 2FA. Puerto 8089. Sin auto-refresh del formulario."""
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse, os, json

PORT = 8089
CODE_FILE = "/tmp/totp_code.txt"
READY_FILE = "/tmp/totp_ready.txt"

HTML_FORM = """<!DOCTYPE html>
<html>
<head>
  <meta charset=utf-8>
  <meta name=viewport content="width=device-width, initial-scale=1">
  <title>SIGMA 2FA</title>
  <style>
    body { background:#0a0a0a; color:#0f0; font-family:monospace; padding:30px; text-align:center; }
    h2 { font-size:24px; margin-bottom:5px; }
    #status { font-size:18px; margin:15px 0; padding:10px; border-radius:5px; }
    .ready { color:#0f0; border:1px solid #0f0; }
    .waiting { color:#f80; border:1px solid #f80; }
    input[type=text] { font-size:52px; width:260px; text-align:center;
      background:#111; color:#0f0; border:2px solid #0f0; padding:10px;
      letter-spacing:8px; margin:20px auto; display:block; }
    button { font-size:28px; background:#0f0; color:#000; border:none;
      padding:12px 40px; cursor:pointer; border-radius:4px; }
    button:hover { background:#0a0; }
    .hint { color:#555; font-size:12px; margin-top:25px; }
    #sent { color:#0f0; font-size:22px; min-height:30px; margin:10px 0; }
  </style>
</head>
<body>
  <h2>SIGMA ENGINE - IBKR 2FA</h2>
  <div id=status class=waiting>Verificando estado...</div>
  <div id=sent></div>
  <form id=frm method=post>
    <input id=code type=text name=code maxlength=6 autofocus placeholder=123456
           autocomplete=off inputmode=numeric pattern=[0-9]{6}>
    <button type=submit>ENVIAR</button>
  </form>
  <p class=hint>Abre Google Authenticator, copia el codigo de IBKR y presiona ENVIAR.<br>
  El estado se actualiza sin borrar lo que escribes.</p>
  <script>
    function refreshStatus(){
      fetch('/status').then(r=>r.json()).then(d=>{
        var el=document.getElementById('status');
        if(d.ready){
          el.textContent='LISTO - Ingresa el codigo ahora';
          el.className='ready';
        } else {
          el.textContent='Esperando que el gateway inicie...';
          el.className='waiting';
        }
      }).catch(function(){});
    }
    refreshStatus();
    setInterval(refreshStatus, 3000);
    document.getElementById('frm').addEventListener('submit', function(e){
      e.preventDefault();
      var code = document.getElementById('code').value.trim();
      if(!/^[0-9]{6}$/.test(code)){
        document.getElementById('sent').textContent='ERROR: necesita exactamente 6 digitos';
        return;
      }
      fetch('/', {method:'POST', body:'code='+code,
                  headers:{'Content-Type':'application/x-www-form-urlencoded'}})
        .then(r=>r.text()).then(function(t){
          document.getElementById('sent').textContent='Codigo '+code+' enviado! Esperando respuesta del gateway...';
          document.getElementById('code').value='';
          document.getElementById('code').focus();
        }).catch(function(){
          document.getElementById('sent').textContent='ERROR enviando - intenta de nuevo';
        });
    });
  </script>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == '/status':
            ready = os.path.exists(READY_FILE)
            body = json.dumps({"ready": ready}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        body = HTML_FORM.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        params = dict(urllib.parse.parse_qsl(raw))
        code = params.get("code", "").strip()
        if code and code.isdigit() and len(code) == 6:
            open(CODE_FILE, "w").write(code)
            msg = ("OK: codigo " + code + " enviado").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", len(msg))
            self.end_headers()
            self.wfile.write(msg)
        else:
            msg = b"Codigo invalido - debe ser exactamente 6 digitos"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", len(msg))
            self.end_headers()
            self.wfile.write(msg)

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[2FA] Servidor en http://178.104.10.97:{PORT}", flush=True)
    server.serve_forever()
