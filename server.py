#!/usr/bin/env python3
"""
DSS Server entry point.

Boots MediaMTX, attaches TLS if cert.pem + key.pem are present, and serves the
REST API + web dashboard. All feature logic lives in the `dss` package.
"""

import signal
import ssl
import sys

from dss import auth, config, mediamtx
from dss.handler import Handler, DSSHTTPServer


def main():
    def shutdown(sig, frame):
        print("\nShutting down...")
        mediamtx.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  DSS Server")

    auth.load_credentials()
    mediamtx.start()

    server = DSSHTTPServer(("", config.PORT), Handler)

    # HTTPS/TLS support — drop cert.pem + key.pem in project root to enable
    if config.TLS_CERT.exists() and config.TLS_KEY.exists():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(config.TLS_CERT), str(config.TLS_KEY))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        config.use_tls = True
        proto = "https"
    else:
        proto = "http"

    print(f"  Web UI:    {proto}://localhost:{config.PORT}")
    print(f"  Login:     {proto}://localhost:{config.PORT}/login")
    if not config.use_tls:
        print("  TLS:       off (add cert.pem + key.pem for HTTPS)")
    else:
        print("  TLS:       on")
    print("  MediaMTX:  http://localhost:9997")
    print(f"  Sessions:  expire after {config.SESSION_TTL // 3600}h")
    print("  Press Ctrl+C to stop")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    server.serve_forever()


if __name__ == "__main__":
    main()
