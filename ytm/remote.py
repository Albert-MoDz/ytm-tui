"""Control remoto web: una página para el celular (y una API JSON) que maneja el reproductor."""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("ytm")

PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>YTM-TUI remoto</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;font:16px system-ui,sans-serif;background:#111;color:#eee;padding:12px;max-width:560px;margin:auto}
h1{font-size:15px;color:#888;font-weight:500;margin:4px 0 12px}.now{background:#1c1c1c;border-radius:14px;padding:14px}
#title{font-size:19px;font-weight:600}#artist{color:#aaa;margin-top:2px}input[type=range]{width:100%;accent-color:#f33;margin:10px 0}
.row{display:flex;gap:8px;justify-content:center;align-items:center;flex-wrap:wrap;margin:6px 0}
button{background:#2a2a2a;color:#eee;border:0;border-radius:10px;padding:12px 16px;font-size:18px;cursor:pointer}button:active{background:#f33}
button.on{background:#7a1f1f}.big{font-size:24px;padding:12px 22px}.t{display:flex;justify-content:space-between;color:#999;font-size:13px}
.q{margin-top:14px}.item{display:flex;align-items:center;gap:8px;padding:9px 6px;border-bottom:1px solid #222}.item .m{flex:1;min-width:0}
.item .m div:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.item small{color:#888}.item button{padding:6px 10px;font-size:14px}
.cur{color:#f55}#q{width:100%;padding:12px;border-radius:10px;border:0;background:#222;color:#eee;font-size:16px}h2{font-size:14px;color:#888;margin:16px 0 4px}
</style></head><body><h1>YTM-TUI · control remoto</h1>
<div class="now"><div id="title">—</div><div id="artist"></div>
<input type="range" id="seek" min="0" max="1000" value="0"><div class="t"><span id="pos">00:00</span><span id="dur">00:00</span></div>
<div class="row"><button onclick="c('prev')">⏮</button><button onclick="c('seek',{delta:-10})">⏪</button><button class="big" id="pp" onclick="c('toggle_pause')">▶</button>
<button onclick="c('seek',{delta:10})">⏩</button><button onclick="c('next')">⏭</button></div>
<div class="row"><button id="sh" onclick="c('shuffle')">⇄</button><button id="rp" onclick="c('repeat')">↻</button>
<button onclick="c('volume',{delta:-5})">♪−</button><span id="vol">80%</span><button onclick="c('volume',{delta:5})">♪+</button><button onclick="c('radio')">◉ Radio</button></div></div>
<h2>Buscar</h2><input id="q" placeholder="Buscar canciones…" enterkeyhint="search"><div id="res"></div>
<h2>Cola</h2><div id="queue" class="q"></div>
<script>
const T=new URLSearchParams(location.search).get('token')||'';let drag=false,last={};
const f=s=>{s=Math.max(0,s|0);return String(s/60|0).padStart(2,'0')+':'+String(s%60).padStart(2,'0')};
async function api(p,o={}){o.headers=Object.assign({'X-Token':T,'Content-Type':'application/json'},o.headers||{});const r=await fetch(p,o);if(!r.ok)throw new Error(r.status);return r.json()}
async function c(cmd,a={}){await api('/api/cmd',{method:'POST',body:JSON.stringify(Object.assign({cmd},a))});poll()}
function row(t,btns){const d=document.createElement('div');d.className='item';const m=document.createElement('div');m.className='m';
const a=document.createElement('div');a.textContent=t.title;const b=document.createElement('small');b.textContent=t.artist||'';m.append(a,b);d.append(m);btns.forEach(x=>d.append(x));return[d,a]}
function btn(l,fn){const b=document.createElement('button');b.textContent=l;b.onclick=fn;return b}
async function poll(){try{const s=await api('/api/state');last=s;document.getElementById('title').textContent=s.now?s.now.title:'Nada en reproducción';
document.getElementById('artist').textContent=s.now?s.now.artist:'';document.getElementById('pos').textContent=f(s.pos);document.getElementById('dur').textContent=f(s.dur);
if(!drag)document.getElementById('seek').value=s.dur?Math.round(s.pos/s.dur*1000):0;document.getElementById('pp').textContent=s.state==='playing'?'⏸':'▶';
document.getElementById('vol').textContent=s.volume+'%';document.getElementById('sh').className=s.shuffle?'on':'';document.getElementById('rp').className=s.repeat!=='off'?'on':'';
document.getElementById('rp').textContent=s.repeat==='one'?'↻¹':'↻';const q=document.getElementById('queue');q.replaceChildren();
s.queue.forEach((t,i)=>{const[d,a]=row(t,[btn('▶',()=>c('jump',{index:i}))]);if(i===s.index)a.className='cur';q.append(d)})}catch(e){}}
const sk=document.getElementById('seek');sk.oninput=()=>drag=true;sk.onchange=()=>{drag=false;c('seek_to',{seconds:sk.value/1000*(last.dur||0)})};
const qi=document.getElementById('q');qi.onkeydown=async e=>{if(e.key!=='Enter')return;const r=document.getElementById('res');r.textContent='Buscando…';
try{const j=await api('/api/search?q='+encodeURIComponent(qi.value));r.replaceChildren();j.forEach(t=>{const[d]=row(t,[btn('▶',()=>c('play',{track:t})),btn('✚',()=>c('enqueue',{track:t}))]);r.append(d)});if(!j.length)r.textContent='Sin resultados'}catch(x){r.textContent='Error'}};
poll();setInterval(poll,1000);
</script></body></html>"""


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class RemoteServer:
    def __init__(self, app, port: int, token: str, host: str = "0.0.0.0"):
        self.app, self.port, self.token, self.host = app, port, token or secrets.token_urlsafe(8), host
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{local_ip()}:{self.port}/?token={self.token}"

    def start(self) -> None:
        app, token = self.app, self.token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencio: no ensuciar la terminal
                pass

            def _authorized(self, query) -> bool:
                given = self.headers.get("X-Token") or (query.get("token") or [""])[0]
                return hmac.compare_digest(given.encode(), token.encode())

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode())

            def do_GET(self):  # noqa: N802
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if u.path == "/":
                    if not self._authorized(q):
                        return self._send(401, "Falta el token. Usa la URL que muestra la app.".encode(), "text/plain")
                    return self._send(200, PAGE.encode(), "text/html")
                if not self._authorized(q):
                    return self._json(401, {"error": "token"})
                try:
                    if u.path == "/api/state":
                        return self._json(200, app.remote_state())
                    if u.path == "/api/search":
                        return self._json(200, app.remote_search((q.get("q") or [""])[0]))
                except Exception as e:  # noqa: BLE001
                    log.exception("remote GET")
                    return self._json(500, {"error": str(e)})
                self._json(404, {"error": "no existe"})

            def do_POST(self):  # noqa: N802
                u = urlparse(self.path)
                if not self._authorized(parse_qs(u.query)):
                    return self._json(401, {"error": "token"})
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    cmd = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                    if u.path == "/api/cmd":
                        return self._json(200, app.run_in_ui(app.remote_command, cmd))
                except Exception as e:  # noqa: BLE001
                    log.exception("remote POST")
                    return self._json(500, {"error": str(e)})
                self._json(404, {"error": "no existe"})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
