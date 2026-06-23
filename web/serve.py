"""Static file server for the DSS frontend with caching disabled.

Plain `python -m http.server` sends no Cache-Control, so browsers heuristically
cache the ES modules and keep running stale JS after an update (this caused
operators to see old behaviour, e.g. an outdated WHEP timeout). Sending
`no-store` forces every client to fetch the current files on each load.

Usage:  python serve.py [port]   (default 8081, binds 0.0.0.0)
"""
import http.server
import socketserver
import sys


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), NoCacheHandler) as httpd:
        print(f"DSS frontend (no-cache) on 0.0.0.0:{port}")
        httpd.serve_forever()
