"""Local browser UI for manual glyph review.

Runs only on loopback and blocks the decoder until the user saves, skips, or quits.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse
import html
import webbrowser


class _ReviewServer:
    def __init__(self, html_path: Path, gid: int, on_save):
        self.html_path = Path(html_path)
        self.gid = int(gid)
        self.on_save = on_save
        self.event = Event()
        self.result = None
        self.server = None

    def page(self) -> str:
        base = self.html_path.read_text(encoding="utf-8")
        gid = html.escape(str(self.gid))
        form = f'''\n<section class="review-controls">\n  <h2>Enter Unicode Mapping</h2>\n  <p>Target CID/GID: <strong>{gid}</strong></p>\n  <form method="post" action="/save">\n    <input id="unicode" name="unicode" type="text" autocomplete="off" autofocus\n           placeholder="Type the exact Bengali character/string here">\n    <button type="submit">Save Mapping</button>\n  </form>\n  <div class="actions">\n    <form method="post" action="/skip"><button type="submit">Skip</button></form>\n    <form method="post" action="/quit"><button type="submit">Quit Review</button></form>\n  </div>\n  <p class="note">The decoder continues after Save or Skip. Saved mappings are written to learned_glyph_map.json.</p>\n</section>\n<style>\n.review-controls{{margin-top:24px;padding:20px;border:2px solid #333;border-radius:10px;background:#fafafa}}\n.review-controls h2{{margin-top:0}}\n.review-controls input{{font-size:28px;width:min(100%,700px);padding:10px;border:2px solid #777;border-radius:6px}}\n.review-controls button{{font-size:18px;padding:10px 18px;margin:8px 6px 0 0;border:0;border-radius:6px;background:#222;color:white;cursor:pointer}}\n.review-controls .actions{{display:flex;gap:8px}}\n.review-controls .actions form{{display:inline}}\n</style></body>'''
        return base.replace("</body>", form, 1)

    def run(self):
        review = self

        class Handler(BaseHTTPRequestHandler):
            def _response(self, title: str, message: str, close: bool = False):
                extra = "<script>setTimeout(()=>window.close(),800)</script>" if close else "<p><a href='/'>Back to review</a></p>"
                body = f'''<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>\n<body style="font-family:Arial,sans-serif;margin:40px"><h1>{html.escape(title)}</h1><p style="font-size:20px">{html.escape(message)}</p>{extra}</body></html>'''.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if urlparse(self.path).path != "/":
                    self.send_error(404)
                    return
                try:
                    body = review.page().encode("utf-8")
                except Exception as exc:
                    self.send_error(500, f"Review page generation failed: {exc}")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                data = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                if path == "/save":
                    text = data.get("unicode", [""])[0].strip()
                    if not text:
                        self._response("Mapping not saved", "Please enter a Unicode character/string.")
                        return
                    try:
                        review.on_save(text)
                    except Exception as exc:
                        self._response("Save failed", str(exc))
                        return
                    review.result = ("save", text)
                    review.event.set()
                    self._response("Mapping saved", f"CID/GID {review.gid} → {text}", close=True)
                    return
                if path == "/skip":
                    review.result = ("skip", None)
                    review.event.set()
                    self._response("Skipped", f"CID/GID {review.gid} was skipped.", close=True)
                    return
                if path == "/quit":
                    review.result = ("quit", None)
                    review.event.set()
                    self._response("Review stopped", "The decoder will stop the current review.", close=True)
                    return
                self.send_error(404)

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self.server.server_address[1]
        url = f"http://127.0.0.1:{port}/"
        print(f"REVIEW UI: {url}", flush=True)
        try:
            webbrowser.open(url, new=2)
        except Exception as exc:
            print(f"Could not automatically open browser: {exc}", flush=True)
        self.server.timeout = 0.5
        while not self.event.is_set():
            self.server.handle_request()
        self.server.server_close()
        return self.result


def run_web_review(html_path: Path, gid: int, on_save):
    """Open a loopback browser reviewer and return ('save'|'skip'|'quit', text)."""
    return _ReviewServer(Path(html_path), int(gid), on_save).run()
