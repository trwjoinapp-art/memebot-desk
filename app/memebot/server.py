import json, os, http.server, socketserver, time, threading
import desk_export as export2
DIR = os.path.dirname(os.path.abspath(__file__))
_cache = {"t": 0, "body": b"{}"}
_lock = threading.Lock()
def state():
    with _lock:
        if time.time() - _cache["t"] > 2:
            try:
                _cache["body"] = json.dumps(export2.build(), separators=(",", ":")).encode(); _cache["t"] = time.time()
            except Exception as e:
                print("build error", e)
        return _cache["body"]
class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._send(200, open(os.path.join(DIR, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif p == "/api/state":
            self._send(200, state(), "application/json")
        elif p == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")
    def log_message(self, *a): pass
class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True; allow_reuse_address = True
if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1"); port = int(os.environ.get("PORT", "8787"))
    print("serving dashboard on", host, port, flush=True)
    S((host, port), H).serve_forever()
