"""HTTP server + JSON API for the detection map viewer."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .template import load_index_html

_HTML = load_index_html()


def run(server):
    class F(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, data, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                self._send(_HTML.encode("utf-8"), "text/html; charset=utf-8")

            elif path == "/info":
                self._send(json.dumps(server.info()).encode("utf-8"),
                           "application/json")

            elif path == "/views":
                self._send(json.dumps(
                    {"fov": server.fov, "views": server.views}
                ).encode("utf-8"), "application/json")

            elif path == "/latest":
                frames = server.tracks.frames()
                last = frames[-1] if frames else 0
                self._send(json.dumps(
                    {"frame_index": last,
                     "pose": server.cur_pose,
                     "records": server.tracks.for_frame(last)}
                ).encode("utf-8"), "application/json")

            elif path == "/tracks":
                fi = int(qs.get("after", ["-1"])[0])
                if "frame" in qs:
                    fr = int(qs["frame"][0])
                    res = {"frame_index": fr,
                           "records": server.tracks.for_frame(fr),
                           "pose": server.pose_for_frame(fr)}
                elif "after" in qs:
                    res = server.tracks.records_after(fi)
                else:
                    res = {k: server.tracks.for_frame(k)
                           for k in server.tracks.frames()}
                self._send(json.dumps(res).encode("utf-8"),
                           "application/json")

            else:
                self._send(b"not found", "text/plain", 404)

    httpd = ThreadingHTTPServer(("", server.port), F)
    print(f"Serving on http://localhost:{server.port}  "
          f"(live={server.live}, fps={server.fps:.1f})")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
