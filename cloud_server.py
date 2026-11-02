"""Public download and status; generation runs independently in one child process."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT/'public'


def load(name):
    try:
        return json.loads((PUBLIC/name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def snapshot():
    generated = load('estado.json')
    age = None
    if generated.get('generated_at_utc'):
        age = (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(generated['generated_at_utc'])).total_seconds()
    return {'service': 'automaton-pine-cloud', 'generator_deployed': True,
            'commit': os.environ.get('RENDER_GIT_COMMIT', 'local'),
            'worker': load('worker.json'), 'last_generation': generated,
            'age_seconds': round(age) if age is not None else None,
            'stale': age is None or age > 900,
            'pc_required': False, 'target_interval_seconds': 60,
            'note': 'Free hosting may sleep; generation time and source restrictions affect updates.'}


HTML = '''<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AUTOMATON Pine</title><style>body{font:18px system-ui;max-width:720px;margin:40px auto;padding:20px;background:#111827;color:#eee}a{color:#67e8f9}pre{white-space:pre-wrap;font-size:14px}</style>
<h1>AUTOMATON Pine</h1><p id="message">Consultando estado…</p>
<p><a id="download" href="/AUTOMATON_ACTUAL.pine" hidden>Descargar Pine actualizado</a></p>
<p>La generación se ejecuta en el servidor. Para aplicarlo en TradingView, pegá el código nuevo en el editor Pine.</p>
<p>El alojamiento gratuito puede suspenderse por inactividad. Cada cálculo puede tardar más de un minuto. No incluye posiciones ni bases históricas privadas del motor local.</p>
<pre id="detail"></pre><script>
async function update(){try{const s=await (await fetch('/status',{cache:'no-store'})).json();
document.getElementById('message').textContent=s.stale?'Todavía no hay un Pine reciente disponible. Estado: '+(s.worker.state||'iniciando'):'Última generación: '+new Date(s.last_generation.generated_at_utc).toLocaleString();
document.getElementById('download').hidden=s.stale;
document.getElementById('detail').textContent=JSON.stringify(s,null,2);
}catch(e){document.getElementById('message').textContent='No se pudo consultar el servidor.'}}
update();setInterval(update,60000);</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, kind='application/json', download=False):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="AUTOMATON_ACTUAL.pine"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split('?')[0]
        if route == '/':
            return self.send(200, HTML.encode(), 'text/html; charset=utf-8')
        if route == '/healthz':
            return self.send(200, b'{"http_server":"ready"}')
        state = snapshot()
        if route in ('/status', '/estado.json'):
            return self.send(200, json.dumps(state).encode())
        if route in ('/AUTOMATON_ACTUAL.pine', '/api/tv_pine_full'):
            try:
                body = (PUBLIC/'AUTOMATON_ACTUAL.pine').read_bytes()
                valid = not state['stale'] and hashlib.sha256(body).hexdigest() == state['last_generation'].get('sha256')
            except OSError:
                valid = False
            if not valid:
                return self.send(503, json.dumps({'error': 'No hay una generacion reciente validada.', 'status': state}).encode())
            return self.send(200, body, 'text/plain; charset=utf-8', True)
        self.send_error(404)


def supervise():
    while True:
        process = subprocess.Popen([sys.executable, '-u', str(ROOT/'cloud_worker.py')], cwd=ROOT)
        while process.poll() is None:
            status = load('worker.json')
            if status.get('state') == 'generating':
                age = time.time()-dt.datetime.fromisoformat(status['updated_at_utc']).timestamp()
                if age > 1800:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
            time.sleep(15)
        time.sleep(60)


if __name__ == '__main__':
    threading.Thread(target=supervise, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), Handler).serve_forever()
